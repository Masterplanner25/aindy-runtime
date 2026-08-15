"""A deactivated or drifted platform system agent must have a way back.

`_bootstrap_system_agents` claimed "idempotent upsert by memory_namespace" in its
docstring and was insert-only in its body. So once a system row drifted from the
platform spec, every subsequent boot walked straight past it — the roster had no repair
path at all. FR-12 recorded the consequence and left it open: an admin can deactivate a
platform system agent, `flow_definitions_memory` filters on `is_active`, and nothing
restores it.

Closing the FR-12 hole made that sharper rather than milder. `POST /admin/agents/register`
was the only surface whose update branch set `is_active = True`, and reserving the seven
system namespaces (correctly) also removed the only accidental route back for exactly
the rows that matter most.

The split this file pins:

  * **Identity** (name / agent_type / description) is platform-owned, so boot repairs it.
  * **`is_active` is not repaired at boot**, because silently re-enabling an agent an
    operator deactivated trades a missing repair path for an unpredictable one. Boot
    warns; `POST /admin/agents/{namespace}/restore` is the repair, and needs no restart.

Whether an admin *should* be able to deactivate a system agent stays a policy question.
These tests fix the mechanism, not the policy.

Marked `runtime_only` — without it CI collects nothing here (CI-MARKER-1).
"""
from __future__ import annotations

import uuid

import pytest

from AINDY.db.models.agent import Agent, SYSTEM_AGENTS, SYSTEM_AGENT_SPECS
from AINDY.services.auth_service import require_admin_principal

pytestmark = pytest.mark.runtime_only

_ADMIN = {
    "user_id": "00000000-0000-0000-0000-000000000001",
    "is_admin": True,
    "auth_type": "jwt",
}

RUNTIME_SPEC = next(s for s in SYSTEM_AGENT_SPECS if s["namespace"] == "runtime")


def _seed(db, spec, *, name=None, description=None, active=True) -> Agent:
    agent = Agent(
        id=str(uuid.uuid4()),
        name=name or spec["name"],
        memory_namespace=spec["namespace"],
        agent_type=spec["agent_type"],
        description=description if description is not None else spec["description"],
        is_active=active,
    )
    db.add(agent)
    db.commit()
    return agent


def _run_bootstrap(db, monkeypatch):
    """Run the boot seed against the test session.

    Patch `AINDY.startup.SessionLocal`, **not** `AINDY.db.database.SessionLocal`. This is
    the inverse of the scheduler-job rule: those import `SessionLocal` inside the function
    body, so they must be patched at the source, while `startup.py` binds it at module
    level (line 56) and never re-reads it. Patching the source here silently does nothing
    — the seed runs against the real engine and reports "no such table: agents" as a
    non-fatal warning, so the test fails on its assertion rather than on the patch.
    """
    from AINDY import startup

    monkeypatch.setattr(startup, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None, raising=False)
    startup._bootstrap_system_agents()


class TestOneRoster:
    def test_the_reserved_set_is_derived_from_the_spec_list(self):
        """Two lists describing one roster is a drift waiting to happen: the set decided
        what an app may not register, the list decided what the platform does register,
        and nothing made them agree."""
        assert SYSTEM_AGENTS == {spec["namespace"] for spec in SYSTEM_AGENT_SPECS}

    def test_the_roster_is_still_the_documented_seven(self):
        assert {s["namespace"] for s in SYSTEM_AGENT_SPECS} == {
            "arm", "genesis", "nodus", "sylva", "platform", "runtime", "memory",
        }

    def test_startup_no_longer_carries_its_own_copy(self):
        """The seed used to hardcode a private `_SYSTEM_AGENTS` list of seven dicts."""
        from pathlib import Path

        source = Path("AINDY/startup.py").read_text(encoding="utf-8")
        assert "SYSTEM_AGENT_SPECS" in source
        assert '"namespace": "arm"' not in source


class TestBootRepair:
    def test_seeds_a_missing_row(self, db_session, monkeypatch):
        _run_bootstrap(db_session, monkeypatch)
        assert db_session.query(Agent).filter(
            Agent.memory_namespace == "runtime"
        ).one().name == RUNTIME_SPEC["name"]

    def test_repairs_a_rewritten_identity(self, db_session, monkeypatch):
        """The FR-12 defect's residue. The route can no longer rewrite these rows, but a
        deployment that ran the old build — or a raw UPDATE — still can have."""
        _seed(db_session, RUNTIME_SPEC, name="Hijacked", description="not ours")

        _run_bootstrap(db_session, monkeypatch)

        row = db_session.query(Agent).filter(Agent.memory_namespace == "runtime").one()
        assert row.name == RUNTIME_SPEC["name"]
        assert row.description == RUNTIME_SPEC["description"]

    def test_repair_is_logged_not_silent(self, db_session, monkeypatch, caplog):
        _seed(db_session, RUNTIME_SPEC, name="Hijacked")
        with caplog.at_level("WARNING"):
            _run_bootstrap(db_session, monkeypatch)
        assert any("Repaired system agent" in r.getMessage() for r in caplog.records)

    def test_an_untouched_row_is_left_alone(self, db_session, monkeypatch, caplog):
        _seed(db_session, RUNTIME_SPEC)
        with caplog.at_level("WARNING"):
            _run_bootstrap(db_session, monkeypatch)
        assert not any("Repaired system agent" in r.getMessage() for r in caplog.records)

    def test_boot_does_not_reactivate_a_deactivated_system_agent(self, db_session, monkeypatch):
        """The deliberate half. Re-enabling on restart would undo an operator action
        without telling anyone — a repair path you cannot predict is not an improvement."""
        _seed(db_session, RUNTIME_SPEC, active=False)

        _run_bootstrap(db_session, monkeypatch)

        row = db_session.query(Agent).filter(Agent.memory_namespace == "runtime").one()
        assert row.is_active is False

    def test_boot_warns_about_a_deactivated_system_agent(self, db_session, monkeypatch, caplog):
        """Not repairing it is only defensible if the condition is visible."""
        _seed(db_session, RUNTIME_SPEC, active=False)
        with caplog.at_level("WARNING"):
            _run_bootstrap(db_session, monkeypatch)

        messages = " ".join(r.getMessage() for r in caplog.records)
        assert "DEACTIVATED" in messages
        assert "restore" in messages, "the warning must name the way back"


class TestRestoreEndpoint:
    @pytest.fixture(autouse=True)
    def _as_admin(self, runtime_only_app):
        runtime_only_app.dependency_overrides[require_admin_principal] = lambda: _ADMIN

    def test_restores_a_deactivated_system_agent(self, runtime_only_client, mock_db):
        _seed(mock_db, RUNTIME_SPEC, active=False)

        response = runtime_only_client.post("/platform/admin/agents/runtime/restore")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["is_active"] is True
        assert body["was_active"] is False

    def test_restore_also_repairs_a_drifted_system_identity(self, runtime_only_client, mock_db):
        """One call fully repairs the row, rather than leaving it active-but-wrong until
        the next boot."""
        _seed(mock_db, RUNTIME_SPEC, name="Hijacked", description="not ours", active=False)

        body = runtime_only_client.post("/platform/admin/agents/runtime/restore").json()

        assert body["name"] == RUNTIME_SPEC["name"]
        assert body["description"] == RUNTIME_SPEC["description"]
        assert set(body["repaired_fields"]) == {"name", "description"}

    def test_restore_is_idempotent(self, runtime_only_client, mock_db):
        _seed(mock_db, RUNTIME_SPEC, active=False)
        runtime_only_client.post("/platform/admin/agents/runtime/restore")
        second = runtime_only_client.post("/platform/admin/agents/runtime/restore").json()
        assert second["is_active"] is True
        assert second["was_active"] is True
        assert second["repaired_fields"] == []

    def test_restore_works_for_a_non_system_agent_too(self, runtime_only_client, mock_db):
        """The repair path is not special-cased to the platform's own rows — an
        app-registered agent deactivated by mistake was equally stuck."""
        agent = Agent(
            id=str(uuid.uuid4()), name="Scribe", memory_namespace="scribe",
            agent_type="custom", is_active=False,
        )
        mock_db.add(agent)
        mock_db.commit()

        body = runtime_only_client.post("/platform/admin/agents/scribe/restore").json()
        assert body["is_active"] is True
        assert body["repaired_fields"] == []

    def test_unknown_namespace_is_404(self, runtime_only_client, mock_db):
        assert runtime_only_client.post(
            "/platform/admin/agents/nope/restore"
        ).status_code == 404

    def test_deactivating_a_system_agent_warns_in_the_response(self, runtime_only_client, mock_db):
        """The policy stays open, but the caller is told what they just did and how to
        undo it — a restart will not."""
        _seed(mock_db, RUNTIME_SPEC)

        body = runtime_only_client.delete("/platform/admin/agents/runtime").json()

        assert body["is_active"] is False
        assert "restore" in body.get("warning", "")

    def test_deactivating_an_ordinary_agent_carries_no_warning(self, runtime_only_client, mock_db):
        agent = Agent(
            id=str(uuid.uuid4()), name="Scribe", memory_namespace="scribe",
            agent_type="custom", is_active=True,
        )
        mock_db.add(agent)
        mock_db.commit()

        body = runtime_only_client.delete("/platform/admin/agents/scribe").json()
        assert "warning" not in body
