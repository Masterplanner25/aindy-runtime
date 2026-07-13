"""NODUS-SYS-SURFACE-1 — fail loud on the idiomatic `std:sys` syscall path.

A `.nd` script's idiomatic `import "std:sys"; sys.call(name, payload)` bottoms out in
nodus's native `syscall` builtin → `nodus.services.syscall_runtime.call_syscall`, an
in-process ephemeral stub that never reaches AINDY's capability-enforced dispatcher — and
it cannot be aliased (nodus forbids overriding a builtin). The worker installs a guard that
converts that silent wrong-backend into an immediate, clear error. The bare `sys(...)`
builtin (Surface B → AINDY) is a different function and is unaffected.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.runtime_only

nodus_syscall_runtime = pytest.importorskip("nodus.services.syscall_runtime")


@pytest.fixture
def _restore_call_syscall():
    original = nodus_syscall_runtime.call_syscall
    yield
    nodus_syscall_runtime.call_syscall = original


def test_install_patches_call_syscall_to_raise(_restore_call_syscall):
    from AINDY.runtime.nodus_worker import _install_std_sys_guard

    assert _install_std_sys_guard() is True
    with pytest.raises(RuntimeError, match="std:sys is not routed"):
        nodus_syscall_runtime.call_syscall("sys.v1.memory.put", {"key": "k", "value": "v"})


def test_std_sys_path_fails_loud_end_to_end(_restore_call_syscall):
    from nodus.runtime.embedding import NodusRuntime

    from AINDY.runtime.nodus_worker import _install_std_sys_guard

    _install_std_sys_guard()
    runtime = NodusRuntime()
    # `syscall(...)` is exactly what stdlib sys.nd's `call()` invokes.
    result = runtime.run_source(
        'let r = syscall("sys.v1.memory.put", {"key": "k", "value": "v"})\n',
        filename="std_sys_guard_test.nd",
    )
    assert result.get("ok") is False
    assert "std:sys is not routed" in str(result.get("error") or "")


def test_guard_message_names_the_bare_sys_builtin():
    from AINDY.runtime.nodus_worker import _STD_SYS_GUARD_MESSAGE

    # The error must tell the developer what to use instead.
    assert 'sys("<name>", <payload>)' in _STD_SYS_GUARD_MESSAGE
    assert "std:sys" in _STD_SYS_GUARD_MESSAGE
