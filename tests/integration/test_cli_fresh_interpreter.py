"""FR-62: an operator command run the way an operator runs it, in a fresh interpreter.

`aindy-runtime memory backfill-paths --yes` failed on its first write: nothing in a fresh CLI
process had imported `users` or `system_events`, and a flush resolves every foreign key on
`memory_nodes`. Its dry run only counts, so it worked, and so did every test, because the test
process had already imported the whole model registry. Only a new interpreter shows it.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.fixture
def pg_url():
    url = os.getenv("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("runs the command against the live Postgres the integration job provides")
    return url


def _run(url: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, DATABASE_URL=url)
    return subprocess.run(
        [sys.executable, "-m", "AINDY.runtime_only", *args],
        capture_output=True, text=True, env=env, timeout=300,
    )


def test_backfill_paths_writes_from_a_fresh_interpreter(pg_url):
    from AINDY.db.models.user import User
    from AINDY.memory.memory_persistence import MemoryNodeModel

    engine = create_engine(pg_url)
    with Session(engine) as s:
        user = User(email=f"cli-{uuid.uuid4().hex[:8]}@aindy.test", username=f"cli-{uuid.uuid4().hex[:8]}",
                    hashed_password="x", is_active=True)
        s.add(user)
        s.flush()
        node = MemoryNodeModel(content="fresh interpreter", tags=[], node_type="insight",
                               memory_type="insight", user_id=user.id, path=None)
        s.add(node)
        s.commit()
        node_id, user_id = node.id, user.id
    try:
        dry = _run(pg_url, "memory", "backfill-paths")
        assert dry.returncode == 0, dry.stderr
        assert "would get a stored path" in dry.stdout, "control: the node is there to fill"

        done = _run(pg_url, "memory", "backfill-paths", "--yes")
        assert done.returncode == 0, done.stderr[-2000:]
        assert "ok: stored a path on" in done.stdout

        with engine.connect() as c:
            path = c.execute(text("SELECT path FROM memory_nodes WHERE id = :i"), {"i": node_id}).scalar_one()
        assert path and str(user_id) in path
    finally:
        with engine.begin() as c:
            c.execute(text("DELETE FROM memory_nodes WHERE id = :i"), {"i": node_id})
            c.execute(text("DELETE FROM users WHERE id = :i"), {"i": user_id})
        engine.dispose()


def test_backfill_call_users_writes_from_a_fresh_interpreter(pg_url):
    """FR-61's one-time command, run as the operator runs it."""
    from AINDY.db.models.user import User

    engine = create_engine(pg_url)
    trace = f"fr61-{uuid.uuid4().hex[:10]}"
    with Session(engine) as s:
        user = User(email=f"cli-{uuid.uuid4().hex[:8]}@aindy.test", username=f"cli-{uuid.uuid4().hex[:8]}",
                    hashed_password="x", is_active=True)
        s.add(user)
        s.commit()
        user_id = user.id
    with engine.begin() as c:
        for kind, owner in (("flow.started", user_id), ("external.call.completed", None)):
            c.execute(
                text("INSERT INTO system_events (id, type, user_id, trace_id, payload, timestamp) "
                     "VALUES (:id, :t, :u, :tr, '{}', now())"),
                {"id": uuid.uuid4(), "t": kind, "u": owner, "tr": trace},
            )
    try:
        done = _run(pg_url, "events", "backfill-call-users", "--yes")
        assert done.returncode == 0, done.stderr[-2000:]
        with engine.connect() as c:
            owner = c.execute(
                text("SELECT user_id FROM system_events WHERE trace_id = :tr AND type = 'external.call.completed'"),
                {"tr": trace},
            ).scalar_one()
        assert owner == user_id, done.stdout
    finally:
        with engine.begin() as c:
            c.execute(text("DELETE FROM system_events WHERE trace_id = :tr"), {"tr": trace})
            c.execute(text("DELETE FROM users WHERE id = :i"), {"i": user_id})
        engine.dispose()
