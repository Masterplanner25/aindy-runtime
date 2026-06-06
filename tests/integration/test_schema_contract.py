"""
tests/integration/test_schema_contract.py
──────────────────────────────────────────
Validates the runtime schema bootstrap contract.

Replaces the Alembic drift check from the archived monolith. The runtime
uses schema_contract.py (not Alembic) to bootstrap, inspect, and reconcile
its database tables.

Runs on PostgreSQL (requires DATABASE_URL pointing to a Postgres instance)
as well as SQLite for basic contract-version and table-name assertions.
"""
from __future__ import annotations

import pytest


pytestmark = [pytest.mark.integration, pytest.mark.postgres]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_postgres(engine) -> bool:
    return engine.dialect.name == "postgresql"


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_schema_contract_version_is_pinned():
    """SCHEMA_CONTRACT_VERSION must be a non-empty ISO-date string."""
    from AINDY.db.schema_contract import SCHEMA_CONTRACT_VERSION

    assert isinstance(SCHEMA_CONTRACT_VERSION, str)
    assert len(SCHEMA_CONTRACT_VERSION) >= 8
    # e.g. "2026-05-20" → starts with a four-digit year
    assert SCHEMA_CONTRACT_VERSION[:4].isdigit()


def test_runtime_owned_table_names_non_empty():
    """runtime_owned_table_names() must return at least the core runtime tables."""
    from AINDY.db.schema_contract import runtime_owned_table_names

    names = runtime_owned_table_names()
    assert isinstance(names, tuple)
    assert len(names) > 0
    # A few tables that must always exist in the runtime contract
    for expected in ("users", "system_events", "execution_units"):
        assert expected in names, (
            f"Expected runtime table {expected!r} missing from contract: {names}"
        )


def test_inspect_runtime_schema_tables_exist_after_bootstrap(test_engine):
    """All runtime-owned tables must exist after _setup_postgres_schema bootstrap."""
    from sqlalchemy import inspect as sa_inspect
    from AINDY.db.schema_contract import runtime_owned_table_names

    inspector = sa_inspect(test_engine)
    missing = [t for t in runtime_owned_table_names() if not inspector.has_table(t)]
    assert not missing, f"Tables missing after bootstrap: {missing}"


def test_inspect_runtime_schema_compatible_on_existing_db(test_engine):
    """inspect_runtime_schema() reports 'compatible' when tables already exist."""
    from AINDY.db.schema_contract import (
        inspect_runtime_schema,
        SCHEMA_STATE_COMPATIBLE,
        SCHEMA_STATE_BLANK_BOOTSTRAP,
    )

    report = inspect_runtime_schema(test_engine)

    assert report.state in (SCHEMA_STATE_COMPATIBLE, SCHEMA_STATE_BLANK_BOOTSTRAP), (
        f"Expected compatible or blank_bootstrap, got {report.state!r}: {report.summary()}"
    )
    assert report.ok, f"Schema report is not OK: {report.summary()}"


def test_ensure_runtime_schema_bootstraps_blank_database():
    """ensure_runtime_schema() creates tables on a blank SQLite database."""
    from sqlalchemy import create_engine, inspect as sa_inspect
    from sqlalchemy.pool import StaticPool
    from AINDY.db.schema_contract import (
        ensure_runtime_schema,
        runtime_owned_table_names,
        SCHEMA_STATE_BLANK_BOOTSTRAP,
        SCHEMA_STATE_COMPATIBLE,
    )
    from tests.helpers.runtime import import_runtime_model_registry

    import_runtime_model_registry()

    blank_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        report = ensure_runtime_schema(blank_engine, allow_bootstrap=True, allow_reconcile=False)
        assert report.state in (SCHEMA_STATE_BLANK_BOOTSTRAP, SCHEMA_STATE_COMPATIBLE), (
            f"ensure_runtime_schema on blank DB returned unexpected state "
            f"{report.state!r}: {report.summary()}"
        )
        assert report.ok, f"Bootstrap result not OK: {report.summary()}"

        # All runtime-owned tables must now exist
        inspector = sa_inspect(blank_engine)
        for table_name in runtime_owned_table_names():
            assert inspector.has_table(table_name), (
                f"Table {table_name!r} missing after bootstrap"
            )
    finally:
        blank_engine.dispose()


def test_reconcile_runtime_schema_on_compatible_schema(test_engine):
    """reconcile_runtime_schema() on an already-compatible schema returns ok."""
    from AINDY.db.schema_contract import (
        reconcile_runtime_schema,
        SCHEMA_STATE_COMPATIBLE,
        SCHEMA_STATE_BLANK_BOOTSTRAP,
    )

    report = reconcile_runtime_schema(test_engine)

    assert report.state in (SCHEMA_STATE_COMPATIBLE, SCHEMA_STATE_BLANK_BOOTSTRAP), (
        f"reconcile_runtime_schema returned {report.state!r}: {report.summary()}"
    )
    assert report.ok, f"Reconcile report is not OK: {report.summary()}"


def test_inspect_blank_database_returns_blank_database_state():
    """inspect_runtime_schema() on an empty SQLite engine reports blank_database."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from AINDY.db.schema_contract import (
        inspect_runtime_schema,
        SCHEMA_STATE_BLANK_DATABASE,
    )
    from tests.helpers.runtime import import_runtime_model_registry

    import_runtime_model_registry()

    blank_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        report = inspect_runtime_schema(blank_engine)
        assert report.state == SCHEMA_STATE_BLANK_DATABASE, (
            f"Expected blank_database on empty engine, got {report.state!r}"
        )
        assert not report.ok
    finally:
        blank_engine.dispose()


# ── Alembic tests ─────────────────────────────────────────────────────────────

def test_alembic_version_runtime_table_exists(test_engine):
    """alembic_version_runtime table must exist after migration 0002 is applied."""
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(test_engine)
    assert inspector.has_table("alembic_version_runtime"), (
        "alembic_version_runtime table missing — run 'alembic upgrade head' first"
    )


def test_alembic_version_runtime_at_head(test_engine):
    """alembic_version_runtime must be stamped at 0005 (current head)."""
    from sqlalchemy import text

    with test_engine.connect() as conn:
        row = conn.execute(
            text("SELECT version_num FROM alembic_version_runtime")
        ).fetchone()

    assert row is not None, "alembic_version_runtime has no rows — DB not stamped"
    assert row[0] == "0005", (
        f"Expected version '0005', got {row[0]!r}"
    )


def test_effect_records_table_exists(test_engine):
    """effect_records table must exist after migration 0003 is applied."""
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(test_engine)
    assert inspector.has_table("effect_records"), (
        "effect_records table missing — run 'alembic upgrade head' first"
    )


def test_effect_records_action_id_unique_index_exists(test_engine):
    """effect_records must have a unique index on action_id."""
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(test_engine)
    indexes = {idx["name"] for idx in inspector.get_indexes("effect_records")}
    assert "uq_effect_records_action_id" in indexes, (
        f"Expected unique index uq_effect_records_action_id — found: {sorted(indexes)}"
    )


def test_idempotency_constraints_exist(test_engine):
    """Migration 0002 must have applied all idempotency unique indexes."""
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(test_engine)

    expected_indexes = {
        "webhook_subscriptions": "uq_webhook_subscriptions_event_url_active",
        "platform_api_keys": "uq_platform_api_keys_user_name_active",
        "execution_units": "uq_execution_units_source",
        "dynamic_flows": "uq_dynamic_flows_name",
        "dynamic_nodes": "uq_dynamic_nodes_name",
    }

    for table, index_name in expected_indexes.items():
        indexes = {idx["name"] for idx in inspector.get_indexes(table)}
        assert index_name in indexes, (
            f"Expected index {index_name!r} on {table!r} — not found. "
            f"Existing indexes: {sorted(indexes)}"
        )
