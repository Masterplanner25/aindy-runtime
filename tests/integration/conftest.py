"""
tests/integration/conftest.py
──────────────────────────────
Fixtures for the PostgreSQL/Redis integration test tier.

This conftest adds only what is absent from tests/fixtures/db.py and
tests/fixtures/client.py:
  - redis_backend  — real RedisQueueBackend scoped to a unique test namespace
  - mongo_client   — session-scoped MongoDB client (skipped if unavailable)
  - mongo_db       — function-scoped MongoDB database (dropped after each test)
  - test_user      — a persisted User row for the current test transaction
  - auth_headers   — Bearer token header for test_user
  - client         — alias for runtime_only_client

The existing db.py fixtures (test_engine, db_session, db_connection,
testing_session_factory, db_session_factory, cleanup_committed_test_state)
and client.py fixtures (runtime_only_app, runtime_only_client) are loaded
automatically by the top-level pytest_plugins declaration in tests/conftest.py.

DATABASE_URL must point to a PostgreSQL instance before running this suite.
Start the required services with: docker-compose -f docker-compose.test.yml up -d
"""
from __future__ import annotations

import os
import uuid

import pytest


def pytest_collection_modifyitems(config, items):
    """Apply skip markers based on missing environment prerequisites."""
    db_url = os.getenv("DATABASE_URL", "")
    redis_url = os.getenv("REDIS_URL", "")

    skip_no_postgres = pytest.mark.skip(
        reason="DATABASE_URL is not PostgreSQL — run docker-compose -f docker-compose.test.yml up -d"
    )
    skip_no_redis = pytest.mark.skip(reason="REDIS_URL not set — skipping Redis tests")

    for item in items:
        # Integration tests that reach the DB need PostgreSQL
        if item.get_closest_marker("integration") and not db_url.startswith("postgresql"):
            item.add_marker(skip_no_postgres)
        # Redis-specific tests also skip without REDIS_URL
        if item.get_closest_marker("redis") and not redis_url:
            item.add_marker(skip_no_redis)


# ── Redis ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def redis_backend():
    """
    Real RedisQueueBackend connected to REDIS_URL with a unique test namespace.

    Skipped if REDIS_URL is not set.  All keys written during the test are
    deleted on teardown so tests do not bleed state into one another.
    """
    redis_url = os.getenv("REDIS_URL", "")
    if not redis_url:
        pytest.skip("REDIS_URL not set")

    from AINDY.core.distributed_queue import RedisQueueBackend

    namespace = f"aindy:test:{uuid.uuid4().hex}"
    backend = RedisQueueBackend(url=redis_url, queue_name=namespace)
    yield backend

    try:
        keys = backend._redis.keys(f"{namespace}:*")
        if keys:
            backend._redis.delete(*keys)
    except Exception:
        pass


# ── MongoDB ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def mongo_client():
    """Session-scoped MongoDB client; skipped if Mongo is unavailable."""
    mongo_url = os.getenv("MONGO_URL", "")
    if not mongo_url:
        pytest.skip("MONGO_URL not set")
    try:
        import pymongo

        client = pymongo.MongoClient(mongo_url, serverSelectionTimeoutMS=3000)
        client.admin.command("ping")
    except Exception as exc:
        pytest.skip(f"MongoDB unavailable: {exc}")
    yield client
    client.close()


@pytest.fixture
def mongo_db(mongo_client):
    """Function-scoped MongoDB database; dropped after each test."""
    db_name = f"aindy_integration_test_{uuid.uuid4().hex[:8]}"
    db = mongo_client[db_name]
    yield db
    mongo_client.drop_database(db_name)


# ── Auth helpers ──────────────────────────────────────────────────────────────

@pytest.fixture
def test_user(db_session):
    """A persisted User row visible for the duration of a single test."""
    from AINDY.db.models.user import User
    from AINDY.services.auth_service import hash_password

    user = User(
        email=f"integration-{uuid.uuid4().hex[:8]}@aindy.test",
        username=f"testuser-{uuid.uuid4().hex[:8]}",
        hashed_password=hash_password("test-password"),
        is_active=True,
        is_admin=False,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def auth_headers(test_user):
    """Authorization header with a valid Bearer token for test_user."""
    from AINDY.services.auth_service import create_access_token

    token = create_access_token({"sub": str(test_user.id)})
    return {"Authorization": f"Bearer {token}"}


# ── HTTP client ───────────────────────────────────────────────────────────────

@pytest.fixture
def client(runtime_only_client):
    """Integration-tier HTTP client backed by the runtime-only FastAPI app."""
    return runtime_only_client
