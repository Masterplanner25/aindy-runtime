"""Generate and verify the runtime's published HTTP route inventory (FR-22).

The app team documents ~51 runtime-owned routes in their API reference. Their own guard
(`scripts/check_api_reference.py`) diffs that document against a booted app — but it is
scoped to `APP_PREFIX = "/apps/"`, so the runtime half of their file is a *curated
inventory nothing checks*. It was accurate when written and would drift silently.

They deliberately did not extend their guard over our routes, and they are right not to:
an app-side mechanism policing a runtime-owned surface makes the app responsible for
something it does not control, and hides the problem from the people who could fix it.
That is the same reasoning FR-20 records.

So the runtime publishes its own surface instead.

**The artifact.** ``AINDY/route_inventory.json`` — every ``(method, path)`` the runtime
serves in the ``runtime-only`` boot profile, taken from the app's OpenAPI schema. It ships
inside the wheel (``[tool.setuptools.package-data]`` already globs ``AINDY/*.json``), so a
consumer reads the inventory *for the version they installed* without booting anything::

    from importlib.resources import files
    import json

    inventory = json.loads(files("AINDY").joinpath("route_inventory.json").read_text())
    served = {(entry["method"], entry["path"]) for entry in inventory["routes"]}

**Why the OpenAPI schema and not the route table.** Walking ``app.routes`` reports
``/webhooks`` where the served path is ``/platform/webhooks``: FastAPI >= 0.137 stores an
included router as a lazy ``_IncludedRouter`` and the prefix lives on the wrapper.
`HTTP-SCOPE-GAP-1` records that trap producing a census wrong by 56 routes, and FR-21's
first draft hit it again. The schema carries full paths and methods, and it is what a
client codes against.

**Why the runtime-only profile.** It is the boot mode in which every served route is
runtime-owned by construction — no plugins, no app routers — so the inventory needs no
hand-curated ownership list that could itself drift. The profile is recorded in the file.

Usage::

    python scripts/check_route_inventory.py            # regenerate the inventory
    python scripts/check_route_inventory.py --check     # CI: fail if it is stale
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
INVENTORY_PATH = REPO_ROOT / "AINDY" / "route_inventory.json"

HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}

#: The boot profile the inventory describes. Recorded in the file so a consumer knows what
#: the absence of a path means: "not served by the runtime alone", not "does not exist".
BOOT_MODE = "runtime-only"


def _configure_import_environment() -> None:
    """A self-contained boot env, so the result is identical in CI and locally.

    Deliberately explicit rather than inherited: an inventory that changes because of an
    ambient environment variable would fail the check for reasons no one can reproduce.
    """
    defaults = {
        "DATABASE_URL": "sqlite://",
        "AINDY_ALLOW_SQLITE": "1",
        "MONGO_URL": "",
        "SKIP_MONGO_PING": "1",
        "OPENAI_API_KEY": "sk-test-placeholder",
        "DEEPSEEK_API_KEY": "ds-test-placeholder",
        "SECRET_KEY": "route-inventory-secret-not-for-production",
        "AINDY_API_KEY": "route-inventory-api-key",
        "ALLOWED_ORIGINS": "http://localhost:3000",
        "ENV": "test",
        "TESTING": "true",
        "TEST_MODE": "true",
        "AINDY_ENABLE_BACKGROUND_TASKS": "false",
        "AINDY_ENFORCE_SCHEMA": "false",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)
    # Not setdefault: the profile IS the definition of the inventory, so an ambient value
    # must not silently change what gets published.
    os.environ["AINDY_BOOT_MODE"] = BOOT_MODE
    # The legacy surface is an opt-in alias set; excluded so the inventory describes the
    # supported surface rather than the compatibility shims.
    os.environ["AINDY_ENABLE_LEGACY_SURFACE"] = "false"


def collect_routes() -> list[dict[str, Any]]:
    """Return the served ``(method, path)`` pairs, with their OpenAPI tags."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    _configure_import_environment()

    from AINDY.main import app

    spec = app.openapi()
    routes: list[dict[str, Any]] = []
    for path, operations in spec.get("paths", {}).items():
        for method, operation in operations.items():
            if method.lower() not in HTTP_METHODS:
                continue
            routes.append(
                {
                    "method": method.upper(),
                    "path": path,
                    # Tags come from the routers themselves, so they group the surface
                    # without a second hand-maintained classification to keep in step.
                    "tags": sorted(operation.get("tags") or []),
                }
            )
    routes.sort(key=lambda entry: (entry["path"], entry["method"]))
    return routes


def build_inventory() -> dict[str, Any]:
    routes = collect_routes()
    return {
        "schema": 1,
        "boot_mode": BOOT_MODE,
        # No version field on purpose: this file is committed, and stamping a version into
        # it would make every release bump edit it and every branch conflict on it. The
        # wheel it ships in already identifies the version.
        "route_count": len(routes),
        "routes": routes,
    }


def _serialise(inventory: dict[str, Any]) -> str:
    return json.dumps(inventory, indent=2, sort_keys=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if the committed inventory does not match the live route surface.",
    )
    args = parser.parse_args(argv)

    inventory = build_inventory()
    rendered = _serialise(inventory)

    if not args.check:
        INVENTORY_PATH.write_text(rendered, encoding="utf-8")
        print(f"Route inventory updated: {inventory['route_count']} routes -> {INVENTORY_PATH}")
        return 0

    if not INVENTORY_PATH.exists():
        print(f"Route inventory missing: {INVENTORY_PATH}", file=sys.stderr)
        print("Run: python scripts/check_route_inventory.py", file=sys.stderr)
        return 1

    committed = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    live_pairs = {(entry["method"], entry["path"]) for entry in inventory["routes"]}
    committed_pairs = {(entry["method"], entry["path"]) for entry in committed.get("routes", [])}

    added = sorted(live_pairs - committed_pairs)
    removed = sorted(committed_pairs - live_pairs)
    if not added and not removed:
        print(f"Route inventory current: {inventory['route_count']} routes.")
        return 0

    print("Route inventory is stale — the runtime's HTTP surface changed.", file=sys.stderr)
    for method, path in added:
        print(f"  + {method} {path}", file=sys.stderr)
    for method, path in removed:
        print(f"  - {method} {path}", file=sys.stderr)
    print(
        "\nRun `python scripts/check_route_inventory.py` and commit the result. A removal "
        "is a breaking change for consumers pinned to this file — say so in the changelog.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
