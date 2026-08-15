"""The runtime-callback subprocess budget (APP-FR-* FR-11).

Each `invoke_runtime_callback` spawns a fresh interpreter that imports the target
module's whole transitive graph, so the budget must cover a cold start. It was a
hardcoded 10.0s that neither `registry.py` call site could override and no env key
exposed — measured at ~3.85s median on the *lightest* profile (runtime-only, idle
host), i.e. ~2.6x headroom, while the sibling nodus subprocess already budgets 15s
for boot alone. Too tight under load, and the leading hypothesis for FLAKY-1.

**The budget is resolved at call time, not import time.** That is the property most
worth protecting here: CLAUDE.md records module-import-time env reads as a recurring
hazard (FR-10's container crash-loop, the ResourceManager backend cache, the rate
limiter's Redis alias) precisely because they are invisible to behavioural tests. The
test below sets the variable on a live module and asserts the change takes effect
with no reload — which is only possible because the read is per call.

Marked `runtime_only` — without it CI collects nothing here (CI-MARKER-1).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from AINDY.platform_layer import runtime_callback_host as host
from AINDY.platform_layer.runtime_callback_host import (
    _CALLBACK_TIMEOUT_ENV,
    _DEFAULT_CALLBACK_TIMEOUT_SECS,
    invoke_runtime_callback,
    resolve_callback_timeout_seconds,
)

pytestmark = pytest.mark.runtime_only


def _spec():
    return {
        "module_name": "some.module",
        "function_name": "some_function",
        "source_path": "",
        "expects_argument": False,
        "bootstrap_register": False,
        "surface": "trigger_evaluator",
        "identifier": "default",
        "owner_class": "runtime",
    }


class TestResolveBudget:
    def test_default_is_used_when_unset(self, monkeypatch):
        monkeypatch.delenv(_CALLBACK_TIMEOUT_ENV, raising=False)
        assert resolve_callback_timeout_seconds() == _DEFAULT_CALLBACK_TIMEOUT_SECS

    def test_default_is_larger_than_the_measured_cold_start(self):
        """~3.85s median measured on the lightest profile. The default must leave real
        headroom for a loaded host, not merely exceed the idle case."""
        assert _DEFAULT_CALLBACK_TIMEOUT_SECS >= 4 * 3.85

    def test_default_covers_the_sibling_subprocess_boot_allowance(self):
        """`nodus_runtime_adapter` budgets 15s for *boot alone* on a comparable
        subprocess. This one previously had 10s for boot **and** work."""
        from AINDY.runtime.nodus_runtime_adapter import _DEFAULT_BOOT_ALLOWANCE_MS

        assert _DEFAULT_CALLBACK_TIMEOUT_SECS >= _DEFAULT_BOOT_ALLOWANCE_MS / 1000.0

    def test_env_override_is_honoured(self, monkeypatch):
        monkeypatch.setenv(_CALLBACK_TIMEOUT_ENV, "45")
        assert resolve_callback_timeout_seconds() == 45.0

    def test_env_is_read_at_call_time_not_import_time(self, monkeypatch):
        """★ The anti-hazard property. Import-time reads are invisible to behavioural
        tests; if this ever regresses to a module constant, this test fails without a
        reload trick."""
        monkeypatch.delenv(_CALLBACK_TIMEOUT_ENV, raising=False)
        assert resolve_callback_timeout_seconds() == _DEFAULT_CALLBACK_TIMEOUT_SECS

        monkeypatch.setenv(_CALLBACK_TIMEOUT_ENV, "99")
        assert resolve_callback_timeout_seconds() == 99.0, (
            "the budget did not change without a module reload — it is being read at "
            "import time again"
        )

        monkeypatch.setenv(_CALLBACK_TIMEOUT_ENV, "7")
        assert resolve_callback_timeout_seconds() == 7.0

    def test_fractional_values_are_allowed(self, monkeypatch):
        monkeypatch.setenv(_CALLBACK_TIMEOUT_ENV, "12.5")
        assert resolve_callback_timeout_seconds() == 12.5

    def test_surrounding_whitespace_is_tolerated(self, monkeypatch):
        monkeypatch.setenv(_CALLBACK_TIMEOUT_ENV, "  20  ")
        assert resolve_callback_timeout_seconds() == 20.0

    def test_empty_value_is_treated_as_unset(self, monkeypatch):
        """Compose renders `${VAR:-}` as an empty string — the FR-10 shape."""
        monkeypatch.setenv(_CALLBACK_TIMEOUT_ENV, "")
        assert resolve_callback_timeout_seconds() == _DEFAULT_CALLBACK_TIMEOUT_SECS

    @pytest.mark.parametrize("bad", ["nonsense", "10s", "None", "1e"])
    def test_unparseable_values_fall_back_rather_than_raise(self, monkeypatch, bad):
        """This runs on the path of scheduled jobs; a bad env var must not raise."""
        monkeypatch.setenv(_CALLBACK_TIMEOUT_ENV, bad)
        assert resolve_callback_timeout_seconds() == _DEFAULT_CALLBACK_TIMEOUT_SECS

    @pytest.mark.parametrize("bad", ["0", "-1", "-0.5"])
    def test_non_positive_values_fall_back(self, monkeypatch, bad):
        """A zero budget would make every callback time out instantly."""
        monkeypatch.setenv(_CALLBACK_TIMEOUT_ENV, bad)
        assert resolve_callback_timeout_seconds() == _DEFAULT_CALLBACK_TIMEOUT_SECS


class TestInvokeUsesTheBudget:
    @staticmethod
    def _fake_process(stdout='{"ok": true, "result": null}'):
        process = MagicMock()
        process.communicate.return_value = (stdout, "")
        return process

    def test_resolved_budget_is_passed_to_the_subprocess(self, monkeypatch):
        monkeypatch.setenv(_CALLBACK_TIMEOUT_ENV, "42")
        process = self._fake_process()
        with patch.object(host.subprocess, "Popen", return_value=process):
            invoke_runtime_callback(_spec())
        assert process.communicate.call_args.kwargs["timeout"] == 42.0

    def test_explicit_argument_overrides_the_environment(self, monkeypatch):
        """The parameter still wins, so a caller that knows better can say so."""
        monkeypatch.setenv(_CALLBACK_TIMEOUT_ENV, "42")
        process = self._fake_process()
        with patch.object(host.subprocess, "Popen", return_value=process):
            invoke_runtime_callback(_spec(), timeout_seconds=3.0)
        assert process.communicate.call_args.kwargs["timeout"] == 3.0

    def test_default_is_used_when_neither_is_supplied(self, monkeypatch):
        monkeypatch.delenv(_CALLBACK_TIMEOUT_ENV, raising=False)
        process = self._fake_process()
        with patch.object(host.subprocess, "Popen", return_value=process):
            invoke_runtime_callback(_spec())
        assert (
            process.communicate.call_args.kwargs["timeout"]
            == _DEFAULT_CALLBACK_TIMEOUT_SECS
        )


class TestTimeoutError:
    def test_message_names_the_budget_and_the_env_key(self, monkeypatch):
        """An operator hitting this needs to know what to change. The old message was
        just 'runtime callback command timed out'."""
        import subprocess as _subprocess

        monkeypatch.setenv(_CALLBACK_TIMEOUT_ENV, "11")
        process = MagicMock()
        # communicate() is called twice: once for the run, once after kill() to reap
        # the child. A bare exception side_effect would raise on both.
        process.communicate.side_effect = [
            _subprocess.TimeoutExpired(cmd="x", timeout=11),
            ("", ""),
        ]

        with patch.object(host.subprocess, "Popen", return_value=process):
            with pytest.raises(RuntimeError) as excinfo:
                invoke_runtime_callback(_spec())

        message = str(excinfo.value)
        assert "timed out" in message
        assert "11" in message
        assert _CALLBACK_TIMEOUT_ENV in message

    def test_the_subprocess_is_killed_on_timeout(self, monkeypatch):
        """A hung child must not be left behind when the budget expires."""
        import subprocess as _subprocess

        process = MagicMock()
        process.communicate.side_effect = [
            _subprocess.TimeoutExpired(cmd="x", timeout=1),
            ("", ""),
        ]
        with patch.object(host.subprocess, "Popen", return_value=process):
            with pytest.raises(RuntimeError):
                invoke_runtime_callback(_spec(), timeout_seconds=1.0)
        process.kill.assert_called_once()
