from __future__ import annotations

import json
import subprocess
import sys
from collections import deque
from pathlib import Path
from typing import Any

RUNTIME_CALLBACK_EXECUTION_MODE = "isolated-runtime-callback"
RUNTIME_CALLBACK_PROTOCOL_VERSION = "2026-05-22"


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
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
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
        raise RuntimeError("runtime callback command timed out") from exc

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
