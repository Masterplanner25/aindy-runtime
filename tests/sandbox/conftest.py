"""
Sandbox escape test suite — environment detection, shared fixtures, result collection.

PURPOSE
-------
These tests prove adversarially that the Docker hardening controls used by
``ContainerizedOciSandboxRunner`` actually block escape vectors at the kernel level.
The claim being proved is: a plugin running inside a properly-hardened container
cannot escape into the host or access resources it was not granted.

The tests are divided into categories matching the six escape vector classes:
  filesystem   — read-only rootfs / read-only bind mount prevent writes
  network      — ``--network none`` prevents outbound connections
  process      — ``--pids-limit`` prevents fork-bomb / PID exhaustion
  privilege    — ``--cap-drop ALL`` / ``no-new-privileges`` block priv escalation
  env_leak     — minimal env set means no host secrets reach the container
  path_boundary — only the plugin-root bind mount is accessible; no ambient host paths

RUNNING
-------
    pytest -m sandbox_escape -v                     # all escape tests
    pytest tests/sandbox/ -v                        # same
    SANDBOX_ESCAPE_IMAGE=python:3.12-alpine pytest -m sandbox_escape -v

RESULT ARTIFACT
---------------
On session finish, ``tests/sandbox/sandbox_escape_results.json`` is written.
Each entry records: attack_vector, hardening_control, docker_flag, description,
status (PASS/FAIL/SKIP/ERROR), exit_code, evidence, duration_ms, and the exact
docker args + command so the run is reproducible from the record alone.

PLATFORM NOTES
--------------
- Docker must be available on PATH.
- Linux containers backend must be active (Linux host, or Docker Desktop with
  Linux containers mode on Windows/macOS).
- Tests that use Linux-only kernel controls (``--cap-drop``, ``--pids-limit``,
  ``--security-opt no-new-privileges``) are skipped automatically when
  ``linux_kernel_controls`` is False.
- On Windows with Docker Desktop (Linux containers), all controls are available
  because the containers run inside a Linux VM (WSL2 or Hyper-V).
"""
from __future__ import annotations

import datetime
import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SANDBOX_ESCAPE_IMAGE: str = os.environ.get("SANDBOX_ESCAPE_IMAGE", "python:3.11-alpine")
RESULTS_PATH: Path = Path(__file__).parent / "sandbox_escape_results.json"

# Sensitive env-var names the container must NOT inherit.
SENSITIVE_ENV_KEYS: frozenset[str] = frozenset(
    [
        "SECRET_KEY",
        "DATABASE_URL",
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "AINDY_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_ACCESS_KEY_ID",
        "PERMISSION_SECRET",
    ]
)

# ---------------------------------------------------------------------------
# Session-wide result accumulator
# ---------------------------------------------------------------------------

_session_results: dict[str, Any] = {}


def record_result(test_node_id: str, result: dict[str, Any]) -> None:
    """Register an escape attempt result for inclusion in the JSON artifact."""
    _session_results[test_node_id] = result


# ---------------------------------------------------------------------------
# Docker detection helpers
# ---------------------------------------------------------------------------


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _detect_linux_backend() -> dict[str, Any]:
    """
    Detect whether Docker is running a Linux containers backend.

    On a Linux host, this is always true.
    On Windows/macOS with Docker Desktop, we probe ``docker info`` for OSType.
    Returns a dict usable as the ``docker_info`` fixture value.
    """
    host = platform.system().lower()
    if host == "linux":
        return {"available": True, "linux_backend": True, "host_platform": host, "os_type": "linux"}

    try:
        proc = subprocess.run(
            ["docker", "info", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
            timeout=8.0,
        )
        if proc.returncode != 0:
            return {
                "available": True,
                "linux_backend": False,
                "host_platform": host,
                "os_type": None,
                "error": f"docker info exited {proc.returncode}",
            }
        info = json.loads(proc.stdout)
        os_type = str(info.get("OSType") or "").lower()
        return {
            "available": True,
            "linux_backend": os_type == "linux",
            "host_platform": host,
            "os_type": os_type,
        }
    except Exception as exc:
        return {
            "available": True,
            "linux_backend": False,
            "host_platform": host,
            "os_type": None,
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# Session fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def docker_info() -> dict[str, Any]:
    """
    Session-wide Docker capability record.

    Skips the entire sandbox suite if Docker is not available.
    Exposes ``linux_backend`` so individual tests can conditionally skip.
    """
    if not _docker_available():
        pytest.skip("docker not found on PATH — sandbox escape tests require Docker")
    info = _detect_linux_backend()
    if not info.get("linux_backend"):
        pytest.skip(
            "Docker is not running a Linux containers backend. "
            "On Windows/macOS, switch Docker Desktop to Linux containers mode."
        )
    return info


@pytest.fixture(scope="session")
def escape_image(docker_info: dict[str, Any]) -> str:
    """
    Pull (if needed) and return the container image tag used for all escape tests.

    The image is pulled once per session. Override with SANDBOX_ESCAPE_IMAGE env var.
    Default: python:3.11-alpine (has Python + sh; ~55 MB).
    """
    image = SANDBOX_ESCAPE_IMAGE
    # Check if already pulled.
    check = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        pull = subprocess.run(
            ["docker", "pull", image],
            capture_output=True,
            text=True,
            timeout=180.0,
        )
        if pull.returncode != 0:
            pytest.skip(
                f"could not pull escape test image {image!r}: {pull.stderr.strip()!r}"
            )
    return image


@pytest.fixture(scope="session")
def linux_kernel_controls(docker_info: dict[str, Any]) -> bool:
    """
    True when Linux-only kernel controls are available.

    These controls — ``--cap-drop``, ``--pids-limit``, ``--security-opt no-new-privileges``
    — require a Linux kernel inside the container, which is available on:
      - Linux hosts running Docker natively
      - Windows/macOS hosts running Docker Desktop in Linux containers mode
        (containers run in a Linux VM via WSL2 or Hyper-V, so Linux kernel semantics apply)

    The fixture returns True when ``docker_info["linux_backend"]`` is True, which is
    already required by the ``docker_info`` fixture for the suite to run at all.
    """
    return bool(docker_info.get("linux_backend"))


# ---------------------------------------------------------------------------
# Core escape test helper
# ---------------------------------------------------------------------------


def run_escape_attempt(
    *,
    docker_args: list[str],
    image: str,
    cmd: list[str],
    attack_vector: str,
    hardening_control: str,
    docker_flag: str,
    description: str,
    expect_failure: bool = True,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """
    Run a Docker container escape attempt and return a structured result.

    Parameters
    ----------
    docker_args:
        Docker ``run`` flags that implement the hardening control under test
        (e.g. ``["--network", "none"]``). These are inserted between ``docker run --rm``
        and the image name.
    image:
        Container image tag (from the ``escape_image`` fixture).
    cmd:
        Command + args run inside the container (e.g. ``["python", "-c", "..."]``).
    attack_vector:
        Short label for the escape category (e.g. ``"network_escape"``).
    hardening_control:
        The Docker/kernel control being tested (e.g. ``"disable_network"``).
    docker_flag:
        The primary Docker flag that implements the control (e.g. ``"--network none"``).
    description:
        One-sentence human summary of what is being tested and why it matters.
    expect_failure:
        When True (default): the escape is expected to be BLOCKED — the container
        command must exit non-zero for the test to PASS.
        When False: a positive-verification check where the container command must
        exit zero for the test to PASS (used for /proc status checks etc.).
    timeout:
        Seconds before the Docker run is killed with a TIMEOUT result.

    Returns a dict with keys: status, attack_vector, hardening_control, docker_flag,
    description, duration_ms, exit_code, evidence, docker_args, image, cmd.
    """
    full_args = ["docker", "run", "--rm", *docker_args, image, *cmd]
    start = time.monotonic()
    try:
        proc = subprocess.run(
            full_args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)
        if expect_failure:
            # PASS = escape was blocked (container exited non-zero)
            passed = proc.returncode != 0
            evidence = f"exit {proc.returncode}"
            raw_stderr = (proc.stderr or "").strip()
            raw_stdout = (proc.stdout or "").strip()
            if raw_stderr:
                evidence += f"; stderr: {raw_stderr[:300]!r}"
            elif raw_stdout:
                evidence += f"; stdout: {raw_stdout[:300]!r}"
        else:
            # PASS = verification check succeeded (container exited zero)
            passed = proc.returncode == 0
            evidence = f"exit {proc.returncode}"
            raw_stdout = (proc.stdout or "").strip()
            if raw_stdout:
                evidence += f"; stdout: {raw_stdout[:300]!r}"
        return {
            "status": "PASS" if passed else "FAIL",
            "attack_vector": attack_vector,
            "hardening_control": hardening_control,
            "docker_flag": docker_flag,
            "description": description,
            "duration_ms": elapsed_ms,
            "exit_code": proc.returncode,
            "evidence": evidence,
            "docker_args": docker_args,
            "image": image,
            "cmd": cmd,
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "SKIP",
            "attack_vector": attack_vector,
            "hardening_control": hardening_control,
            "docker_flag": docker_flag,
            "description": description,
            "duration_ms": int(timeout * 1000),
            "exit_code": None,
            "evidence": f"docker run timed out after {timeout}s",
            "docker_args": docker_args,
            "image": image,
            "cmd": cmd,
        }
    except Exception as exc:
        return {
            "status": "ERROR",
            "attack_vector": attack_vector,
            "hardening_control": hardening_control,
            "docker_flag": docker_flag,
            "description": description,
            "duration_ms": int((time.monotonic() - start) * 1000),
            "exit_code": None,
            "evidence": str(exc),
            "docker_args": docker_args,
            "image": image,
            "cmd": cmd,
        }


# ---------------------------------------------------------------------------
# Session-finish: write JSON artifact
# ---------------------------------------------------------------------------


def pytest_sessionfinish(session: Any, exitstatus: Any) -> None:
    """Write sandbox_escape_results.json after all sandbox tests complete."""
    if not _session_results:
        return

    total = len(_session_results)
    passed = sum(1 for r in _session_results.values() if r.get("status") == "PASS")
    failed = sum(1 for r in _session_results.values() if r.get("status") == "FAIL")
    skipped_or_error = total - passed - failed

    artifact: dict[str, Any] = {
        "schema_version": "2026-06-04",
        "tested_at": datetime.datetime.utcnow().isoformat() + "Z",
        "host_platform": platform.system().lower(),
        "container_image": SANDBOX_ESCAPE_IMAGE,
        "results": _session_results,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "skipped_or_error": skipped_or_error,
        },
    }
    try:
        RESULTS_PATH.write_text(json.dumps(artifact, indent=2))
    except Exception:
        pass
