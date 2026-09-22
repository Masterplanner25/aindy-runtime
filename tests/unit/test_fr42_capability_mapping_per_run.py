"""FR-42 / DEC-071 — the capability-mapping audit row is owed per RUN; a scope that is not an
`AgentRun` gets the agent-type rows and no run rows, deliberately, and the token says so.

Found by SUBSTRATE-WITNESS-1's first live run: `mint_token` → `create_run_capability_mappings`
inserted `AgentCapabilityMapping(agent_run_id=<claw session>)`, `agent_run_id` is a FK to
`agent_runs.id`, the insert violated it, the broad `except` logged a WARNING and the mint went
on — so every non-agent consumer's audit trail was silently empty, reported as a failure.

The control is the agent case: the same call with a real `AgentRun` row writes the run rows and
reports `mapping_recorded: True`. Mutation: drop the `run_is_agent_run` guard → the non-agent
test fails on the FK (SQLite enforces it here) or on `mapping_recorded`.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.runtime_only


def _capability(db, name="channel.send"):
    from AINDY.db.models.capability import Capability

    row = Capability(name=name, description="d", risk_level="medium")
    db.add(row)
    db.commit()
    return row


def _agent_run(db):
    from AINDY.db.models import AgentRun

    run = AgentRun(id=uuid.uuid4(), user_id=uuid.uuid4(), goal="g", status="executing", steps_total=1,
                   plan={"steps": []})
    db.add(run)
    db.commit()
    return run


def _mapping_rows(db):
    from AINDY.db.models.capability import AgentCapabilityMapping

    return db.query(AgentCapabilityMapping).all()


def test_an_agent_run_gets_run_rows_and_reports_recorded(db_session):
    from AINDY.agents import capability_service as cap

    _capability(db_session)
    run = _agent_run(db_session)
    recorded = cap.create_run_capability_mappings(str(run.id), "default", ["channel.send"], db_session)
    rows = _mapping_rows(db_session)
    assert recorded is True
    assert sorted((r.agent_type or "", str(r.agent_run_id or "")) for r in rows) == [("", str(run.id)), ("default", "")]


def test_a_non_agent_scope_gets_type_rows_only_and_says_so(db_session, caplog):
    from AINDY.agents import capability_service as cap

    _capability(db_session)
    session_scope = str(uuid.uuid5(uuid.NAMESPACE_URL, "claw://session/s1"))  # a UUID, not an AgentRun
    recorded = cap.create_run_capability_mappings(session_scope, "default", ["channel.send"], db_session)
    rows = _mapping_rows(db_session)
    assert recorded is False
    assert [(r.agent_type, r.agent_run_id) for r in rows] == [("default", None)], rows
    assert not [r for r in caplog.records if "create_run_capability_mappings failed" in r.getMessage()], (
        "the non-agent scope must be a decision, never a caught foreign-key violation"
    )


def test_the_token_carries_mapping_recorded(db_session):
    """Through the real mint, for both scopes, with the tool/capability lookups stubbed."""
    from AINDY.agents import capability_service as cap

    _capability(db_session, "cap.a")
    run = _agent_run(db_session)
    with (
        patch.object(cap, "get_grantable_tools", return_value=["toolA"]),
        patch.object(cap, "get_plan_required_capabilities", return_value=["cap.a"]),
        patch.object(cap, "_get_capabilities_for_tool", return_value=["cap.a"]),
    ):
        agent_token = cap.mint_token(run_id=str(run.id), user_id=str(run.user_id),
                                     plan={"steps": [{"tool": "toolA"}]}, db=db_session, approval_mode="manual")
        session_token = cap.mint_token(run_id=str(uuid.uuid4()), user_id=str(run.user_id),
                                       plan={"steps": [{"tool": "toolA"}]}, db=db_session, approval_mode="manual")
    assert agent_token is not None and agent_token["mapping_recorded"] is True
    assert session_token is not None and session_token["mapping_recorded"] is False
    # informational, OUTSIDE the HMAC: the hash is computed from the same fields as before, so a
    # token minted by a runtime without this key validates against one with it and vice versa
    import inspect

    assert "mapping_recorded" not in inspect.signature(cap._token_hash).parameters
    assert "mapping_recorded" not in inspect.signature(cap._token_hash_matches).parameters
