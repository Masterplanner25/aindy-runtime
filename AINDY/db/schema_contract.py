"""Runtime-owned database schema bootstrap, validation, and reconciliation."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateColumn

from AINDY.db.database import Base

# Import runtime-owned models so Base.registry contains the full runtime contract
# even when no app profile has been loaded.
import AINDY.db.model_registry  # noqa: F401
import AINDY.memory.memory_persistence  # noqa: F401

_RUNTIME_MODEL_PREFIXES = ("AINDY.db.models.",)
_RUNTIME_MODEL_MODULES = {"AINDY.memory.memory_persistence"}

SCHEMA_STATE_BLANK_DATABASE = "blank_database"
SCHEMA_STATE_BLANK_BOOTSTRAP = "blank_bootstrap"
SCHEMA_STATE_COMPATIBLE = "compatible"
SCHEMA_STATE_UPGRADE_REQUIRED = "upgrade_required"
SCHEMA_STATE_INCOMPATIBLE_MANUAL = "incompatible_manual"

_SAFE_RECONCILE_CODES = {"missing_table", "missing_column"}


@dataclass(frozen=True)
class SchemaIssue:
    code: str
    detail: str
    table: str | None = None
    column: str | None = None
    reconcile_supported: bool = False


@dataclass(frozen=True)
class SchemaReport:
    ok: bool
    bootstrapped: bool
    reconciled: bool
    state: str
    reconcile_supported: bool
    operator_action: str
    issues: tuple[SchemaIssue, ...]

    def summary(self) -> str:
        if self.state == SCHEMA_STATE_BLANK_BOOTSTRAP:
            return "Runtime-owned schema bootstrapped from packaged metadata."
        if self.state == SCHEMA_STATE_COMPATIBLE:
            if self.reconciled:
                return "Runtime-owned schema reconciled and now matches packaged metadata."
            return "Runtime-owned schema matches packaged metadata."
        if self.state == SCHEMA_STATE_BLANK_DATABASE:
            return "Runtime-owned schema is absent and requires blank-database bootstrap."
        if self.state == SCHEMA_STATE_UPGRADE_REQUIRED:
            return (
                "Runtime-owned schema requires an explicit additive reconcile: "
                + "; ".join(issue.detail for issue in self.issues)
            )
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


def _inspect_schema_issues(
    resolved: Engine | Connection,
) -> tuple[tuple[str, ...], tuple[SchemaIssue, ...]]:
    inspector = inspect(resolved)
    expected_tables = runtime_owned_table_names()
    existing_runtime_tables = tuple(
        table_name for table_name in expected_tables if inspector.has_table(table_name)
    )
    issues: list[SchemaIssue] = []

    for table in _runtime_owned_tables():
        table_name = table.name
        if not inspector.has_table(table_name):
            issues.append(
                SchemaIssue(
                    code="missing_table",
                    table=table_name,
                    detail=f"Missing required runtime table {table_name!r}.",
                    reconcile_supported=True,
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
                        reconcile_supported=_column_reconcile_supported(expected_column),
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

    return existing_runtime_tables, tuple(issues)


def _column_reconcile_supported(column) -> bool:
    if bool(column.primary_key):
        return False
    if getattr(column, "foreign_keys", None):
        return False
    if column.server_default is not None:
        return True
    return bool(column.nullable)


def _classify_schema_state(
    existing_runtime_tables: tuple[str, ...],
    issues: tuple[SchemaIssue, ...],
) -> tuple[str, bool, str]:
    if not existing_runtime_tables:
        return (
            SCHEMA_STATE_BLANK_DATABASE,
            True,
            "bootstrap",
        )
    if not issues:
        return (
            SCHEMA_STATE_COMPATIBLE,
            False,
            "none",
        )
    reconcile_supported = all(
        issue.code in _SAFE_RECONCILE_CODES and issue.reconcile_supported
        for issue in issues
    )
    if reconcile_supported:
        return (
            SCHEMA_STATE_UPGRADE_REQUIRED,
            True,
            "explicit_reconcile",
        )
    return (
        SCHEMA_STATE_INCOMPATIBLE_MANUAL,
        False,
        "manual_intervention",
    )


def inspect_runtime_schema(bind: Engine | Connection | Session) -> SchemaReport:
    resolved = _resolve_bind(bind)
    existing_runtime_tables, issues = _inspect_schema_issues(resolved)
    state, reconcile_supported, operator_action = _classify_schema_state(
        existing_runtime_tables,
        issues,
    )
    return SchemaReport(
        ok=state == SCHEMA_STATE_COMPATIBLE,
        bootstrapped=False,
        reconciled=False,
        state=state,
        reconcile_supported=reconcile_supported,
        operator_action=operator_action,
        issues=issues,
    )


def validate_runtime_schema(bind: Engine | Connection | Session) -> SchemaReport:
    return inspect_runtime_schema(bind)


def _execute_ddl(bind: Engine | Connection, sql: str) -> None:
    if isinstance(bind, Engine):
        with bind.begin() as conn:
            conn.execute(text(sql))
        return
    bind.execute(text(sql))


def _render_add_column_sql(bind: Engine | Connection, table, column) -> str:
    compiled = str(CreateColumn(column).compile(dialect=bind.dialect)).strip()
    table_name = bind.dialect.identifier_preparer.format_table(table)
    return f"ALTER TABLE {table_name} ADD COLUMN {compiled}"


def reconcile_runtime_schema(bind: Engine | Connection | Session) -> SchemaReport:
    resolved = _resolve_bind(bind)
    report = inspect_runtime_schema(resolved)

    if report.state == SCHEMA_STATE_BLANK_DATABASE:
        Base.metadata.create_all(bind=resolved, tables=_runtime_owned_tables(), checkfirst=True)
        validated = inspect_runtime_schema(resolved)
        return SchemaReport(
            ok=validated.ok,
            bootstrapped=validated.ok,
            reconciled=False,
            state=SCHEMA_STATE_BLANK_BOOTSTRAP if validated.ok else validated.state,
            reconcile_supported=validated.reconcile_supported,
            operator_action=validated.operator_action,
            issues=validated.issues,
        )

    if report.state != SCHEMA_STATE_UPGRADE_REQUIRED:
        return report

    for table in _runtime_owned_tables():
        if not inspect(resolved).has_table(table.name):
            table.create(bind=resolved, checkfirst=True)
            continue

        actual_columns = {
            column["name"]: column for column in inspect(resolved).get_columns(table.name)
        }
        for expected_column in table.columns:
            if expected_column.name in actual_columns:
                continue
            if not _column_reconcile_supported(expected_column):
                continue
            sql = _render_add_column_sql(resolved, table, expected_column)
            _execute_ddl(resolved, sql)

    validated = inspect_runtime_schema(resolved)
    return SchemaReport(
        ok=validated.ok,
        bootstrapped=False,
        reconciled=validated.ok,
        state=validated.state,
        reconcile_supported=validated.reconcile_supported,
        operator_action=validated.operator_action,
        issues=validated.issues,
    )


def ensure_runtime_schema(
    bind: Engine | Connection | Session,
    *,
    allow_bootstrap: bool = True,
    allow_reconcile: bool = False,
) -> SchemaReport:
    report = inspect_runtime_schema(bind)

    if report.state == SCHEMA_STATE_BLANK_DATABASE:
        if allow_bootstrap:
            return reconcile_runtime_schema(bind)
        return report

    if report.state == SCHEMA_STATE_UPGRADE_REQUIRED and allow_reconcile:
        return reconcile_runtime_schema(bind)

    return report
