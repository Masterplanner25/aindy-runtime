"""GUEST-CONFINE-1 — the guest VM must not reach subprocess, network or host env.

`nodus_worker` runs *submitted script content*, not first-party code. The nodus VM defaults
to ``allow_subprocess=True, allow_network=True, allow_env=True``, under which nodus registers
the **real** ``std:subprocess`` / ``std:http`` / ``std:env`` modules — so a guest script could
reach subprocess, network and host environment **without touching the dispatcher, capability
token, effect ledger, egress guard or tool registry**. Demonstrated 2026-08-15: a guest script
created a file on the host, read the real PATH, and performed real DNS.

The worker now passes the three deny flags. These tests drive the **real worker entry point**
(`run_one`) rather than asserting on the construction site's source text — a source assertion
can only confirm that code was written, never what it returns (ROUTE-GUARD-1).

**Liveness control.** Every assertion below is an assertion of *absence*, which passes
trivially if the VM is broken and every script fails (EVENTBUS-COVERAGE-1: a first-draft wire
suite scored 4/7 for exactly this reason). `test_liveness_benign_script_still_succeeds` must
run and pass, or the rest of this file proves nothing. The block assertions additionally
require the error be a **sandbox** error specifically — "blocked" and "broke" are different
answers, and a caller cannot tell them apart from a bare failure.
"""
from __future__ import annotations

import os
import tempfile

import pytest

pytestmark = pytest.mark.runtime_only

pytest.importorskip("nodus.runtime.embedding")


def _run(script: str) -> dict:
    """Execute a script through the real worker entry point."""
    from AINDY.runtime import nodus_worker

    return nodus_worker.run_one(
        {"script": script, "state": {}, "context": {"user_id": "guest-confine-test"}}
    )


def _sandbox_error(result: dict) -> str:
    """Return the error text, asserting the failure is a *sandbox* refusal.

    `run_one` stringifies the VM's structured error (`nodus_worker.py:463` wraps it in
    `str(...)`), so this is substring matching on a repr, not a dict lookup. That is worth
    stating because it is a real constraint on what these tests can assert: the `kind`
    discriminator survives only as text.
    """
    assert result.get("status") == "failure", f"expected refusal, got {result!r}"
    error = str(result.get("error") or "")
    assert "sandbox" in error, (
        f"expected a sandbox refusal, got {error!r} — a guest must be able to tell 'denied' "
        f"from 'the runtime broke'"
    )
    return error


# --------------------------------------------------------------------------------------
# Liveness control — without this, every assertion below is vacuous.
# --------------------------------------------------------------------------------------


def test_liveness_benign_script_still_succeeds():
    """A script using no host capability must still run.

    This is the control for the whole file: if confinement were implemented by breaking the
    VM outright, every block test below would still pass. This one would not.
    """
    result = _run('set_state("computed", 1 + 1)\n')

    assert result.get("status") == "success", f"guest VM is broken, not confined: {result!r}"
    assert result.get("error") is None
    # Assert an observable *effect*, not just a status: `set_state` is a host function the
    # worker registers, so this proves the VM executed and its output reached the caller.
    # (`print` output does not land in `stdout_log` on this path, so it is not a liveness signal.)
    assert result.get("output_state", {}).get("computed") == 2


# --------------------------------------------------------------------------------------
# The three demonstrated escapes.
# --------------------------------------------------------------------------------------


def test_subprocess_is_denied_and_writes_no_host_file():
    """The demonstrated escape: `subprocess_shell` returned exit_code 0 and created a file.

    Subprocess is the sharpest of the three because it also bypasses the VM's *filesystem*
    confinement — `allowed_paths` defaults to the cwd, but a shelled-out command is not
    subject to that check at all. So `allow_subprocess=False` is what actually closed the
    demonstrated host-file write.
    """
    marker = os.path.join(tempfile.gettempdir(), "gc1_regression_probe.txt").replace("\\", "/")
    if os.path.exists(marker):
        os.remove(marker)

    try:
        result = _run(f'let x = subprocess_shell("echo hi > {marker}")\n')

        assert "allow_subprocess=False" in _sandbox_error(result)
        assert not os.path.exists(marker), "guest script created a file on the host filesystem"
    finally:
        if os.path.exists(marker):
            os.remove(marker)


def test_network_is_denied():
    """`http_get` performed real DNS in the demonstration.

    Egress that is legitimately needed goes through the mediated paths (`sys()` / `call_tool`),
    which are capability-gated; the raw builtin is not.
    """
    result = _run('let x = http_get("http://example.com")\n')

    assert "allow_network=False" in _sandbox_error(result)


def test_host_env_is_denied():
    """`env_get("PATH")` returned the real host PATH in the demonstration.

    Host env is where secrets live (`AINDY_SECRET_*` is an entire broker namespace), so this
    is a disclosure boundary, not only an execution one.
    """
    result = _run('let x = env_get("PATH")\n')

    assert "allow_env=False" in _sandbox_error(result)


# --------------------------------------------------------------------------------------
# The flags must reach the VM the worker actually builds.
# --------------------------------------------------------------------------------------


def test_worker_vm_is_constructed_with_all_three_denials():
    """Pin the construction site behaviourally, by capturing the kwargs the worker passes.

    This complements the end-to-end tests: they prove the guest is refused today, this proves
    *why*, so a future change that silently drops a flag fails here with a precise message
    rather than only as a mysteriously-permissive guest.
    """
    import nodus.runtime.embedding as embedding

    captured: dict = {}
    original = embedding.NodusRuntime

    class _Recording(original):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)
            super().__init__(*args, **kwargs)

    # The worker imports NodusRuntime inside the function body, so patching the module
    # attribute here is what the call site actually resolves.
    embedding.NodusRuntime = _Recording
    try:
        _run('set_state("x", 1)\n')
    finally:
        embedding.NodusRuntime = original

    assert captured.get("allow_subprocess") is False, "allow_subprocess not denied"
    assert captured.get("allow_network") is False, "allow_network not denied"
    assert captured.get("allow_env") is False, "allow_env not denied"
