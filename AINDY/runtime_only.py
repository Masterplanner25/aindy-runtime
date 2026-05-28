from __future__ import annotations

import argparse
import os
import sys
from typing import NoReturn

from AINDY._version import __version__
from AINDY.platform_layer.deployment_contract import BOOT_MODE_ENV_VAR, RUNTIME_ONLY_BOOT_MODE


def __getattr__(name: str):
    """Lazy-load ``app`` so importing this module does not pull in the database layer.

    uvicorn resolves ``AINDY.runtime_only:app`` via getattr after import, so the
    attribute must be reachable from the module namespace — but it does not need to be
    defined at import time. Deferring the import of AINDY.main keeps ``--help``,
    ``--version``, and ``sandbox`` from triggering database engine creation.
    """
    if name == "app":
        from AINDY.main import app as _app
        globals()["app"] = _app
        return _app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _run_sandbox_check() -> NoReturn:
    """Print sandbox posture as JSON. Exit 0 if requirements satisfied, 1 if not, 2 on error."""
    import json
    import sys as _sys
    from AINDY.platform_layer.deployment_contract import (
        get_api_runtime_conditions,
        plugin_sandbox_assurance_posture,
    )
    from AINDY.platform_layer.extension_runtime_inventory import trusted_python_execution_inventory
    from AINDY.platform_layer.sandbox_runner import sandbox_platform_capability_matrix

    try:
        from AINDY.platform_layer.health_service import sandbox_verification_posture
        _sv_posture = sandbox_verification_posture()
    except Exception:
        _sv_posture = {"skipped": True, "reason": "database not configured"}

    try:
        posture = plugin_sandbox_assurance_posture()
        payload = {
            "plugin_sandbox_posture": posture,
            "plugin_sandbox_platform": sandbox_platform_capability_matrix(),
            "sandbox_verification_posture": _sv_posture,
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


def _serve() -> NoReturn:
    """Start the aindy-runtime HTTP API server."""
    os.environ.setdefault(BOOT_MODE_ENV_VAR, RUNTIME_ONLY_BOOT_MODE)

    from AINDY.config import settings
    if not settings.DATABASE_URL:
        print(
            "error: DATABASE_URL is not set.\n"
            "\n"
            "Docker Compose quickstart (recommended):\n"
            "  cp AINDY/.env.example AINDY/.env   # then edit with real values\n"
            "  docker compose up -d\n"
            "\n"
            "Manual / local dev:\n"
            "  DATABASE_URL=postgresql://user:password@host:5432/aindy aindy-runtime serve\n"
            "\n"
            "For full setup instructions see README.md — Quickstart.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    import uvicorn
    uvicorn.run(
        "AINDY.runtime_only:app",
        host=os.getenv("AINDY_HOST", "127.0.0.1"),
        port=int(os.getenv("AINDY_PORT", "8000")),
    )
    raise SystemExit(0)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="aindy-runtime",
        description="A.I.N.D.Y. runtime — HTTP server and diagnostics.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"aindy-runtime {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser(
        "serve",
        help="Start the aindy-runtime HTTP API server.",
        description=(
            "Start the aindy-runtime HTTP API server. "
            "DATABASE_URL must be set to a valid PostgreSQL URI."
        ),
    )
    subparsers.add_parser(
        "sandbox",
        help="Report sandbox capabilities and exit.",
        description=(
            "Print sandbox assurance posture as JSON and exit. "
            "Exit 0 if all requirements are satisfied, 1 if not, 2 on error. "
            "Does not require a running database."
        ),
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        raise SystemExit(0)

    if args.command == "serve":
        _serve()
    elif args.command == "sandbox":
        _run_sandbox_check()


if __name__ == "__main__":
    main()
