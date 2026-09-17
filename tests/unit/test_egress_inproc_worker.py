"""EGRESS-INPROC-1 — the egress decision is made once at `execute_tool` and enforced where the
tool runs (DEC-048..051).

The defect: `execute_tool` computed the domain allowlist and entered `egress_scope` around the
IN-PROCESS call only; the isolated branch returned before it, so a tool that declared
`isolation=` — the one the runtime distrusts enough to move out of process — ran with no egress
enforcement at all, flag on or off. The allowlist was computed for it and dropped.

Now `(mode, domains)` is resolved BEFORE the isolation branch; the in-process branch scopes it
as before, the worker installs the same guard PROCESS-GLOBALLY from its request payload (which
also closes the `threading.Thread` contextvar bypass there — pinned open in-process, by the
guard's own docstring), and the envelope + span report which mechanism applied.

No real DNS: the guard's original resolver is stubbed, and a denied host never reaches it.
"""
from __future__ import annotations

import json
import socket
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from AINDY.agents import capability_policy as cp
from AINDY.agents import tool_registry as tr
from AINDY.agents.tool_worker import run_one
from AINDY.core.execution_environment import ASSURANCE_INSECURE_DEV
from AINDY.platform_layer import egress_guard as eg

pytestmark = pytest.mark.runtime_only

_TOOL = "test.egress_probe"


@pytest.fixture(autouse=True)
def _isolated_guard(monkeypatch):
    """The guard is a process-wide install — reset it (and the worker's process-global
    allowlist) around every test, and pretend this host offers `insecure-dev` so a declared
    tool is routed to the worker rather than refused."""
    orig = (socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex)
    eg._installed = False
    eg._orig_getaddrinfo = eg._orig_connect = eg._orig_connect_ex = None
    eg.clear_process_egress()
    monkeypatch.setenv("AINDY_TOOL_ISOLATION", "1")
    monkeypatch.setattr(
        "AINDY.core.execution_environment._host_assurance",
        lambda: (ASSURANCE_INSECURE_DEV, "insecure-dev/test"),
    )
    cp.clear_capability_policies()
    yield
    socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex = orig
    eg._installed = False
    eg._orig_getaddrinfo = eg._orig_connect = eg._orig_connect_ex = None
    eg.clear_process_egress()
    cp.clear_capability_policies()
    tr.TOOL_REGISTRY.pop(_TOOL, None)


def _install_with_stub() -> list:
    """Install the guard and swap its delegate resolver for a no-network stub."""
    eg.install_egress_guard()
    seen: list = []

    def _stub(host, *a, **k):
        seen.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]

    eg._orig_getaddrinfo = _stub
    return seen


def _register(fn, *, isolation=None, env_spec=None):
    tr.TOOL_REGISTRY.pop(_TOOL, None)
    tr.register_tool(
        _TOOL,
        risk="high",
        description="probe",
        capability="outbound.http",
        required_capability="outbound.http",
        category="test",
        egress_scope="web",
        isolation=isolation,
        env_spec=env_spec,
    )(fn)


def _resolve(host):
    def _fn(args, user_id, db):
        socket.getaddrinfo(host, 443)
        return {"resolved": host}

    return _fn


def _authorized_execute(*, worker=None):
    """Drive `execute_tool` with a granted token and a domain policy on `outbound.http`.

    `worker`, when given, replaces the subprocess spawn: it receives the JSON request the
    parent would have piped to `python -m AINDY.agents.tool_worker` and returns the response
    dict — the real `run_one` by default, so the parent → worker path is exercised end to end
    minus the process boundary (a test-registered tool is invisible to a real subprocess).
    """
    cp.register_capability_policy("outbound.http", cp.CapabilityPolicy(domains=("allowed.com",)))
    patches = [
        patch.object(tr, "_ensure_tools_loaded", lambda: None),
        patch(
            "AINDY.agents.capability_service.check_tool_capability",
            return_value={"ok": True, "allowed_capabilities": ["outbound.http"], "granted_tools": [_TOOL]},
        ),
        patch("AINDY.agents.capability_service._get_capabilities_for_tool", return_value=["outbound.http"]),
        patch.object(tr, "queue_system_event", lambda **k: None),
    ]
    captured: dict = {}
    if worker is not None:

        def _fake_spawn(cmd, *, payload, tool_name, run_id, spawn_kwargs):
            request = json.loads(payload)
            captured["request"] = request
            return SimpleNamespace(returncode=0, stdout=json.dumps(worker(request)), stderr="")

        patches.append(patch.object(tr, "_run_worker_or_kill_on_cancel", _fake_spawn))
    for p in patches:
        p.start()
    try:
        result = tr.execute_tool(
            _TOOL, {"note": "no url here"}, "user-1", MagicMock(), run_id="run-1", execution_token={"t": 1}
        )
    finally:
        for p in reversed(patches):
            p.stop()
    return result, captured


# ── The finding, first ───────────────────────────────────────────────────────


def test_liveness_an_allowed_host_resolves_inside_the_worker(monkeypatch):
    """Control: with the decision carried, the worker still resolves an allowlisted host. Without
    this, every refusal below could be a worker that resolves nothing at all."""
    monkeypatch.setenv("AINDY_EGRESS_ENFORCEMENT", "1")
    seen = _install_with_stub()
    _register(_resolve("api.allowed.com"), isolation=ASSURANCE_INSECURE_DEV)

    result, captured = _authorized_execute(worker=run_one)

    assert result["success"] is True, result
    assert seen == ["api.allowed.com"]
    assert captured["request"]["egress"] == {"mode": "scoped", "domains": ["allowed.com"]}


def test_an_isolated_tool_is_refused_egress_outside_its_allowlist(monkeypatch):
    """★ The defect. At HEAD the isolated branch returned before `egress_scope`, so this
    resolved `evil.com` successfully with the flag ON and a policy registered."""
    monkeypatch.setenv("AINDY_EGRESS_ENFORCEMENT", "1")
    seen = _install_with_stub()
    _register(_resolve("evil.com"), isolation=ASSURANCE_INSECURE_DEV)

    result, captured = _authorized_execute(worker=run_one)

    assert result["success"] is False, (
        "an ISOLATED tool egressed outside its capability allowlist — the decision was computed "
        "in the parent and never reached the worker"
    )
    assert "evil.com" in result["error"] and "egress" in result["error"].lower()
    assert seen == [], "the denied host reached the real resolver"
    assert captured["request"]["egress"]["domains"] == ["allowed.com"]


def test_the_in_process_tool_is_unchanged(monkeypatch):
    monkeypatch.setenv("AINDY_EGRESS_ENFORCEMENT", "1")
    _install_with_stub()
    _register(_resolve("evil.com"))

    result, _ = _authorized_execute()

    assert result["success"] is False
    assert "evil.com" in result["error"]


# ── The worker installs the guard process-globally ───────────────────────────


def _resolve_on_a_bare_thread(host):
    def _fn(args, user_id, db):
        outcome: dict = {}

        def _work():
            try:
                socket.getaddrinfo(host, 443)
                outcome["resolved"] = True
            except Exception as exc:  # noqa: BLE001
                outcome["error"] = f"{type(exc).__name__}: {exc}"

        t = threading.Thread(target=_work)  # raw Thread: does NOT copy contextvars
        t.start()
        t.join()
        if "error" in outcome:
            raise eg.EgressDenied(outcome["error"])
        return outcome

    return _fn


def test_the_worker_closes_the_thread_bypass(monkeypatch):
    """In the worker the guard is process-global, so a thread that does not inherit the
    contextvar is still inside it."""
    monkeypatch.setenv("AINDY_EGRESS_ENFORCEMENT", "1")
    seen = _install_with_stub()
    _register(_resolve_on_a_bare_thread("evil.com"), isolation=ASSURANCE_INSECURE_DEV)

    result, _ = _authorized_execute(worker=run_one)

    assert result["success"] is False, "a bare thread in the WORKER escaped the egress guard"
    assert "evil.com" in result["error"]
    assert seen == []


def test_the_in_process_thread_bypass_stays_open_and_documented(monkeypatch):
    """Pinned, not fixed: closing it in-process means wrapping `threading.Thread` globally,
    which the guard's docstring declines. The worker-global install is the answer for the
    isolated case; this test turning red means someone changed the in-process contract."""
    monkeypatch.setenv("AINDY_EGRESS_ENFORCEMENT", "1")
    seen = _install_with_stub()
    _register(_resolve_on_a_bare_thread("evil.com"))

    result, _ = _authorized_execute()

    assert result["success"] is True
    assert seen == ["evil.com"]
    assert "does not inherit the contextvar" in eg.__doc__


def test_the_worker_never_reads_policy(monkeypatch):
    """DEC-049 — the worker gets the DECISION, not the policy. Its request carries the domains;
    nothing in the worker consults `capability_policy` (which it could not: it has no policies
    registered and no db)."""
    monkeypatch.setenv("AINDY_EGRESS_ENFORCEMENT", "1")
    _install_with_stub()
    _register(_resolve("evil.com"), isolation=ASSURANCE_INSECURE_DEV)

    def _worker_without_policies(request):
        cp.clear_capability_policies()  # what a real subprocess has: none
        return run_one(request)

    result, _ = _authorized_execute(worker=_worker_without_policies)

    assert result["success"] is False
    assert "evil.com" in result["error"]


# ── `authority.network` is part of the decision ──────────────────────────────


def test_a_declared_network_none_denies_everything_in_process(monkeypatch):
    """`none` → no egress, even with no domain policy at all (the mode alone decides)."""
    monkeypatch.setenv("AINDY_EGRESS_ENFORCEMENT", "1")
    seen = _install_with_stub()
    _register(_resolve("api.allowed.com"), env_spec={"authority": {"network": "none"}})
    cp.clear_capability_policies()

    with patch.object(tr, "_ensure_tools_loaded", lambda: None), patch.object(
        tr, "queue_system_event", lambda **k: None
    ):
        result = tr.execute_tool(_TOOL, {}, "user-1", MagicMock(), run_id=None, execution_token=None)

    assert result["success"] is False
    assert "api.allowed.com" in result["error"]
    assert seen == []


def test_a_declared_network_none_is_carried_to_the_worker(monkeypatch):
    monkeypatch.setenv("AINDY_EGRESS_ENFORCEMENT", "1")
    seen = _install_with_stub()
    _register(
        _resolve("api.allowed.com"),
        isolation=ASSURANCE_INSECURE_DEV,
        env_spec={"authority": {"network": "none"}},
    )

    result, captured = _authorized_execute(worker=run_one)

    assert captured["request"]["egress"] == {"mode": "none", "domains": []}
    assert result["success"] is False
    assert seen == []


def test_scoped_with_no_domains_is_fail_closed():
    decision = eg.resolve_egress_decision("scoped", ())
    assert (decision.mode, decision.domains) == ("scoped", ())
    assert decision.enforces is True


def test_open_with_no_domains_enforces_nothing():
    decision = eg.resolve_egress_decision("open", ())
    assert decision.mode == "open"
    assert decision.enforces is False


def test_policy_domains_narrow_an_open_mode():
    decision = eg.resolve_egress_decision("open", ("b.com", "a.com"))
    assert (decision.mode, decision.domains) == ("scoped", ("a.com", "b.com"))


# ── The switch, and the honesty channel ──────────────────────────────────────


def test_flag_off_carries_no_decision_and_enforces_nothing(monkeypatch):
    """DEC-051 — `AINDY_EGRESS_ENFORCEMENT` stays the switch, default off. Off: the request
    carries no `egress` key, the worker installs nothing, the envelope reports nothing."""
    monkeypatch.delenv("AINDY_EGRESS_ENFORCEMENT", raising=False)
    seen = _install_with_stub()
    _register(_resolve("evil.com"), isolation=ASSURANCE_INSECURE_DEV)

    result, captured = _authorized_execute(worker=run_one)

    assert result["success"] is True
    assert seen == ["evil.com"]
    assert "egress" not in captured["request"]
    assert "egress" not in result


def test_the_envelope_reports_which_mechanism_applied(monkeypatch):
    """DEC-050 — a consumer reading `socket_guard` knows the two documented bypasses apply;
    `socket_guard:worker` means the thread bypass does not."""
    monkeypatch.setenv("AINDY_EGRESS_ENFORCEMENT", "1")
    _install_with_stub()

    _register(_resolve("api.allowed.com"), isolation=ASSURANCE_INSECURE_DEV)
    isolated, _ = _authorized_execute(worker=run_one)
    assert isolated["egress"] == {"mode": "scoped", "mechanism": "socket_guard:worker"}

    _register(_resolve("api.allowed.com"))
    local, _ = _authorized_execute()
    assert local["egress"] == {"mode": "scoped", "mechanism": "socket_guard"}


def test_a_provider_that_cannot_enforce_reports_rather_than_refuses(monkeypatch):
    """DEC-050 — a worker that received a decision but whose guard is not installed (the
    request said so) still runs and says `none`; refusing would fail every declared tool on
    every host without a container runner, which is every host today."""
    response = run_one({"tool_name": "definitely.not.registered", "args": {}, "user_id": "u"})
    assert response["ok"] is False
    assert "not registered" in response["error"]
    # The reporting contract lives on the parent envelope; the worker's reply carries the
    # mechanism it applied so the parent reports the truth, not its own assumption.
    monkeypatch.setenv("AINDY_EGRESS_ENFORCEMENT", "1")
    _install_with_stub()
    _register(_resolve("api.allowed.com"), isolation=ASSURANCE_INSECURE_DEV)

    def _worker_that_could_not_install(request):
        reply = run_one({k: v for k, v in request.items() if k != "egress"})
        return reply

    result, _ = _authorized_execute(worker=_worker_that_could_not_install)

    assert result["success"] is True
    assert result["egress"] == {"mode": "scoped", "mechanism": "none"}


def test_the_span_carries_the_mechanism(monkeypatch):
    monkeypatch.setenv("AINDY_EGRESS_ENFORCEMENT", "1")
    _install_with_stub()
    _register(_resolve("api.allowed.com"))
    seen: dict = {}

    class _Span:
        def outcome(self, result):
            seen["outcome"] = result

        def set_attribute(self, key, value):
            seen[key] = value

    import contextlib

    @contextlib.contextmanager
    def _fake_tool_operation(**kwargs):
        yield _Span()

    with patch("AINDY.platform_layer.genai_telemetry.tool_operation", _fake_tool_operation):
        result, _ = _authorized_execute()

    assert result["success"] is True
    assert seen["aindy.egress.mode"] == "scoped"
    assert seen["aindy.egress.mechanism"] == "socket_guard"


# ── Mutation guard on the payload key ────────────────────────────────────────


def test_dropping_the_payload_key_is_the_defect_again(monkeypatch):
    """The first test above is what goes red if the parent stops carrying the decision — this
    states it directly so the mutation is named in the suite."""
    monkeypatch.setenv("AINDY_EGRESS_ENFORCEMENT", "1")
    seen = _install_with_stub()
    _register(_resolve("evil.com"), isolation=ASSURANCE_INSECURE_DEV)

    def _worker_that_lost_the_key(request):
        request.pop("egress", None)
        return run_one(request)

    result, _ = _authorized_execute(worker=_worker_that_lost_the_key)

    assert result["success"] is True, "with no decision carried the worker cannot refuse — that IS the defect"
    assert seen == ["evil.com"]
