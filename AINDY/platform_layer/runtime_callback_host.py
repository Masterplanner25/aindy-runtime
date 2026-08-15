from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from collections import deque
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

RUNTIME_CALLBACK_EXECUTION_MODE = "isolated-runtime-callback"
RUNTIME_CALLBACK_PROTOCOL_VERSION = "2026-05-22"

# APP-FR-* FR-11. Each invocation spawns a fresh interpreter that must import the
# target module's whole transitive graph, so the budget has to cover a cold start,
# not just the callback's own work.
#
# The old value was a hardcoded 10.0s that neither `registry.py` call site could
# override and no env key exposed. Two pieces of evidence say that was too tight:
#
#   * Measured 2026-08-15 on the *lightest* profile (runtime-only, idle host):
#     ~3.85s median — only ~2.6x headroom. A loaded host or a larger plugin profile
#     eats that easily.
#   * The sibling subprocess in `nodus_runtime_adapter.py` already budgets
#     `_DEFAULT_BOOT_ALLOWANCE_MS = 15_000` for *boot alone*, on top of a 30s script
#     budget — i.e. this callback's entire budget was smaller than what a comparable
#     subprocess is given just to start.
#
# 30s keeps a genuinely hung callback bounded while giving cold start ~8x the
# measured idle cost.
_DEFAULT_CALLBACK_TIMEOUT_SECS: float = 30.0
_CALLBACK_TIMEOUT_ENV = "AINDY_RUNTIME_CALLBACK_TIMEOUT_SECS"


def resolve_callback_timeout_seconds() -> float:
    """Resolve the subprocess budget, **reading the environment at call time**.

    Deliberately not a module-level constant. CLAUDE.md records module-import-time
    env reads as a recurring hazard in this repo (FR-10's crash-loop, the
    ResourceManager backend cache, the rate limiter's Redis alias): they are invisible
    to behavioural tests, so only a source read or a reload-based test can see them.
    Resolving per call costs one `os.getenv` against a process spawn — nothing — and
    keeps this knob honestly testable and settable without a restart.

    Invalid or non-positive values fall back to the default with a warning rather than
    raising, since this is on the path of scheduled jobs.
    """
    raw = os.getenv(_CALLBACK_TIMEOUT_ENV, "").strip()
    if not raw:
        return _DEFAULT_CALLBACK_TIMEOUT_SECS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "[runtime_callback] %s=%r is not a number; using %.1fs",
            _CALLBACK_TIMEOUT_ENV, raw, _DEFAULT_CALLBACK_TIMEOUT_SECS,
        )
        return _DEFAULT_CALLBACK_TIMEOUT_SECS
    if value <= 0:
        logger.warning(
            "[runtime_callback] %s=%r must be > 0; using %.1fs",
            _CALLBACK_TIMEOUT_ENV, raw, _DEFAULT_CALLBACK_TIMEOUT_SECS,
        )
        return _DEFAULT_CALLBACK_TIMEOUT_SECS
    return value


def build_runtime_callback_spec(
    *,
    surface: str,
    identifier: str,
    owner_class: str,
    module_name: str,
    function_name: str,
    source_path: str | None = None,
    expects_argument: bool,
    bootstrap_register: bool = False,
) -> dict[str, Any]:
    return {
        "surface": str(surface),
        "identifier": str(identifier),
        "owner_class": str(owner_class),
        "module_name": str(module_name),
        "function_name": str(function_name),
        "source_path": str(source_path or ""),
        "expects_argument": bool(expects_argument),
        "bootstrap_register": bool(bootstrap_register),
        "execution_mode": RUNTIME_CALLBACK_EXECUTION_MODE,
        "protocol_version": RUNTIME_CALLBACK_PROTOCOL_VERSION,
    }


def invoke_runtime_callback(
    spec: dict[str, Any],
    *,
    argument: Any = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Run a registered callback in an isolated subprocess.

    Args:
        timeout_seconds: Explicit budget. ``None`` (the default) resolves from
            ``AINDY_RUNTIME_CALLBACK_TIMEOUT_SECS`` at call time — see FR-11 above.
    """
    if timeout_seconds is None:
        timeout_seconds = resolve_callback_timeout_seconds()
    worker_module = "AINDY.platform_layer.runtime_callback_worker"
    command = [sys.executable, "-m", worker_module]
    payload = {
        "module_name": spec["module_name"],
        "function_name": spec["function_name"],
        "source_path": spec.get("source_path") or "",
        "expects_argument": bool(spec.get("expects_argument")),
        "bootstrap_register": bool(spec.get("bootstrap_register")),
        "argument": argument,
    }
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    try:
        stdout, stderr = process.communicate(
            json.dumps(payload, default=str),
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise RuntimeError(
            f"runtime callback command timed out after {timeout_seconds:g}s "
            f"(set {_CALLBACK_TIMEOUT_ENV} to raise the budget)"
        ) from exc

    stderr_excerpt = " | ".join(
        line.strip()
        for line in deque((stderr or "").splitlines(), maxlen=10)
        if line.strip()
    )
    try:
        response = json.loads(stdout or "{}")
    except Exception as exc:
        raise RuntimeError(
            f"runtime callback worker returned invalid JSON: {exc}"
            + (f" | stderr={stderr_excerpt}" if stderr_excerpt else "")
        ) from exc

    if not response.get("ok"):
        error = str(response.get("error") or "runtime callback failed")
        if stderr_excerpt:
            error += f" | stderr={stderr_excerpt}"
        raise RuntimeError(error)

    response["execution_mode"] = RUNTIME_CALLBACK_EXECUTION_MODE
    response["protocol_version"] = RUNTIME_CALLBACK_PROTOCOL_VERSION
    response["surface"] = spec.get("surface")
    response["identifier"] = spec.get("identifier")
    return response
