"""A caller's identity comes from authentication, whatever the payload or flow state says.

Each boundary is driven through its real entry, with a same-shape control beside it, because a
refusal test passes just as well on a path that has stopped working.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from tests.fixtures.db import build_private_engine

pytestmark = pytest.mark.runtime_only

CALLER = str(uuid.uuid4())
OTHER = str(uuid.uuid4())


@pytest.fixture
def factory(tmp_path, monkeypatch):
    engine = build_private_engine(tmp_path / "identity.db")
    session_factory = sessionmaker(
        bind=engine, autocommit=False, autoflush=False, expire_on_commit=False
    )
    monkeypatch.setattr("AINDY.db.database.SessionLocal", session_factory)
    monkeypatch.setattr(
        "AINDY.platform_layer.async_job_service.SessionLocal", session_factory, raising=False
    )
    monkeypatch.delenv("AINDY_PAYLOAD_IDENTITY_DISPATCH", raising=False)
    monkeypatch.setattr(
        "AINDY.agents.tool_registry._ensure_tools_loaded", lambda *a, **k: None, raising=False
    )
    try:
        yield session_factory
    finally:
        engine.dispose()


@pytest.fixture
def probe_job(monkeypatch):
    from AINDY.platform_layer import async_job_service as jobs

    seen: list = []

    def handler(payload, db):
        seen.append(payload.get("user_id"))
        return {"ok": True}

    monkeypatch.setitem(jobs._JOB_REGISTRY, "probe.job", handler)
    return seen


def _submit_payload(user_id):
    return {"task_name": "probe.job", "payload": {"user_id": user_id}}


def _guest(script, **context):
    from AINDY.runtime import nodus_worker

    return nodus_worker.run_one(
        {
            "script": script,
            "state": {},
            "context": {
                "user_id": CALLER,
                "execution_unit_id": str(uuid.uuid4()),
                "trace_id": "t",
                **context,
            },
        }
    )


# ── a forwarded payload may not name another user ───────────────────────────────────────────


def test_a_guest_script_cannot_submit_a_job_as_another_user(factory, probe_job):
    script = (
        f'let r = sys("sys.v1.job.submit", {{"task_name": "probe.job", '
        f'"payload": {{"user_id": "{OTHER}"}}}})\nset_state("r", r)\n'
    )

    reply = _guest(script)

    assert reply["status"] == "success", reply
    assert probe_job == []
    assert reply["output_state"]["r"]["status"] == "error"
    assert "TENANT_VIOLATION" in reply["output_state"]["r"]["error"]


def test_control_a_guest_script_submits_a_job_as_itself(factory, probe_job):
    script = (
        f'let r = sys("sys.v1.job.submit", {{"task_name": "probe.job", '
        f'"payload": {{"user_id": "{CALLER}"}}}})\nset_state("r", r)\n'
    )

    reply = _guest(script)

    assert reply["output_state"]["r"]["status"] == "success", reply
    assert probe_job == [CALLER]


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("sys.v1.job.submit", "payload"),
        ("sys.v1.flow.run", "initial_state"),
        ("sys.v1.flow.execute_intent", "intent_data"),
        ("sys.v1.nodus.execute", "input_payload"),
    ],
)
def test_the_dispatcher_refuses_a_forwarded_user_id_for_each_syscall(name, key, factory):
    from AINDY.kernel.syscall_dispatcher import dispatch_syscall
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY

    required = SYSCALL_REGISTRY[name].input_schema.get("required") or []
    payload = {field: "x" for field in required if field != key}
    payload[key] = {"user_id": OTHER}

    result = dispatch_syscall(
        name, payload, user_id=CALLER, capability=SYSCALL_REGISTRY[name].capability,
        execution_unit_id=str(uuid.uuid4()),
    )

    assert result["status"] == "error"
    assert "TENANT_VIOLATION" in result["error"]


def test_the_transport_refuses_even_when_the_dispatcher_only_observes(factory, probe_job, monkeypatch):
    """`observe` relaxes in-process callers. A script is not one."""
    from AINDY.runtime.nodus_worker import dispatch_worker_syscall

    monkeypatch.setenv("AINDY_PAYLOAD_IDENTITY_DISPATCH", "observe")

    result = dispatch_worker_syscall("sys.v1.job.submit", _submit_payload(OTHER), user_id=CALLER)

    assert result["status"] == "error"
    assert probe_job == []


def test_the_same_uuid_in_another_spelling_is_not_a_mismatch(factory, probe_job):
    from AINDY.runtime.nodus_worker import dispatch_worker_syscall

    result = dispatch_worker_syscall(
        "sys.v1.job.submit", _submit_payload(CALLER.upper()), user_id=CALLER
    )

    assert result["status"] == "success", result
    assert len(probe_job) == 1


# ── a token ties a run to its user ──────────────────────────────────────────────────────────


def _seed_step(factory, *, owner=OTHER):
    from AINDY.db.models import AgentRun, AgentStep

    run_id = uuid.uuid4()
    db = factory()
    db.add(
        AgentRun(id=run_id, user_id=uuid.UUID(owner), goal="g", status="executing",
                 overall_risk="low", steps_total=1)
    )
    db.flush()
    db.add(
        AgentStep(run_id=run_id, step_index=0, tool_name="t", tool_args={}, risk_level="low",
                  status="success", result={"secret": "step result"}, description="d")
    )
    db.commit()
    db.close()
    return str(run_id)


def _token(run_id, user_id, *, expired=False):
    from AINDY.agents.capability_service import _token_hash

    now = datetime.now(timezone.utc)
    issued = (now - timedelta(hours=2)).isoformat()
    expires = (now - timedelta(hours=1) if expired else now + timedelta(hours=1)).isoformat()
    fields = {
        "execution_token": "exec",
        "issued_at": issued,
        "expires_at": expires,
        "approval_mode": "auto",
        # Names this process has not registered, as a Nodus worker hasn't when the seam checks.
        "granted_tools": ["not.registered.here"],
        "allowed_capabilities": ["not_registered_here"],
    }
    return {
        **fields,
        "run_id": run_id,
        "user_id": user_id,
        "token_hash": _token_hash(run_id=run_id, user_id=user_id, **fields),
    }


READ_STEP = 'let r = call_tool("t", {}, 0)\nset_state("r", r)\n'


def test_a_junk_token_cannot_read_another_users_step(factory):
    run_id = _seed_step(factory)

    reply = _guest(READ_STEP, run_id=run_id, execution_token={"junk": 1}, continuation=True)

    assert "step result" not in str(reply.get("output_state"))
    assert reply["output_state"]["r"]["failure_class"] == "permission"


def test_a_token_for_another_users_run_cannot_read_this_one(factory):
    run_id = _seed_step(factory)
    mine = _seed_step(factory, owner=CALLER)

    reply = _guest(READ_STEP, run_id=run_id, execution_token=_token(mine, CALLER), continuation=True)

    assert "step result" not in str(reply.get("output_state"))


def test_control_the_owner_replays_their_own_step(factory):
    run_id = _seed_step(factory, owner=CALLER)

    reply = _guest(READ_STEP, run_id=run_id, execution_token=_token(run_id, CALLER), continuation=True)

    assert reply["output_state"]["r"]["result"] == {"secret": "step result"}, reply
    assert reply["output_state"]["r"]["replayed"] is True


def test_the_owner_still_replays_after_their_token_expires(factory):
    """A crash re-drive may outlive the token. Replay runs no tool, so expiry isn't its question."""
    run_id = _seed_step(factory, owner=CALLER)

    reply = _guest(
        READ_STEP, run_id=run_id, execution_token=_token(run_id, CALLER, expired=True),
        continuation=True,
    )

    assert reply["output_state"]["r"]["replayed"] is True, reply


def test_a_junk_token_cannot_overwrite_another_users_step(factory):
    from AINDY.db.models import AgentStep

    run_id = _seed_step(factory)

    _guest('let r = call_tool("mine", {"a": 1}, 0)\n', run_id=run_id, execution_token={"junk": 1})

    db = factory()
    row = db.query(AgentStep).filter(AgentStep.step_index == 0).one()
    assert (row.tool_name, row.status) == ("t", "success")
    db.close()


def test_a_tampered_token_is_not_bound():
    from AINDY.agents.capability_service import token_binds_run

    run_id = str(uuid.uuid4())
    token = _token(run_id, CALLER)
    assert token_binds_run(token, run_id, CALLER) is True

    assert token_binds_run({**token, "approval_mode": "manual"}, run_id, CALLER) is False
    assert token_binds_run({**token, "granted_tools": ["more"]}, run_id, CALLER) is False
    assert token_binds_run({**token, "expires_at": "2099-01-01T00:00:00+00:00"}, run_id, CALLER) is False
    assert token_binds_run({**token, "user_id": OTHER}, run_id, OTHER) is False
    assert token_binds_run(token, run_id, OTHER) is False
    assert token_binds_run(None, run_id, CALLER) is False


# ── flow state may not claim a run ──────────────────────────────────────────────────────────


def _run_node(factory, monkeypatch, state):
    from AINDY.runtime import nodus_adapter

    seen = {}

    class Result:
        status = "success"
        output_state: dict = {}
        emitted_events: list = []
        memory_writes: list = []
        error = None
        simulated_effects: list = []
        stdout_log = ""
        result = None

        def __getattr__(self, _):
            return None

    def capture(*_args, **kwargs):
        seen.update(kwargs)
        return Result()

    monkeypatch.setattr(nodus_adapter, "execute_nodus_runtime", capture)
    db = factory()
    try:
        nodus_adapter.nodus_execute_node(
            {"nodus_script": 'set_state("x", 1)', **state},
            {"db": db, "user_id": CALLER, "run_id": str(uuid.uuid4()), "trace_id": "t",
             "attempts": {}},
        )
    finally:
        db.close()
    return seen


def test_flow_state_cannot_claim_another_users_run(factory, monkeypatch):
    target = _seed_step(factory)

    seen = _run_node(
        factory, monkeypatch,
        {"agent_run_id": target, "execution_token": {"junk": 1}, "__continuation": True},
    )

    assert seen["run_id"] != target
    assert seen["execution_token"] is None
    assert seen["continuation"] is False


def test_flow_state_cannot_claim_a_run_with_no_token(factory, monkeypatch):
    target = _seed_step(factory)

    seen = _run_node(factory, monkeypatch, {"agent_run_id": target})

    assert seen["run_id"] != target


def test_control_flow_state_keeps_a_run_its_token_is_bound_to(factory, monkeypatch):
    run_id = _seed_step(factory, owner=CALLER)
    token = _token(run_id, CALLER)

    seen = _run_node(
        factory, monkeypatch,
        {"agent_run_id": run_id, "execution_token": token, "__continuation": True},
    )

    assert seen["run_id"] == run_id
    assert seen["execution_token"] == token
    assert seen["continuation"] is True


def test_control_flow_state_keeps_the_callers_own_run_with_no_token(factory, monkeypatch):
    """The agent preview runs this way when it couldn't mint a token."""
    run_id = _seed_step(factory, owner=CALLER)

    seen = _run_node(factory, monkeypatch, {"agent_run_id": run_id})

    assert seen["run_id"] == run_id


# ── execution units ─────────────────────────────────────────────────────────────────────────


def test_a_unit_is_not_reused_across_users(factory):
    from AINDY.core.execution_gate import require_execution_unit

    db = factory()
    args = {"db": db, "eu_type": "job", "source_type": "probe", "source_id": "shared-name",
            "correlation_id": "c"}
    theirs = require_execution_unit(user_id=OTHER, **args)
    assert theirs is not None

    assert require_execution_unit(user_id=CALLER, **args) is None
    assert require_execution_unit(user_id=OTHER, **args).id == theirs.id
    db.close()


def test_the_same_task_name_gives_each_user_their_own_source():
    from AINDY.runtime.nodus_execution_service import _user_scoped_source_id

    assert _user_scoped_source_id(CALLER, "memory.nodus.t") != _user_scoped_source_id(
        OTHER, "memory.nodus.t"
    )
    long_name = "x" * 500
    assert len(_user_scoped_source_id(CALLER, long_name)) <= 128
    assert _user_scoped_source_id(CALLER, long_name) != _user_scoped_source_id(OTHER, long_name)
    assert _user_scoped_source_id(CALLER, long_name) != _user_scoped_source_id(CALLER, "y" * 500)


# ── an identity that does not parse ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("identity", ["not-a-uuid", "None"])
def test_an_unparseable_identity_reads_nothing(identity, factory):
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO
    from AINDY.kernel.syscall_dispatcher import dispatch_syscall

    path = f"/memory/{OTHER}/probe/note/{uuid.uuid4()}"
    db = factory()
    node = MemoryNodeDAO(db).save(
        content="their note", user_id=OTHER, node_type="insight", generate_embedding=False, path=path
    )
    db.commit()

    for name, payload in (
        ("sys.v1.memory.list", {"path": f"/memory/{OTHER}/**"}),
        ("sys.v1.memory.tree", {"path": f"/memory/{OTHER}/**"}),
        ("sys.v1.memory.trace", {"path": path}),
    ):
        result = dispatch_syscall(
            name, payload, user_id=identity, capability="memory.read",
            execution_unit_id=str(uuid.uuid4()),
        )
        assert result["status"] == "success", result
        assert "their note" not in str(result["data"]), name

    assert MemoryNodeDAO(db).get_by_id(node["id"], user_id=identity) is None
    assert MemoryNodeDAO(db).delete_by_id(node["id"], user_id=identity) is False
    db.close()


def test_control_the_owner_and_trusted_code_still_read(factory):
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

    path = f"/memory/{OTHER}/probe/note/{uuid.uuid4()}"
    db = factory()
    node = MemoryNodeDAO(db).save(
        content="their note", user_id=OTHER, node_type="insight", generate_embedding=False, path=path
    )
    db.commit()

    assert MemoryNodeDAO(db).get_by_path(path, user_id=OTHER)["id"] == node["id"]
    assert MemoryNodeDAO(db).get_by_path(path)["id"] == node["id"]
    assert MemoryNodeDAO(db).get_by_path(path, user_id=CALLER) is None
    db.close()
