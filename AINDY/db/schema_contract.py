"""Runtime-owned database schema bootstrap and validation helpers."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import inspect
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from AINDY.db.database import Base

# Import runtime-owned models so Base.registry contains the full runtime contract
# even when no app profile has been loaded.
import AINDY.db.model_registry  # noqa: F401
import AINDY.memory.memory_persistence  # noqa: F401

_RUNTIME_MODEL_PREFIXES = ("AINDY.db.models.",)
_RUNTIME_MODEL_MODULES = {"AINDY.memory.memory_persistence"}


@dataclass(frozen=True)
class SchemaIssue:
    code: str
    detail: str
    table: str | None = None
    column: str | None = None


@dataclass(frozen=True)
class SchemaReport:
    ok: bool
    bootstrapped: bool
    issues: tuple[SchemaIssue, ...]

    def summary(self) -> str:
        if self.ok:
            if self.bootstrapped:
                return "Runtime-owned schema bootstrapped from packaged metadata."
            return "Runtime-owned schema matches packaged metadata."
        return "; ".join(issue.detail for issue in self.issues)


def _normalize_type_name(type_, *, dialect=None) -> str:
    if dialect is not None:
        try:
            compiled = str(type_.compile(dialect=dialect))
            return compiled.lower().split("(", 1)[0].strip()
        except Exception:
            pass
    visit_name = getattr(type_, "__visit_name__", None)
    if visit_name:
        return str(visit_name).lower()
    return type(type_).__name__.lower()


def _runtime_owned_tables():
    tables_by_name = {}
    for mapper in Base.registry.mappers:
        module_name = mapper.class_.__module__
        if not (
            module_name.startswith(_RUNTIME_MODEL_PREFIXES)
            or module_name in _RUNTIME_MODEL_MODULES
        ):
            continue
        table = mapper.local_table
        tables_by_name[table.name] = table
    return [tables_by_name[name] for name in sorted(tables_by_name)]


def runtime_owned_table_names() -> tuple[str, ...]:
    return tuple(table.name for table in _runtime_owned_tables())


def _resolve_bind(bind: Engine | Connection | Session):
    if isinstance(bind, Session):
        return bind.get_bind()
    return bind


def validate_runtime_schema(bind: Engine | Connection | Session) -> SchemaReport:
    resolved = _resolve_bind(bind)
    inspector = inspect(resolved)
    issues: list[SchemaIssue] = []

    for table in _runtime_owned_tables():
        table_name = table.name
        if not inspector.has_table(table_name):
            issues.append(
                SchemaIssue(
                    code="missing_table",
                    table=table_name,
                    detail=f"Missing required runtime table {table_name!r}.",
                )
            )
            continue

        actual_columns = {
            column["name"]: column for column in inspector.get_columns(table_name)
        }
        for expected_column in table.columns:
            actual_column = actual_columns.get(expected_column.name)
            if actual_column is None:
                issues.append(
                    SchemaIssue(
                        code="missing_column",
                        table=table_name,
                        column=expected_column.name,
                        detail=(
                            f"Runtime table {table_name!r} is missing required column "
                            f"{expected_column.name!r}."
                        ),
                    )
                )
                continue

            expected_type = _normalize_type_name(
                expected_column.type,
                dialect=resolved.dialect,
            )
            actual_type = _normalize_type_name(actual_column["type"])
            if expected_type != actual_type:
                issues.append(
                    SchemaIssue(
                        code="column_type_mismatch",
                        table=table_name,
                        column=expected_column.name,
                        detail=(
                            f"Runtime table {table_name!r} column {expected_column.name!r} "
                            f"has type {actual_type!r}; expected {expected_type!r}."
                        ),
                    )
                )

            actual_nullable = bool(actual_column.get("nullable", True))
            if actual_nullable != bool(expected_column.nullable):
                issues.append(
                    SchemaIssue(
                        code="column_nullability_mismatch",
                        table=table_name,
                        column=expected_column.name,
                        detail=(
                            f"Runtime table {table_name!r} column {expected_column.name!r} "
                            f"nullable={actual_nullable!r}; expected "
                            f"{bool(expected_column.nullable)!r}."
                        ),
                    )
                )

            actual_primary_key = bool(actual_column.get("primary_key", False))
            if actual_primary_key != bool(expected_column.primary_key):
                issues.append(
                    SchemaIssue(
                        code="column_primary_key_mismatch",
                        table=table_name,
                        column=expected_column.name,
                        detail=(
                            f"Runtime table {table_name!r} column {expected_column.name!r} "
                            f"primary_key={actual_primary_key!r}; expected "
                            f"{bool(expected_column.primary_key)!r}."
                        ),
                    )
                )

    return SchemaReport(ok=not issues, bootstrapped=False, issues=tuple(issues))


def ensure_runtime_schema(
    bind: Engine | Connection | Session,
    *,
    allow_bootstrap: bool = True,
) -> SchemaReport:
    resolved = _resolve_bind(bind)
    inspector = inspect(resolved)
    expected_tables = runtime_owned_table_names()
    existing_runtime_tables = {
        table_name for table_name in expected_tables if inspector.has_table(table_name)
    }
    bootstrapped = False

    if allow_bootstrap and not existing_runtime_tables:
        Base.metadata.create_all(bind=resolved, tables=_runtime_owned_tables(), checkfirst=True)
        bootstrapped = True

    report = validate_runtime_schema(resolved)
    if report.ok:
        return SchemaReport(ok=True, bootstrapped=bootstrapped, issues=report.issues)
    return SchemaReport(ok=False, bootstrapped=bootstrapped, issues=report.issues)
