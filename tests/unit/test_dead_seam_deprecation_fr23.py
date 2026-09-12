"""FR-23 ABI half — the two dead registration seams are deprecated with a window.

`platform_layer.register_syscall` and `register_agent_tool` validate a registration and store it
in a dict nothing reads (the dispatcher resolves `kernel.syscall_registry.SYSCALL_REGISTRY`;
`execute_tool` resolves `agents.tool_registry.TOOL_REGISTRY`). The decision (2026-09-12) was to
deprecate with a window rather than wire in (their handler contract differs — it is an adapter,
not a rename) or remove now (ABI break). These assert the window is real:

* both emit a `DeprecationWarning` that names the replacement;
* both still record what they are given, so an out-of-tree caller is not broken mid-window;
* the replacement paths do NOT warn — a deprecation that also fires on the blessed path teaches
  nobody anything.
"""
from __future__ import annotations

import warnings

import pytest

pytestmark = pytest.mark.runtime_only


def _unguard(monkeypatch):
    """Neutralise the capability gate so these tests exercise the deprecation, not the ABI guard."""
    from AINDY.platform_layer import registry

    monkeypatch.setattr(registry, "_require_in_process_extension_capability", lambda *_a, **_k: None)
    return registry


def test_register_syscall_warns_and_still_records(monkeypatch):
    registry = _unguard(monkeypatch)

    def handler(payload):
        return {}

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with monkeypatch.context() as m:
            m.setitem(registry._syscalls, "__never__", None)  # ensure dict is live
            registry.register_syscall("sys.v1.fr23.dep", handler)

    deps = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(deps) == 1, [str(w.message) for w in caught]
    msg = str(deps[0].message)
    assert "kernel.syscall_registry.register_syscall" in msg
    assert "not callable" in msg or "not read by the dispatcher" in msg
    # still recorded through the window
    assert registry.get_syscall("sys.v1.fr23.dep") is handler


def test_register_agent_tool_warns_and_still_records(monkeypatch):
    registry = _unguard(monkeypatch)
    tool = {
        "name": "fr23.tool", "fn": lambda **_k: None, "description": "probe",
        "capability": "test.fr23", "category": "test", "egress_scope": None,
        "required_capability": "test.fr23", "risk": "low",
    }

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        registry.register_agent_tool("fr23.tool", tool)

    deps = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(deps) == 1, [str(w.message) for w in caught]
    msg = str(deps[0].message)
    assert "register_run_tool_provider" in msg or "register_tool" in msg
    assert registry.get_agent_tool("fr23.tool") is tool


def test_the_blessed_syscall_path_does_not_warn():
    """Registering through the kernel registry — the path apps actually use — is not deprecated."""
    from AINDY.kernel import syscall_registry as R

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        R.SYSCALL_REGISTRY["sys.v1.fr23.blessed"] = R.SyscallEntry(
            handler=lambda payload, ctx: {}, capability="test.fr23", description="probe"
        )
    try:
        assert not [w for w in caught if issubclass(w.category, DeprecationWarning)], (
            "the kernel registration path must not warn — it is the replacement"
        )
    finally:
        R.SYSCALL_REGISTRY.pop("sys.v1.fr23.blessed", None)


def test_the_blessed_tool_path_does_not_warn(monkeypatch):
    registry = _unguard(monkeypatch)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        registry.register_run_tool_provider("fr23_runtype", lambda context: [])

    assert not [w for w in caught if issubclass(w.category, DeprecationWarning)], (
        "register_run_tool_provider is the live provider model — it must not warn"
    )


def test_the_capabilities_are_still_audited():
    """The window keeps the INPROC capabilities in the audited set — a plugin declaring them is
    still honoured until removal. A missing capability here would mean the seam was removed
    without updating the ownership audit."""
    from AINDY.platform_layer import registry

    names = {
        registry.INPROC_CAP_REGISTER_SYSCALL,
        registry.INPROC_CAP_REGISTER_AGENT_TOOL,
    }
    assert names == {"registry.register_syscall", "registry.register_agent_tool"}
