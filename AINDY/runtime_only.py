from __future__ import annotations

import os
from typing import NoReturn

from AINDY.platform_layer.deployment_contract import BOOT_MODE_ENV_VAR, RUNTIME_ONLY_BOOT_MODE

os.environ.setdefault(BOOT_MODE_ENV_VAR, RUNTIME_ONLY_BOOT_MODE)

from AINDY.main import app  # noqa: E402,F401


def _run_sandbox_check() -> NoReturn:
    """Print sandbox posture as JSON. Exit 0 if requirements satisfied, 1 if not, 2 on error."""
    import json
    import sys as _sys
    from AINDY.platform_layer.deployment_contract import (
        get_api_runtime_conditions,
        plugin_sandbox_assurance_posture,
    )
    from AINDY.platform_layer.extension_runtime_inventory import trusted_python_execution_inventory
    from AINDY.platform_layer.health_service import sandbox_verification_posture
    from AINDY.platform_layer.sandbox_runner import sandbox_platform_capability_matrix

    try:
        posture = plugin_sandbox_assurance_posture()
        payload = {
            "plugin_sandbox_posture": posture,
            "plugin_sandbox_platform": sandbox_platform_capability_matrix(),
            "sandbox_verification_posture": sandbox_verification_posture(),
            "trusted_python_execution": trusted_python_execution_inventory(),
            "runtime_conditions": get_api_runtime_conditions(),
        }
        req_status = posture.get("requirement_status", {})
        satisfied = bool(
            req_status.get("assurance_class_satisfied", False)
            and req_status.get("certification_tier_satisfied", False)
        )
        print(json.dumps(payload, indent=2))
        raise SystemExit(0 if satisfied else 1)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"sandbox check failed: {exc}", file=_sys.stderr)
        raise SystemExit(2)


def main() -> NoReturn:
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "sandbox":
        _run_sandbox_check()

    import uvicorn

    uvicorn.run(
        "AINDY.runtime_only:app",
        host=os.getenv("AINDY_HOST", "127.0.0.1"),
        port=int(os.getenv("AINDY_PORT", "8000")),
    )
    raise SystemExit(0)


if __name__ == "__main__":
    main()
