from __future__ import annotations

import importlib

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tests.helpers.runtime import import_runtime_model_registry


pytestmark = pytest.mark.runtime_only


def _make_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def test_runtime_schema_contract_bootstraps_blank_database():
    import_runtime_model_registry()

    from AINDY.db.schema_contract import (
        SCHEMA_STATE_BLANK_BOOTSTRAP,
        ensure_runtime_schema,
        runtime_owned_table_names,
    )

    engine = _make_engine()
    try:
        report = ensure_runtime_schema(engine, allow_bootstrap=True)

        assert report.ok is True
        assert report.bootstrapped is True
        assert report.state == SCHEMA_STATE_BLANK_BOOTSTRAP
        assert set(runtime_owned_table_names()) <= set(report_table_names(engine))
    finally:
        engine.dispose()


def test_runtime_schema_contract_rejects_partial_runtime_schema():
    import_runtime_model_registry()

    from AINDY.db.database import Base
    from AINDY.db.schema_contract import (
        DRIFT_CLASS_ADDITIVE_MISSING_TABLE,
        REMEDIATION_STARTUP_RECONCILE,
        SCHEMA_STATE_UPGRADE_REQUIRED,
        ensure_runtime_schema,
    )

    engine = _make_engine()
    try:
        Base.metadata.tables["background_task_leases"].create(bind=engine)

        report = ensure_runtime_schema(engine, allow_bootstrap=True)

        assert report.ok is False
        assert report.bootstrapped is False
        assert report.state == SCHEMA_STATE_UPGRADE_REQUIRED
        assert report.reconcile_supported is True
        assert report.drift_classes == (DRIFT_CLASS_ADDITIVE_MISSING_TABLE,)
        assert report.remediation_categories == (REMEDIATION_STARTUP_RECONCILE,)
        assert report.offline_migration_required is False
        assert any(issue.code == "missing_table" for issue in report.issues)
    finally:
        engine.dispose()


def test_runtime_schema_contract_reconciles_partial_runtime_schema_when_explicit():
    import_runtime_model_registry()

    from AINDY.db.database import Base
    from AINDY.db.schema_contract import ensure_runtime_schema, runtime_owned_table_names

    engine = _make_engine()
    try:
        Base.metadata.tables["background_task_leases"].create(bind=engine)

        report = ensure_runtime_schema(
            engine,
            allow_bootstrap=True,
            allow_reconcile=True,
        )

        assert report.ok is True
        assert report.reconciled is True
        assert report.bootstrapped is False
        assert set(runtime_owned_table_names()) <= set(report_table_names(engine))
    finally:
        engine.dispose()


def test_runtime_schema_contract_marks_type_drift_as_manual_intervention():
    import_runtime_model_registry()

    from AINDY.db.schema_contract import (
        DRIFT_CLASS_TYPE_MISMATCH,
        REMEDIATION_OFFLINE_MIGRATION,
        SCHEMA_STATE_INCOMPATIBLE_MANUAL,
        ensure_runtime_schema,
    )

    engine = _make_engine()
    try:
        table = Table(
            "background_task_leases",
            MetaData(),
            Column("id", String(36), primary_key=True),
            Column("name", Integer, nullable=False),
            Column("owner_id", String(255), nullable=False),
            Column("acquired_at", String(255), nullable=False),
            Column("heartbeat_at", String(255), nullable=False),
            Column("expires_at", String(255), nullable=False),
        )
        table.create(bind=engine)

        report = ensure_runtime_schema(
            engine,
            allow_bootstrap=True,
            allow_reconcile=True,
        )

        assert report.ok is False
        assert report.reconciled is False
        assert report.state == SCHEMA_STATE_INCOMPATIBLE_MANUAL
        assert DRIFT_CLASS_TYPE_MISMATCH in report.drift_classes
        assert REMEDIATION_OFFLINE_MIGRATION in report.remediation_categories
        assert report.offline_migration_required is True
        assert any(issue.code == "column_type_mismatch" for issue in report.issues)
    finally:
        engine.dispose()


def test_runtime_schema_contract_classifies_missing_required_column_as_offline_migration():
    import_runtime_model_registry()

    from AINDY.db.schema_contract import (
        DRIFT_CLASS_UNSUPPORTED_REQUIRED_COLUMN,
        REMEDIATION_OFFLINE_MIGRATION,
        SCHEMA_STATE_INCOMPATIBLE_MANUAL,
        ensure_runtime_schema,
    )

    engine = _make_engine()
    try:
        table = Table(
            "background_task_leases",
            MetaData(),
            Column("id", String(36), primary_key=True),
            Column("name", String(255), nullable=False),
            Column("owner_id", String(255), nullable=False),
            Column("acquired_at", String(255), nullable=False),
            Column("heartbeat_at", String(255), nullable=False),
        )
        table.create(bind=engine)

        report = ensure_runtime_schema(
            engine,
            allow_bootstrap=True,
            allow_reconcile=True,
        )

        assert report.state == SCHEMA_STATE_INCOMPATIBLE_MANUAL
        assert DRIFT_CLASS_UNSUPPORTED_REQUIRED_COLUMN in report.drift_classes
        assert REMEDIATION_OFFLINE_MIGRATION in report.remediation_categories
        assert report.offline_migration_required is True
        assert report.startup_reconcile_permitted is False
    finally:
        engine.dispose()


def test_runtime_schema_report_exposes_offline_migration_contract():
    import_runtime_model_registry()

    from AINDY.db.database import Base
    from AINDY.db.schema_contract import ensure_runtime_schema

    engine = _make_engine()
    try:
        Base.metadata.tables["background_task_leases"].create(bind=engine)
        report = ensure_runtime_schema(engine, allow_bootstrap=True)
        workflow = report.operator_workflow()

        assert workflow["operator_action"] == "startup_reconcile"
        assert workflow["offline_migration_required"] is False
        assert "offline_migration" in workflow
        assert workflow["offline_migration"]["owned_by"] == "aindy-runtime"
        assert workflow["offline_migration"]["inspection_command"] == "python -m AINDY.db.schema_ops inspect --format json"
        assert "python -m AINDY.db.schema_ops inspect --format json" in workflow["offline_migration"]["operator_steps"][0]
        assert workflow["inspection"]["entrypoints"]["module"] == "python -m AINDY.db.schema_ops inspect --format json"
    finally:
        engine.dispose()


def test_runtime_schema_report_exports_machine_readable_operator_payload():
    import_runtime_model_registry()

    from AINDY.db.database import Base
    from AINDY.db.schema_contract import ensure_runtime_schema

    engine = _make_engine()
    try:
        Base.metadata.tables["background_task_leases"].create(bind=engine)
        report = ensure_runtime_schema(engine, allow_bootstrap=True)
        payload = report.to_dict()

        assert payload["schema_contract_version"] == "2026-07-04"
        assert payload["state"] == "upgrade_required"
        assert payload["operator_action"] == "startup_reconcile"
        assert payload["inspection"]["entrypoints"]["module"] == "python -m AINDY.db.schema_ops inspect --format json"
        assert payload["schema_contract"]["owned_by"] == "aindy-runtime"
        assert "background_task_leases" in payload["schema_contract"]["table_names"]
    finally:
        engine.dispose()


def test_runtime_schema_ops_inspect_command_emits_machine_readable_payload(capsys, tmp_path):
    import_runtime_model_registry()

    from AINDY.db.database import Base
    from AINDY.db.schema_ops import main as schema_ops_main

    db_path = tmp_path / "schema_ops_inspect.db"
    file_url = f"sqlite:///{db_path.as_posix()}"
    engine = create_engine(file_url)
    try:
        Base.metadata.tables["background_task_leases"].create(bind=engine)
        exit_code = schema_ops_main(
            [
                "inspect",
                "--database-url",
                file_url,
                "--format",
                "json",
            ]
        )
        output = capsys.readouterr().out

        assert exit_code == 0
        assert '"state": "upgrade_required"' in output
        assert '"schema_contract_version": "2026-07-04"' in output
        assert '"owned_by": "aindy-runtime"' in output
    finally:
        engine.dispose()


def test_runtime_schema_ops_require_compatible_exits_nonzero_for_drift(tmp_path):
    import_runtime_model_registry()

    from AINDY.db.database import Base
    from AINDY.db.schema_ops import main as schema_ops_main

    db_path = tmp_path / "schema_ops_require_compatible.db"
    file_url = f"sqlite:///{db_path.as_posix()}"
    engine = create_engine(file_url)
    try:
        Base.metadata.tables["background_task_leases"].create(bind=engine)
        exit_code = schema_ops_main(
            [
                "inspect",
                "--database-url",
                file_url,
                "--require-compatible",
            ]
        )

        assert exit_code == 2
    finally:
        engine.dispose()


def test_startup_schema_guard_bootstraps_blank_runtime_schema(monkeypatch):
    import AINDY.startup as startup

    startup = importlib.reload(startup)
    engine = _make_engine()
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    try:
        monkeypatch.setenv("AINDY_ENFORCE_SCHEMA", "true")
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.setattr(startup.settings, "ENV", "development")
        monkeypatch.setattr(startup.settings, "TESTING", False)
        monkeypatch.setattr(startup.settings, "TEST_MODE", False)

        startup._enforce_schema_guard(session_factory)

        assert "background_task_leases" in report_table_names(engine)
    finally:
        engine.dispose()


def test_startup_schema_guard_requires_explicit_reconcile_for_partial_schema(monkeypatch):
    import AINDY.startup as startup

    startup = importlib.reload(startup)
    engine = _make_engine()
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    from AINDY.db.database import Base

    try:
        Base.metadata.tables["background_task_leases"].create(bind=engine)
        monkeypatch.setenv("AINDY_ENFORCE_SCHEMA", "true")
        monkeypatch.delenv("AINDY_SCHEMA_RECONCILE", raising=False)
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.setattr(startup.settings, "ENV", "development")
        monkeypatch.setattr(startup.settings, "TESTING", False)
        monkeypatch.setattr(startup.settings, "TEST_MODE", False)

        with pytest.raises(RuntimeError, match="python -m AINDY.db.schema_ops inspect --format json"):
            startup._enforce_schema_guard(session_factory)
    finally:
        engine.dispose()


def test_startup_schema_guard_reconciles_partial_schema_when_explicit(monkeypatch):
    import AINDY.startup as startup

    startup = importlib.reload(startup)
    engine = _make_engine()
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    from AINDY.db.database import Base
    from AINDY.db.schema_contract import runtime_owned_table_names

    try:
        Base.metadata.tables["background_task_leases"].create(bind=engine)
        monkeypatch.setenv("AINDY_ENFORCE_SCHEMA", "true")
        monkeypatch.setenv("AINDY_SCHEMA_RECONCILE", "true")
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.setattr(startup.settings, "ENV", "development")
        monkeypatch.setattr(startup.settings, "TESTING", False)
        monkeypatch.setattr(startup.settings, "TEST_MODE", False)

        startup._enforce_schema_guard(session_factory)

        assert set(runtime_owned_table_names()) <= set(report_table_names(engine))
    finally:
        engine.dispose()


def test_health_schema_check_reports_runtime_schema_drift(monkeypatch):
    import_runtime_model_registry()

    from AINDY.platform_layer import health_service

    engine = _make_engine()
    try:
        table = Table(
            "background_task_leases",
            MetaData(),
            Column("id", String(36), primary_key=True),
            Column("name", Integer, nullable=False),
            Column("owner_id", String(255), nullable=False),
            Column("acquired_at", String(255), nullable=False),
            Column("heartbeat_at", String(255), nullable=False),
            Column("expires_at", String(255), nullable=False),
        )
        table.create(bind=engine)
        monkeypatch.setattr(health_service.settings, "DATABASE_URL", "sqlite://")
        monkeypatch.setattr(health_service, "create_engine", lambda *args, **kwargs: engine)

        status = health_service.check_schema()

        assert status.status == "unavailable"
        assert status.critical is True
        assert status.metadata["schema_state"] == "incompatible_manual"
        assert "column_type_mismatch" in status.metadata["drift_classes"]
        assert status.metadata["offline_migration_required"] is True
        assert status.metadata["inspection"]["entrypoints"]["module"] == "python -m AINDY.db.schema_ops inspect --format json"
        assert status.metadata["schema_contract"]["owned_by"] == "aindy-runtime"
        assert "expected" in (status.detail or "")
    finally:
        engine.dispose()


def test_worker_schema_readiness_bootstraps_blank_runtime_schema(monkeypatch):
    import AINDY.worker as worker

    engine = _make_engine()
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    try:
        monkeypatch.setattr(worker, "SessionLocal", session_factory)

        assert worker._background_schema_ready() is True
        assert "background_task_leases" in report_table_names(engine)
    finally:
        engine.dispose()


def test_worker_schema_readiness_reconciles_partial_schema_when_explicit(monkeypatch):
    import AINDY.worker as worker

    from AINDY.db.database import Base
    from AINDY.db.schema_contract import runtime_owned_table_names

    engine = _make_engine()
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    try:
        Base.metadata.tables["background_task_leases"].create(bind=engine)
        monkeypatch.setenv("AINDY_SCHEMA_RECONCILE", "true")
        monkeypatch.setattr(worker, "SessionLocal", session_factory)

        assert worker._background_schema_ready() is True
        assert set(runtime_owned_table_names()) <= set(report_table_names(engine))
    finally:
        engine.dispose()


def report_table_names(engine) -> set[str]:
    with engine.connect() as conn:
        return set(conn.dialect.get_table_names(conn))


# ── Advisory lock (IDEM-6) ────────────────────────────────────────────────────

def _make_postgres_mock_engine():
    """Return a MagicMock that passes the postgres URL detection check.

    isinstance(mock, Engine) is False for a MagicMock, so the code takes the
    nullcontext(resolved) branch and _conn == mock_engine inside the with block.
    str(mock.engine.url) returns the default MagicMock repr, which does not start
    with "sqlite", so _is_postgres = True.
    Advisory lock execute() calls land on mock_engine.execute.
    """
    from unittest.mock import MagicMock
    return MagicMock()


def test_reconcile_blank_db_acquires_advisory_lock_for_postgres():
    """pg_advisory_lock + pg_advisory_unlock bracket create_all for PostgreSQL."""
    from unittest.mock import MagicMock, patch
    from AINDY.db.schema_contract import (
        SCHEMA_STATE_BLANK_DATABASE,
        SCHEMA_STATE_BLANK_BOOTSTRAP,
        reconcile_runtime_schema,
    )

    mock_engine = _make_postgres_mock_engine()

    blank_report = MagicMock()
    blank_report.state = SCHEMA_STATE_BLANK_DATABASE
    post_lock_report = MagicMock()
    post_lock_report.state = SCHEMA_STATE_BLANK_DATABASE
    validated = MagicMock()
    validated.state = SCHEMA_STATE_BLANK_BOOTSTRAP
    validated.ok = True

    with patch(
        "AINDY.db.schema_contract.inspect_runtime_schema",
        side_effect=[blank_report, post_lock_report, validated],
    ):
        with patch("AINDY.db.schema_contract.Base.metadata.create_all") as mock_create_all:
            result = reconcile_runtime_schema(mock_engine)

    # isinstance(mock_engine, Engine) = False → nullcontext path → _conn = mock_engine.
    # c.args[0] is the TextClause; str() yields the SQL text.
    execute_sqls = [str(c.args[0]) for c in mock_engine.execute.call_args_list]
    assert any("pg_advisory_lock" in s for s in execute_sqls), execute_sqls
    assert any("pg_advisory_unlock" in s for s in execute_sqls), execute_sqls
    mock_create_all.assert_called_once()
    assert result.bootstrapped is True


def test_reconcile_blank_db_skips_create_all_when_another_instance_bootstrapped():
    """TOCTOU guard: if DB is no longer blank after acquiring the lock, skip create_all."""
    from unittest.mock import MagicMock, patch
    from AINDY.db.schema_contract import (
        SCHEMA_STATE_BLANK_DATABASE,
        SCHEMA_STATE_COMPATIBLE,
        reconcile_runtime_schema,
    )

    mock_engine = _make_postgres_mock_engine()

    blank_report = MagicMock()
    blank_report.state = SCHEMA_STATE_BLANK_DATABASE
    post_lock_report = MagicMock()
    post_lock_report.state = SCHEMA_STATE_COMPATIBLE  # already bootstrapped by peer
    validated = MagicMock()
    validated.state = SCHEMA_STATE_COMPATIBLE
    validated.ok = True

    with patch(
        "AINDY.db.schema_contract.inspect_runtime_schema",
        side_effect=[blank_report, post_lock_report, validated],
    ):
        with patch("AINDY.db.schema_contract.Base.metadata.create_all") as mock_create_all:
            reconcile_runtime_schema(mock_engine)

    execute_sqls = [str(c.args[0]) for c in mock_engine.execute.call_args_list]
    assert any("pg_advisory_lock" in s for s in execute_sqls), execute_sqls
    assert any("pg_advisory_unlock" in s for s in execute_sqls), execute_sqls
    mock_create_all.assert_not_called()


def test_reconcile_blank_db_advisory_unlock_called_even_on_create_all_failure():
    """Advisory lock is always released even if create_all raises."""
    from unittest.mock import MagicMock, patch
    from AINDY.db.schema_contract import (
        SCHEMA_STATE_BLANK_DATABASE,
        reconcile_runtime_schema,
    )

    mock_engine = _make_postgres_mock_engine()

    blank_report = MagicMock()
    blank_report.state = SCHEMA_STATE_BLANK_DATABASE
    post_lock_report = MagicMock()
    post_lock_report.state = SCHEMA_STATE_BLANK_DATABASE

    with patch(
        "AINDY.db.schema_contract.inspect_runtime_schema",
        side_effect=[blank_report, post_lock_report],
    ):
        with patch(
            "AINDY.db.schema_contract.Base.metadata.create_all",
            side_effect=RuntimeError("DDL error"),
        ):
            with pytest.raises(RuntimeError, match="DDL error"):
                reconcile_runtime_schema(mock_engine)

    execute_sqls = [str(c.args[0]) for c in mock_engine.execute.call_args_list]
    assert any("pg_advisory_unlock" in s for s in execute_sqls), execute_sqls
