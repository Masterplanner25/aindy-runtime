"""TOOL-SEAM-ISOLATION-1 step C2 — a declared tool actually runs out of process.

Steps A and B narrowed one argument and let a tool *declare* a boundary. This is the first thing
in this entry that **applies** one.

★ The property that matters most: **there is no fallback.**
--------------------------------------------------------
The Nodus adapter deliberately falls back — a warm-pool failure spills to a fresh subprocess —
because there both paths give the *same* guarantee and falling back is strictly better than
failing. **Here they do not.** Falling back would run a tool that asked to be confined
**unconfined**, which is precisely the *"gated path that does not actually confine"* failure this
entry exists to prevent. A worker that crashes, times out, or cannot be spawned means the tool
does not run.

Half these tests exist to prove that absence, and an absence is only worth asserting alongside a
liveness control — so one runs first.

★ A worker has its own registry
-------------------------------
A subprocess starts with an empty ``TOOL_REGISTRY`` and rebuilds it from the plugin stack. A tool
registered ad hoc in the parent — as tests do — is **not** visible there. That is a real
deployment constraint, not a test artefact, and the worker says so in its error rather than
reporting a generic failure.
"""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock

import pytest

from AINDY.agents.tool_registry import TOOL_REGISTRY, execute_tool, register_tool
from AINDY.agents.tool_worker import run_one
from AINDY.core.execution_environment import ASSURANCE_INSECURE_DEV

pytestmark = pytest.mark.runtime_only

_PROBE = "test.c2_probe"


@pytest.fixture(autouse=True)
def _enforced(monkeypatch):
    monkeypatch.setenv("AINDY_TOOL_ISOLATION", "1")
    monkeypatch.setattr(
        "AINDY.core.execution_environment._host_assurance",
        lambda: (ASSURANCE_INSECURE_DEV, "insecure-dev/test"),
    )
    yield
    TOOL_REGISTRY.pop(_PROBE, None)


def _register(isolation=ASSURANCE_INSECURE_DEV):
    ran: list[int] = []

    def _fn(args, user_id, db):
        ran.append(1)
        return {"ok": True}

    register_tool(
        _PROBE,
        risk="low",
        description="probe",
        capability=f"tool:{_PROBE}",
        required_capability="read_memory",
        category="test",
        egress_scope="internal",
        isolation=isolation,
    )(_fn)
    return ran


def _invoke():
    return execute_tool(
        _PROBE, {}, user_id="u1", db=MagicMock(), run_id=None, execution_token=None
    )


# ── Liveness control ─────────────────────────────────────────────────────────


def test_liveness_the_worker_can_execute_a_tool():
    """★ Drives the real worker entry point in-process. If this fails, every 'did not run
    in-process' assertion below is satisfied by a boundary that executes nothing at all."""
    register_tool(
        _PROBE,
        risk="low",
        description="probe",
        capability=f"tool:{_PROBE}",
        required_capability="read_memory",
        category="test",
        egress_scope="internal",
    )(lambda args, user_id, db: {"echo": args.get("x")})

    response = run_one({"tool_name": _PROBE, "args": {"x": 7}, "user_id": "u1"})

    assert response["ok"] is True, response
    assert response["result"] == {"echo": 7}


# ── The boundary applies ─────────────────────────────────────────────────────


def test_a_declared_tool_does_not_run_in_this_process():
    """★ The point of the whole entry: the parent's copy of the function is never called.

    The worker cannot see a test-registered tool, so this refuses — and the refusal is itself the
    evidence, because an in-process execution would have succeeded.
    """
    ran = _register()

    result = _invoke()

    assert ran == [], (
        "the tool executed in the runtime process despite declaring isolation — the declaration "
        "is decorative and step C2 is not applied"
    )
    assert result["success"] is False


def test_the_worker_names_a_registry_mismatch_rather_than_failing_generically():
    """A subprocess rebuilds `TOOL_REGISTRY` from the plugin stack, so an ad-hoc parent
    registration is invisible there. That is a deployment constraint operators will hit, and a
    generic 'worker failed' would send them looking in the wrong place."""
    response = run_one({"tool_name": "definitely.not.registered", "args": {}, "user_id": "u"})

    assert response["ok"] is False
    assert "not registered in this worker" in response["error"]
    assert "deployment problem" in response["error"]


def test_an_undeclared_tool_still_runs_in_process():
    """C2 is opt-in per tool. The subprocess round-trip is real latency and must not be imposed
    on tools that never asked for a boundary."""
    register_tool(
        _PROBE,
        risk="low",
        description="probe",
        capability=f"tool:{_PROBE}",
        required_capability="read_memory",
        category="test",
        egress_scope="internal",
    )(lambda args, user_id, db: {"ok": True})

    assert _invoke()["success"] is True


def test_enforcement_can_be_disabled_without_disabling_the_declaration(monkeypatch):
    """`AINDY_TOOL_ISOLATION=0` reverts to declare-and-refuse. The declaration is still
    validated and still refused when the host cannot meet it — only application stops."""
    monkeypatch.setenv("AINDY_TOOL_ISOLATION", "0")
    ran = _register()

    assert _invoke()["success"] is True
    assert ran == [1], "with enforcement off a satisfiable declaration must run in-process"


# ── No fallback ──────────────────────────────────────────────────────────────


def test_a_worker_that_cannot_start_refuses_rather_than_running_locally(monkeypatch):
    """★ THE assertion. Falling back would run a tool that asked to be confined UNCONFINED."""
    import subprocess

    def _no_spawn(*a, **kw):
        raise OSError("cannot fork")

    monkeypatch.setattr(subprocess, "run", _no_spawn)
    ran = _register()

    result = _invoke()

    assert result["success"] is False
    assert ran == [], "the tool ran in-process after its worker failed to start — no fallback"
    assert "refused" in result["error"]


def test_a_worker_timeout_refuses_rather_than_retrying_locally(monkeypatch):
    """A slow confined tool must not be quietly re-run without the boundary."""
    import subprocess

    def _timeout(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="tool_worker", timeout=1)

    monkeypatch.setattr(subprocess, "run", _timeout)
    ran = _register()

    result = _invoke()

    assert result["success"] is False
    assert ran == []
    assert "NOT retried in-process" in result["error"]


def test_a_crashed_worker_refuses_and_says_it_crashed(monkeypatch):
    """★ Strengthened after mutation testing, and the reason is worth keeping.

    The first version asserted only ``success is False``. Bypassing the crash check entirely
    **survived** it, because a downstream guard still refused — an empty stdout fails
    ``json.loads`` and lands in the unreadable-response branch. The tool was still safe (defence
    in depth working), but the test could not tell the two paths apart.

    The distinction is operator-facing: "worker exited 1" sends you to the worker's stderr,
    "unreadable response" sends you to the protocol. Asserting the exit code makes the check that
    produces that message load-bearing rather than decorative.
    """
    import subprocess

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: MagicMock(returncode=1, stdout="", stderr="boom"),
    )
    ran = _register()

    result = _invoke()

    assert result["success"] is False
    assert ran == []
    assert "exit 1" in result["error"], (
        "a crashed worker must be reported as a crash, not as an unreadable response — they "
        "send an operator to different places"
    )


def test_an_unreadable_worker_response_refuses(monkeypatch):
    import subprocess

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: MagicMock(returncode=0, stdout="not json", stderr=""),
    )
    ran = _register()

    assert _invoke()["success"] is False
    assert ran == []


# ── Marshalling ──────────────────────────────────────────────────────────────


def test_a_result_that_does_not_marshal_fails_in_the_worker():
    """★ Where C1's counter earns its keep. In-process a non-marshalling return is COUNTED, because
    the effect has landed and rejecting would discard it. Here it must FAIL — the value cannot
    cross the pipe, so there is nothing to carry back. C1 exists so this is known in advance."""
    import uuid

    register_tool(
        _PROBE,
        risk="low",
        description="probe",
        capability=f"tool:{_PROBE}",
        required_capability="read_memory",
        category="test",
        egress_scope="internal",
    )(lambda args, user_id, db: {"id": uuid.uuid4()})

    response = run_one({"tool_name": _PROBE, "args": {}, "user_id": "u"})

    assert response["ok"] is False
    assert "does not marshal" in response["error"]
    assert "aindy_tool_return_contract_violations_total" in response["error"], (
        "the error must point at the counter that would have predicted this"
    )


def test_the_worker_passes_db_as_none():
    """Measured, not assumed: 18 of 18 tool functions take `db` and none uses it. A session
    cannot cross a process boundary, so a tool needing data reaches through a syscall."""
    seen: dict = {}

    register_tool(
        _PROBE,
        risk="low",
        description="probe",
        capability=f"tool:{_PROBE}",
        required_capability="read_memory",
        category="test",
        egress_scope="internal",
    )(lambda args, user_id, db: seen.update(db_was=db) or {"ok": True})

    run_one({"tool_name": _PROBE, "args": {}, "user_id": "u"})

    assert seen["db_was"] is None


def test_the_worker_protocol_round_trips_through_the_real_entry_point():
    """★ Drives `python -m AINDY.agents.tool_worker` as a real subprocess — the only test here
    that proves the module is executable, that stdout carries a parseable frame, and that nothing
    else writes to stdout and corrupts it."""
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-m", "AINDY.agents.tool_worker"],
        input=json.dumps({"tool_name": "definitely.not.registered", "args": {}, "user_id": "u"}),
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert proc.returncode == 0, proc.stderr[-500:]
    response = json.loads(proc.stdout)  # must be the ONLY thing on stdout
    assert response["ok"] is False
    assert "not registered in this worker" in response["error"]
