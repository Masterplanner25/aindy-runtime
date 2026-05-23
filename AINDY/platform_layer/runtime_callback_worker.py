from __future__ import annotations

import importlib
import json
import os
import sys
from importlib import util as importlib_util
from pathlib import Path
from typing import Any


def _error(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message, "worker_pid": os.getpid()}


def _handle(payload: dict[str, Any]) -> dict[str, Any]:
    module_name = str(payload.get("module_name") or "").strip()
    function_name = str(payload.get("function_name") or "").strip()
    source_path = str(payload.get("source_path") or "").strip()
    expects_argument = bool(payload.get("expects_argument"))
    bootstrap_register = bool(payload.get("bootstrap_register"))
    argument = payload.get("argument")

    if not module_name or not function_name:
        return _error("runtime callback worker requires module_name and function_name")
    if module_name.startswith("AINDY."):
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            return _error(f"cannot import runtime callback module {module_name!r}: {exc}")
    elif module_name.startswith("apps."):
        if not source_path:
            return _error(
                f"runtime callback worker requires source_path for first-party module {module_name!r}"
            )
        candidate = Path(source_path)
        if not candidate.is_file():
            return _error(
                f"runtime callback worker source_path was not found for {module_name!r}: {source_path!r}"
            )
        spec = importlib_util.spec_from_file_location(module_name, candidate)
        if spec is None or spec.loader is None:
            return _error(
                f"cannot load first-party runtime callback module {module_name!r} from {source_path!r}"
            )
        module = importlib_util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            return _error(
                f"cannot import first-party runtime callback module {module_name!r}: {exc}"
            )
    else:
        return _error(
            f"runtime callback worker only imports AINDY.* or apps.* modules, got {module_name!r}"
        )

    if bootstrap_register:
        register = getattr(module, "register", None)
        if callable(register):
            try:
                register()
            except Exception as exc:
                return _error(f"runtime callback module register() failed for {module_name!r}: {exc}")

    fn = getattr(module, function_name, None)
    if not callable(fn):
        return _error(f"runtime callback {module_name}.{function_name} is not callable")

    try:
        result = fn(argument) if expects_argument else fn()
    except Exception as exc:
        return _error(f"{exc.__class__.__name__}: {exc}")

    return {
        "ok": True,
        "result": result,
        "worker_pid": os.getpid(),
        "module_name": module_name,
        "function_name": function_name,
    }


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw or "{}")
    except Exception as exc:
        sys.stdout.write(json.dumps(_error(f"invalid JSON request: {exc}")))
        return 1
    response = _handle(payload)
    sys.stdout.write(json.dumps(response, default=str))
    return 0 if response.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
