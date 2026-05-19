from __future__ import annotations

import importlib

import pytest
from sqlalchemy import create_engine
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

    from AINDY.db.schema_contract import ensure_runtime_schema, runtime_owned_table_names

    engine = _make_engine()
    try:
        report = ensure_runtime_schema(engine, allow_bootstrap=True)

        assert report.ok is True
        assert report.bootstrapped is True
        assert set(runtime_owned_table_names()) <= set(report_table_names(engine))
    finally:
        engine.dispose()


def test_runtime_schema_contract_rejects_partial_runtime_schema():
    import_runtime_model_registry()

    from AINDY.db.database import Base
    from AINDY.db.schema_contract import ensure_runtime_schema

    engine = _make_engine()
    try:
        Base.metadata.tables["background_task_leases"].create(bind=engine)

        report = ensure_runtime_schema(engine, allow_bootstrap=True)

        assert report.ok is False
        assert report.bootstrapped is False
        assert any(issue.code == "missing_table" for issue in report.issues)
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


def test_health_schema_check_reports_runtime_schema_drift(monkeypatch):
    import_runtime_model_registry()

    from AINDY.db.database import Base
    from AINDY.platform_layer import health_service

    engine = _make_engine()
    try:
        Base.metadata.tables["background_task_leases"].create(bind=engine)
        monkeypatch.setattr(health_service.settings, "DATABASE_URL", "sqlite://")
        monkeypatch.setattr(health_service, "create_engine", lambda *args, **kwargs: engine)

        status = health_service.check_schema()

        assert status.status == "unavailable"
        assert status.critical is True
        assert "Missing required runtime table" in (status.detail or "")
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


def report_table_names(engine) -> set[str]:
    with engine.connect() as conn:
        return set(conn.dialect.get_table_names(conn))
