"""TOOL-SEAM-ISOLATION-1 step B — a tool can DECLARE the isolation it needs.

Scope: ``docs/runtime/TOOL_SEAM_ISOLATION_SCOPE.md``.

★ **What this is not.** A tool that is *allowed* to run still runs **in-process** with the
process's ambient authority. Nothing here confines anything — step C is the process boundary and
is not built. Reading a passing declaration as confinement would be exactly the *"gated path that
does not actually confine"* failure the scope warns against, so the tests below say so explicitly
rather than leaving it to the reader.

★ **Why an assurance class rather than a mechanism.** The entry originally proposed
``isolation="in_process" | "subprocess" | "container" | "strong_vm"``. That asks a caller to state
a *mechanism* the runtime cannot verify, and ``in_process`` and ``subprocess`` are indistinguishable
as **assurance** — a bare subprocess is not a sandbox, and both report ``insecure-dev``. Declaring
against `EXEC-ENV-BIND-1`'s existing assurance vocabulary reuses what is already there instead of
growing a second one beside it, which is the same argument that keeps `FS-SCOPE-1` a field on that
descriptor rather than a peer of ``egress_scope``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from AINDY.agents.tool_registry import TOOL_REGISTRY, execute_tool, register_tool
from AINDY.core.execution_environment import (
    ASSURANCE_CONTAINER,
    ASSURANCE_INSECURE_DEV,
    ASSURANCE_STRONG,
)

pytestmark = pytest.mark.runtime_only

_PROBE = "test.isolation_probe"


def _register(isolation=None):
    calls: list[int] = []

    def _fn(args, user_id, db):
        calls.append(1)
        return {"ran": len(calls)}

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
    return calls


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    TOOL_REGISTRY.pop(_PROBE, None)


def _invoke():
    return execute_tool(
        _PROBE, {}, user_id="u1", db=MagicMock(), run_id=None, execution_token=None
    )


def _host(monkeypatch, assurance):
    monkeypatch.setattr(
        "AINDY.core.execution_environment._host_assurance",
        lambda: (assurance, f"{assurance}/test"),
    )


# ── Registration-time validation ─────────────────────────────────────────────


def test_an_unknown_isolation_class_raises_at_registration():
    """★ Loudly, at registration — the `register_syscall` lesson (IDEM-11), where an
    unforwarded parameter left every plugin syscall at the weakest setting with no way to opt
    in. Silently downgrading a misspelled class would hand the tool a weaker boundary than it
    asked for, which is the one direction that must never be quiet."""
    with pytest.raises(ValueError, match="not a known assurance class"):
        _register(isolation="container")  # the mechanism-shaped name, not an assurance class


@pytest.mark.parametrize(
    "declared", [ASSURANCE_INSECURE_DEV, ASSURANCE_CONTAINER, ASSURANCE_STRONG]
)
def test_every_known_class_is_accepted_and_recorded(declared):
    _register(isolation=declared)
    assert TOOL_REGISTRY[_PROBE]["isolation"] == declared


def test_no_declaration_is_recorded_as_none():
    """The default path, which every existing tool takes."""
    _register()
    assert TOOL_REGISTRY[_PROBE]["isolation"] is None


# ── Liveness control ─────────────────────────────────────────────────────────


def test_liveness_an_undeclared_tool_still_runs(monkeypatch):
    """★ If this fails, every 'is refused' assertion below passes on a seam that runs nothing."""
    _host(monkeypatch, ASSURANCE_INSECURE_DEV)
    calls = _register()

    result = _invoke()

    assert result["success"] is True, result
    assert len(calls) == 1


# ── Refusal ──────────────────────────────────────────────────────────────────


def test_a_tool_demanding_more_than_the_host_provides_is_refused(monkeypatch):
    """★ Fail-closed, and the handler must not run at all."""
    _host(monkeypatch, ASSURANCE_INSECURE_DEV)
    calls = _register(isolation=ASSURANCE_STRONG)

    result = _invoke()

    assert result["success"] is False
    assert calls == [], "the tool executed despite being refused — the check is advisory"
    assert ASSURANCE_STRONG in result["error"]
    assert ASSURANCE_INSECURE_DEV in result["error"], (
        "the error must name what the host DOES provide, or an operator cannot tell a "
        "misconfigured host from an over-strict declaration"
    )


def test_a_refusal_is_an_envelope_not_an_exception(monkeypatch):
    """★ `execute_tool`'s contract is {success, result, error} and every caller reads it that
    way. A refusal that raised would be caught by the seam's own broad handler and reported as a
    tool FAILURE — which reads to a caller as "the tool broke" rather than "this host cannot run
    it". That is the status-code confusion `ROUTE-GUARD-1` was."""
    _host(monkeypatch, ASSURANCE_INSECURE_DEV)
    _register(isolation=ASSURANCE_STRONG)

    result = _invoke()  # must not raise

    assert isinstance(result, dict)
    assert set(result) == {"success", "result", "error"}


def test_a_satisfiable_declaration_runs(monkeypatch):
    _host(monkeypatch, ASSURANCE_STRONG)
    calls = _register(isolation=ASSURANCE_CONTAINER)

    result = _invoke()

    assert result["success"] is True, result
    assert len(calls) == 1


def test_an_exact_match_runs(monkeypatch):
    """`>=`, not `>` — a host that provides exactly what was asked for must satisfy it."""
    _host(monkeypatch, ASSURANCE_CONTAINER)
    calls = _register(isolation=ASSURANCE_CONTAINER)

    assert _invoke()["success"] is True
    assert len(calls) == 1


def test_a_host_resolution_failure_refuses_a_strict_declaration(monkeypatch):
    """★ Failing toward refusal is the only safe direction for a boundary check.
    `_host_assurance` reports the weakest class on any error, so a broken provider denies a
    strict declaration rather than admitting it."""
    def _boom():
        raise RuntimeError("provider exploded")

    # ★ Patch at the SOURCE module only. `_host_assurance` imports
    # `resolve_sandbox_runner_type` inside the function body (to keep `AINDY/core` from pulling
    # `platform_layer` at import time), so there is no module attribute on
    # `execution_environment` to patch — the same shape as the scheduler-job rule in CLAUDE.md.
    monkeypatch.setattr(
        "AINDY.platform_layer.sandbox_runner.resolve_sandbox_runner_type", _boom
    )
    calls = _register(isolation=ASSURANCE_STRONG)

    assert _invoke()["success"] is False
    assert calls == []


# ── The boundary of the claim ────────────────────────────────────────────────


def test_an_allowed_tool_still_runs_in_process(monkeypatch):
    """★ THE thing most likely to be misread. A satisfied declaration means the host meets the
    class — NOT that this tool was confined. It still executes in the runtime process with
    ambient authority; step C is the process boundary and is not built."""
    _host(monkeypatch, ASSURANCE_STRONG)
    seen: dict = {}

    def _fn(args, user_id, db):
        import os
        import threading

        seen["pid"] = os.getpid()
        seen["can_spawn_thread"] = threading.current_thread() is not None
        return {}

    register_tool(
        _PROBE,
        risk="low",
        description="probe",
        capability=f"tool:{_PROBE}",
        required_capability="read_memory",
        category="test",
        egress_scope="internal",
        isolation=ASSURANCE_STRONG,
    )(_fn)

    assert _invoke()["success"] is True
    import os

    assert seen["pid"] == os.getpid(), (
        "the tool ran in a different process — if that ever becomes true, step C landed and "
        "this test should be rewritten rather than deleted"
    )
    assert seen["can_spawn_thread"] is True
