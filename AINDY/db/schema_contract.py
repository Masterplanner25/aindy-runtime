"""Runtime-owned database schema bootstrap, validation, and reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
SCHEMA_CONTRACT_VERSION = "2026-05-23"
SCHEMA_INSPECT_MODULE = "python -m AINDY.db.schema_ops inspect --format json"

SCHEMA_STATE_BLANK_DATABASE = "blank_database"
SCHEMA_STATE_BLANK_BOOTSTRAP = "blank_bootstrap"
SCHEMA_STATE_COMPATIBLE = "compatible"
SCHEMA_STATE_UPGRADE_REQUIRED = "upgrade_required"
SCHEMA_STATE_INCOMPATIBLE_MANUAL = "incompatible_manual"

_SAFE_RECONCILE_CODES = {"missing_table", "missing_column"}
REMEDIATION_BOOTSTRAP = "bootstrap"
REMEDIATION_STARTUP_RECONCILE = "startup_reconcile"
REMEDIATION_OFFLINE_MIGRATION = "offline_migration"
REMEDIATION_MANUAL_REPAIR = "manual_repair"
DRIFT_CLASS_ADDITIVE_MISSING_TABLE = "additive_missing_table"
DRIFT_CLASS_ADDITIVE_MISSING_COLUMN = "additive_missing_column"
DRIFT_CLASS_UNSUPPORTED_REQUIRED_COLUMN = "unsupported_required_column"
DRIFT_CLASS_TYPE_MISMATCH = "column_type_mismatch"
DRIFT_CLASS_NULLABILITY_MISMATCH = "column_nullability_mismatch"
DRIFT_CLASS_PRIMARY_KEY_MISMATCH = "primary_key_mismatch"


@dataclass(frozen=True)
class SchemaIssue:
    code: str
    detail: str
    table: str | None = None
    column: str | None = None
    reconcile_supported: bool = False
    drift_class: str = ""
    remediation_category: str = REMEDIATION_MANUAL_REPAIR

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "detail": self.detail,
            "table": self.table,
            "column": self.column,
            "reconcile_supported": self.reconcile_supported,
            "drift_class": self.drift_class,
            "remediation_category": self.remediation_category,
        }


@dataclass(frozen=True)
class SchemaReport:
    ok: bool
    bootstrapped: bool
    reconciled: bool
    state: str
    reconcile_supported: bool
    operator_action: str
    issues: tuple[SchemaIssue, ...]
    drift_classes: tuple[str, ...]
    remediation_categories: tuple[str, ...]
    offline_migration_required: bool
    startup_reconcile_permitted: bool

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

    def operator_workflow(self) -> dict[str, object]:
        return {
            "schema_contract_version": SCHEMA_CONTRACT_VERSION,
            "state": self.state,
            "operator_action": self.operator_action,
            "reconcile_supported": self.reconcile_supported,
            "startup_reconcile_permitted": self.startup_reconcile_permitted,
            "offline_migration_required": self.offline_migration_required,
            "drift_classes": list(self.drift_classes),
            "remediation_categories": list(self.remediation_categories),
            "inspection": inspection_contract(
                remediation_categories=self.remediation_categories,
                drift_classes=self.drift_classes,
            ),
            "offline_migration": offline_migration_contract(
                remediation_categories=self.remediation_categories,
                drift_classes=self.drift_classes,
            ),
            "schema_contract": runtime_schema_contract_metadata(),
        }

    def to_dict(self) -> dict[str, object]:
        workflow = self.operator_workflow()
        return {
            "schema_contract_version": SCHEMA_CONTRACT_VERSION,
            "ok": self.ok,
            "bootstrapped": self.bootstrapped,
            "reconciled": self.reconciled,
            "state": self.state,
            "summary": self.summary(),
            "reconcile_supported": self.reconcile_supported,
            "operator_action": self.operator_action,
            "drift_classes": list(self.drift_classes),
            "remediation_categories": list(self.remediation_categories),
            "offline_migration_required": self.offline_migration_required,
            "startup_reconcile_permitted": self.startup_reconcile_permitted,
            "issues": [issue.to_dict() for issue in self.issues],
            "inspection": workflow["inspection"],
            "offline_migration": workflow["offline_migration"],
            "schema_contract": workflow["schema_contract"],
        }


def runtime_schema_contract_metadata() -> dict[str, object]:
    return {
        "owned_by": "aindy-runtime",
        "schema_contract_version": SCHEMA_CONTRACT_VERSION,
        "table_names": list(runtime_owned_table_names()),
        "lifecycle_states": [
            SCHEMA_STATE_BLANK_BOOTSTRAP,
            SCHEMA_STATE_COMPATIBLE,
            SCHEMA_STATE_UPGRADE_REQUIRED,
            SCHEMA_STATE_INCOMPATIBLE_MANUAL,
        ],
        "automatic_actions": {
            "blank_bootstrap": [DRIFT_CLASS_ADDITIVE_MISSING_TABLE],
            "explicit_startup_reconcile_only": [
                DRIFT_CLASS_ADDITIVE_MISSING_TABLE,
                DRIFT_CLASS_ADDITIVE_MISSING_COLUMN,
            ],
            "never_automatic": [
                DRIFT_CLASS_UNSUPPORTED_REQUIRED_COLUMN,
                DRIFT_CLASS_TYPE_MISMATCH,
                DRIFT_CLASS_NULLABILITY_MISMATCH,
                DRIFT_CLASS_PRIMARY_KEY_MISMATCH,
            ],
        },
        "remediation_categories": {
            REMEDIATION_BOOTSTRAP: "Blank-database bootstrap from packaged metadata.",
            REMEDIATION_STARTUP_RECONCILE: (
                "Explicit additive startup reconcile with AINDY_SCHEMA_RECONCILE=true."
            ),
            REMEDIATION_OFFLINE_MIGRATION: (
                "Operator-managed offline SQL migration while the runtime is stopped."
            ),
            REMEDIATION_MANUAL_REPAIR: (
                "Operator-managed manual schema repair before restart."
            ),
        },
        "inspection": inspection_contract(),
    }


def inspection_contract(
    *,
    remediation_categories: tuple[str, ...] = (),
    drift_classes: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "mode": "inspection-only",
        "entrypoints": {
            "module": SCHEMA_INSPECT_MODULE,
            "health_surface": "GET /health",
            "readiness_surface": "GET /ready",
        },
        "active_remediation_categories": list(remediation_categories),
        "active_drift_classes": list(drift_classes),
        "notes": (
            "Inspection tooling exports the runtime-owned schema contract and the "
            "current drift report. It does not mutate the database."
        ),
    }


def offline_migration_contract(
    *,
    remediation_categories: tuple[str, ...] = (),
    drift_classes: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "owned_by": "aindy-runtime",
        "schema_contract_version": SCHEMA_CONTRACT_VERSION,
        "mode": "offline-manual",
        "required_for_categories": [
            REMEDIATION_OFFLINE_MIGRATION,
            REMEDIATION_MANUAL_REPAIR,
        ],
        "startup_reconcile_scope": [
            DRIFT_CLASS_ADDITIVE_MISSING_TABLE,
            DRIFT_CLASS_ADDITIVE_MISSING_COLUMN,
        ],
        "not_performed_automatically": [
            DRIFT_CLASS_TYPE_MISMATCH,
            DRIFT_CLASS_NULLABILITY_MISMATCH,
            DRIFT_CLASS_PRIMARY_KEY_MISMATCH,
            DRIFT_CLASS_UNSUPPORTED_REQUIRED_COLUMN,
        ],
        "inspection_command": SCHEMA_INSPECT_MODULE,
        "unsupported_drift_matrix": {
            DRIFT_CLASS_UNSUPPORTED_REQUIRED_COLUMN: REMEDIATION_OFFLINE_MIGRATION,
            DRIFT_CLASS_TYPE_MISMATCH: REMEDIATION_OFFLINE_MIGRATION,
            DRIFT_CLASS_NULLABILITY_MISMATCH: REMEDIATION_OFFLINE_MIGRATION,
            DRIFT_CLASS_PRIMARY_KEY_MISMATCH: REMEDIATION_MANUAL_REPAIR,
        },
        "operator_steps": [
            "Inspect the drift report with GET /health or python -m AINDY.db.schema_ops inspect --format json.",
            "Prepare an out-of-band SQL migration or schema repair that matches packaged runtime metadata.",
            "Apply the change while the runtime is not serving traffic.",
            "Restart the runtime with AINDY_ENFORCE_SCHEMA=true and verify schema_state=compatible.",
        ],
        "active_remediation_categories": list(remediation_categories),
        "active_drift_classes": list(drift_classes),
    }


_PG_TYPE_ALIASES: dict[str, str] = {
    # PostgreSQL FLOAT and DOUBLE PRECISION are numeric aliases.
    # SQLAlchemy Float compiles to "float"; PostgreSQL reflects stored
    # float columns as "double precision".
    "double precision": "float",
    "double_precision": "float",
    # SQLAlchemy DateTime(timezone=False) compiles to "timestamp"; PostgreSQL
    # inspector returns "timestamp without time zone" for the same column.
    "timestamp without time zone": "timestamp",
}


def _normalize_type_name(type_, *, dialect=None) -> str:
    if dialect is not None:
        try:
            compiled = str(type_.compile(dialect=dialect))
            raw = compiled.lower().split("(", 1)[0].strip()
            return _PG_TYPE_ALIASES.get(raw, raw)
        except Exception:
            pass
    visit_name = getattr(type_, "__visit_name__", None)
    if visit_name:
        raw = str(visit_name).lower()
        return _PG_TYPE_ALIASES.get(raw, raw)
    raw = type(type_).__name__.lower()
    return _PG_TYPE_ALIASES.get(raw, raw)


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
                    drift_class=DRIFT_CLASS_ADDITIVE_MISSING_TABLE,
                    remediation_category=REMEDIATION_STARTUP_RECONCILE,
                )
            )
            continue

        actual_columns = {
            column["name"]: column for column in inspector.get_columns(table_name)
        }
        # get_columns() does not reliably populate "primary_key" on PostgreSQL;
        # get_pk_constraint() is the authoritative source.
        try:
            pk_info = inspector.get_pk_constraint(table_name)
            pk_col_names: set[str] = set(pk_info.get("constrained_columns", []))
        except Exception:
            pk_col_names = set()
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
                        drift_class=(
                            DRIFT_CLASS_ADDITIVE_MISSING_COLUMN
                            if _column_reconcile_supported(expected_column)
                            else DRIFT_CLASS_UNSUPPORTED_REQUIRED_COLUMN
                        ),
                        remediation_category=(
                            REMEDIATION_STARTUP_RECONCILE
                            if _column_reconcile_supported(expected_column)
                            else REMEDIATION_OFFLINE_MIGRATION
                        ),
                    )
                )
                continue

            expected_type = _normalize_type_name(
                expected_column.type,
                dialect=resolved.dialect,
            )
            # Pass the same dialect so both sides use the same compilation path.
            actual_type = _normalize_type_name(actual_column["type"], dialect=resolved.dialect)
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
                        drift_class=DRIFT_CLASS_TYPE_MISMATCH,
                        remediation_category=REMEDIATION_OFFLINE_MIGRATION,
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
                        drift_class=DRIFT_CLASS_NULLABILITY_MISMATCH,
                        remediation_category=REMEDIATION_OFFLINE_MIGRATION,
                    )
                )

            actual_primary_key = expected_column.name in pk_col_names
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
                        drift_class=DRIFT_CLASS_PRIMARY_KEY_MISMATCH,
                        remediation_category=REMEDIATION_MANUAL_REPAIR,
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
) -> tuple[str, bool, str, tuple[str, ...], tuple[str, ...], bool]:
    if not existing_runtime_tables:
        return (
            SCHEMA_STATE_BLANK_DATABASE,
            True,
            REMEDIATION_BOOTSTRAP,
            (),
            (REMEDIATION_BOOTSTRAP,),
            False,
        )
    if not issues:
        return (
            SCHEMA_STATE_COMPATIBLE,
            False,
            "none",
            (),
            (),
            False,
        )
    drift_classes = tuple(sorted({issue.drift_class or issue.code for issue in issues}))
    remediation_categories = tuple(
        sorted({issue.remediation_category for issue in issues if issue.remediation_category})
    )
    reconcile_supported = all(
        issue.code in _SAFE_RECONCILE_CODES and issue.reconcile_supported
        for issue in issues
    )
    if reconcile_supported:
        return (
            SCHEMA_STATE_UPGRADE_REQUIRED,
            True,
            REMEDIATION_STARTUP_RECONCILE,
            drift_classes,
            remediation_categories,
            False,
        )
    return (
        SCHEMA_STATE_INCOMPATIBLE_MANUAL,
        False,
        (
            REMEDIATION_MANUAL_REPAIR
            if REMEDIATION_MANUAL_REPAIR in remediation_categories
            else REMEDIATION_OFFLINE_MIGRATION
        ),
        drift_classes,
        remediation_categories,
        True,
    )


def inspect_runtime_schema(bind: Engine | Connection | Session) -> SchemaReport:
    resolved = _resolve_bind(bind)
    existing_runtime_tables, issues = _inspect_schema_issues(resolved)
    (
        state,
        reconcile_supported,
        operator_action,
        drift_classes,
        remediation_categories,
        offline_migration_required,
    ) = _classify_schema_state(
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
        drift_classes=drift_classes,
        remediation_categories=remediation_categories,
        offline_migration_required=offline_migration_required,
        startup_reconcile_permitted=reconcile_supported,
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
            drift_classes=validated.drift_classes,
            remediation_categories=validated.remediation_categories,
            offline_migration_required=validated.offline_migration_required,
            startup_reconcile_permitted=validated.startup_reconcile_permitted,
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
        drift_classes=validated.drift_classes,
        remediation_categories=validated.remediation_categories,
        offline_migration_required=validated.offline_migration_required,
        startup_reconcile_permitted=validated.startup_reconcile_permitted,
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


def inspect_runtime_schema_payload(bind: Engine | Connection | Session) -> dict[str, object]:
    return inspect_runtime_schema(bind).to_dict()
