from __future__ import annotations

import os
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

pytestmark = pytest.mark.runtime_only

_EXE_SUFFIX = ".exe" if sys.platform == "win32" else ""
AINDY_RUNTIME = Path(sysconfig.get_path("scripts")) / f"aindy-runtime{_EXE_SUFFIX}"


def _clean_env() -> dict[str, str]:
    """Minimal env: OS-level basics only — no AINDY, database, or API-key vars."""
    env: dict[str, str] = {}
    if "PATH" in os.environ:
        env["PATH"] = os.environ["PATH"]
    if sys.platform == "win32":
        for key in ("SystemRoot", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE"):
            if key in os.environ:
                env[key] = os.environ[key]
    return env


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(AINDY_RUNTIME), *args],
        env=_clean_env(),
        capture_output=True,
        text=True,
    )


def test_help_flag_exits_zero_without_env():
    result = _run("--help")
    assert result.returncode == 0, f"--help exited {result.returncode}; stderr: {result.stderr}"
    assert "aindy-runtime" in result.stdout
    assert "serve" in result.stdout
    assert "sandbox" in result.stdout


def test_version_flag_exits_zero_without_env():
    result = _run("--version")
    assert result.returncode == 0, f"--version exited {result.returncode}; stderr: {result.stderr}"
    assert "aindy-runtime" in result.stdout


def test_bare_invocation_shows_help_and_exits_zero():
    result = _run()
    assert result.returncode == 0, f"bare invocation exited {result.returncode}; stderr: {result.stderr}"
    assert "aindy-runtime" in result.stdout
    assert "serve" in result.stdout


def test_sandbox_subcommand_runs_without_database():
    result = _run("sandbox")
    # 0 = satisfied, 1 = not satisfied, both are valid outcomes.
    # 2 = error running the check — that is a test failure.
    assert result.returncode in {0, 1}, (
        f"sandbox exited {result.returncode}; stderr: {result.stderr}"
    )
    assert result.stdout.strip(), "sandbox produced no output"


def test_serve_exits_nonzero_and_reports_missing_database_url():
    result = _run("serve")
    assert result.returncode != 0, "serve should exit non-zero when DATABASE_URL is not set"
    assert "DATABASE_URL" in result.stderr, (
        f"expected DATABASE_URL in stderr; got: {result.stderr!r}"
    )
