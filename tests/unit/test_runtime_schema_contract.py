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
        assert "Inspect reported drift_classes and issues" in workflow["offline_migration"]["operator_steps"][0]
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

        with pytest.raises(RuntimeError, match="explicit additive reconcile"):
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
