"""A failed runtime callback must say something useful about why.

Found while hunting FLAKY-1. The captured traceback — the first natural one, after
three earlier failures were run through `| tail` and destroyed — ended at:

    RuntimeError: runtime callback failed
    AINDY/platform_layer/runtime_callback_host.py:159

and that is the *entire* diagnostic content. No exit code, no stderr, no mention of
which callback. The mechanism: a worker that dies before replying writes nothing to
stdout, `json.loads(stdout or "{}")` turns that into `{}`, `{}.get("ok")` is falsy,
and the handler emits its default message. A subprocess that never started and a
callback that legitimately returned `{"ok": false}` were indistinguishable.

**That collapse is why FLAKY-1 has resisted diagnosis.** Its recorded "leading
mechanism" is a 10s timeout — but the timeout branch raises a *different*, explicit
message, and the natural failure does not take it. The hypothesis was formed by
*forcing* a timeout and matching the shape, never by reading a real failure.

These tests pin that a failure names the exit code, and that the three distinct
failure modes stay distinguishable from each other.

Marked `runtime_only` — without it CI collects nothing here (CI-MARKER-1).
"""
from __future__ import annotations

import subprocess

import pytest

from AINDY.platform_layer import runtime_callback_host as host

pytestmark = pytest.mark.runtime_only

SPEC = {
    "surface": "trigger_evaluator",
    "identifier": "default",
    "module_name": "some.module",
    "function_name": "evaluate",
    "source_path": "",
    "expects_argument": True,
}


class _FakeProcess:
    """Stands in for the worker subprocess: scripted streams and exit code."""

    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0):
        self._out, self._err = stdout, stderr
        self.returncode = returncode

    def communicate(self, _input=None, timeout=None):
        return self._out, self._err

    def kill(self):  # pragma: no cover - only the timeout path calls this
        pass


@pytest.fixture
def worker(monkeypatch):
    """Install a scripted subprocess for `invoke_runtime_callback`."""
    def _install(stdout: str, stderr: str = "", returncode: int = 0):
        monkeypatch.setattr(
            host.subprocess, "Popen",
            lambda *a, **kw: _FakeProcess(stdout, stderr, returncode),
        )
    return _install


class TestFailuresNameTheExitCode:
    def test_silent_worker_death_is_not_reported_as_a_callback_failure(self, worker):
        """The exact shape of the captured FLAKY-1 traceback.

        Empty stdout, empty stderr, non-zero exit — a worker killed before it could
        reply. 0xC0000142 (STATUS_DLL_INIT_FAILED) under process pressure produces
        precisely this, and so does an OOM kill.
        """
        worker(stdout="", stderr="", returncode=3221225794)

        with pytest.raises(RuntimeError) as exc:
            host.invoke_runtime_callback(SPEC, argument={})

        message = str(exc.value)
        assert "produced no output" in message, message
        assert "exit=3221225794" in message, message
        # It must also say which callback, since the caller catches broadly.
        assert "some.module.evaluate" in message, message
        # And it must NOT masquerade as the callback having returned a failure.
        assert message != "runtime callback failed"

    def test_a_real_callback_failure_still_reports_its_own_error(self, worker):
        worker(stdout='{"ok": false, "error": "evaluator exploded"}', returncode=0)

        with pytest.raises(RuntimeError) as exc:
            host.invoke_runtime_callback(SPEC, argument={})

        message = str(exc.value)
        assert "evaluator exploded" in message
        assert "exit=0" in message

    def test_invalid_json_reports_the_exit_code_too(self, worker):
        worker(stdout="not json at all", stderr="boom", returncode=1)

        with pytest.raises(RuntimeError) as exc:
            host.invoke_runtime_callback(SPEC, argument={})

        message = str(exc.value)
        assert "invalid JSON" in message
        assert "exit=1" in message
        assert "stderr=boom" in message

    def test_stderr_is_carried_when_the_worker_dies_loudly(self, worker):
        worker(stdout="", stderr="Traceback...\nImportError: no such module", returncode=1)

        with pytest.raises(RuntimeError) as exc:
            host.invoke_runtime_callback(SPEC, argument={})

        assert "ImportError: no such module" in str(exc.value)


class TestTheThreeModesStayDistinguishable:
    """A liveness control for the tests above.

    Collapsing every failure into one message would satisfy any single assertion
    that only checks for a substring. These pin that the modes differ from each
    other, which is the property that was actually lost.
    """

    def _message(self, worker, **kw) -> str:
        worker(**kw)
        with pytest.raises(RuntimeError) as exc:
            host.invoke_runtime_callback(SPEC, argument={})
        return str(exc.value)

    def test_silent_death_invalid_json_and_callback_error_are_three_messages(self, worker):
        silent = self._message(worker, stdout="", returncode=1)
        bad_json = self._message(worker, stdout="{", returncode=0)
        failed = self._message(worker, stdout='{"ok": false, "error": "nope"}', returncode=0)

        assert len({silent, bad_json, failed}) == 3, (silent, bad_json, failed)
        assert "produced no output" in silent
        assert "invalid JSON" in bad_json
        assert "nope" in failed

    def test_the_timeout_path_is_still_its_own_message(self, monkeypatch):
        """The branch FLAKY-1's recorded hypothesis assumed. It is distinct — which
        is the evidence that the captured natural failure did NOT take it."""
        class _Timeout:
            returncode = None

            def communicate(self, _input=None, timeout=None):
                if timeout is not None:
                    raise subprocess.TimeoutExpired(cmd="worker", timeout=timeout)
                return "", ""

            def kill(self):
                pass

        monkeypatch.setattr(host.subprocess, "Popen", lambda *a, **kw: _Timeout())

        with pytest.raises(RuntimeError) as exc:
            host.invoke_runtime_callback(SPEC, argument={}, timeout_seconds=1)

        message = str(exc.value)
        assert "timed out after 1s" in message
        assert "produced no output" not in message


def test_a_successful_callback_is_unaffected(worker):
    worker(stdout='{"ok": true, "result": {"decision": "execute"}}', returncode=0)

    response = host.invoke_runtime_callback(SPEC, argument={})

    assert response["ok"] is True
    assert response["result"] == {"decision": "execute"}
    assert response["surface"] == "trigger_evaluator"
    assert response["identifier"] == "default"
