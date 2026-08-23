"""FR-22 — the runtime publishes its HTTP surface, and this is what makes that true.

The app team documents ~51 runtime-owned routes in their API reference. Their guard
(`scripts/check_api_reference.py`) diffs that document against a booted app but is scoped
to `APP_PREFIX = "/apps/"`, so the runtime half of their file is a curated inventory
**nothing checks** — accurate when written, free to drift afterwards. They declined to
extend their guard over our routes, for the reason FR-20 records: an app-side mechanism
policing a runtime-owned surface makes the app responsible for something it does not own,
and hides the problem from whoever could fix it.

So the runtime publishes `AINDY/route_inventory.json` instead, and this file is the
guarantee behind it. Without a check, a published inventory is just a second document that
can go stale — the `DOCS-COVERAGE-CLAIM-1` shape with a `.json` extension.

★ **A finding that changes how a consumer should read it: the `/apps/` prefix is not an
ownership boundary.** 35 routes under `/apps/*` — coordination, memory, agent — are served
by the runtime *alone*, with no plugins loaded. That is why the inventory is defined by
**boot profile** rather than by path prefix, and why it is more useful than the ask: a
consumer can subtract it from their booted surface to derive the genuinely app-owned set,
instead of curating one by hand.
"""
from __future__ import annotations

import json
import pathlib

import pytest

pytestmark = pytest.mark.runtime_only

_REPO = pathlib.Path(__file__).resolve().parents[2]
_INVENTORY = _REPO / "AINDY" / "route_inventory.json"


def _committed() -> dict:
    return json.loads(_INVENTORY.read_text(encoding="utf-8"))


def _pairs(inventory: dict) -> set[tuple[str, str]]:
    return {(entry["method"], entry["path"]) for entry in inventory["routes"]}


def _served(app) -> set[tuple[str, str]]:
    """The published surface, read the way a client reads it."""
    return {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }


def _supported_surface_app(monkeypatch):
    """A fresh app in the profile the inventory describes.

    The legacy alias surface is off: the inventory publishes the *supported* routes, not
    the compatibility shims. `tests/conftest.py` turns those aliases on session-wide, so
    this must be built explicitly rather than taken from the shared fixture.
    """
    monkeypatch.setenv("AINDY_ENABLE_LEGACY_SURFACE", "false")

    from tests.fixtures.client import _fresh_main_app

    return _fresh_main_app(runtime_only=True, require_apps=False)


class TestTheInventoryIsCurrent:
    def test_it_matches_the_served_surface_exactly(self, monkeypatch):
        app = _supported_surface_app(monkeypatch)
        served = _served(app)
        committed = _pairs(_committed())

        added = sorted(served - committed)
        removed = sorted(committed - served)
        assert not added and not removed, (
            f"route inventory is stale — added={added} removed={removed}. "
            "Run `python scripts/check_route_inventory.py` and commit the result; a "
            "removal is a breaking change for consumers pinned to this file."
        )

    def test_the_count_field_agrees_with_the_list(self):
        """A count that drifts from its own list is how a summary starts lying."""
        inventory = _committed()
        assert inventory["route_count"] == len(inventory["routes"])

    def test_it_records_the_profile_it_describes(self):
        """Absence of a path must mean "not served by the runtime alone", not "gone"."""
        assert _committed()["boot_mode"] == "runtime-only"


class TestTheCheckCanFail:
    """Liveness controls. An equality assertion is worth nothing until it can be broken."""

    def test_an_added_route_is_detected(self, monkeypatch):
        app = _supported_surface_app(monkeypatch)

        @app.get("/fr22-liveness-probe")
        def _probe():  # pragma: no cover - never called, only routed
            return {"ok": True}

        app.openapi_schema = None  # FastAPI caches the schema after the first build
        assert ("GET", "/fr22-liveness-probe") in _served(app) - _pairs(_committed())

    def test_a_removed_route_would_be_detected(self):
        """The other direction, which matters more: a consumer pinned to this file."""
        inventory = _committed()
        trimmed = {"routes": inventory["routes"][1:]}
        assert _pairs(inventory) - _pairs(trimmed), "the comparison ignores removals"


class TestConsumersCanActuallyReadIt:
    def test_it_ships_inside_the_package(self):
        """`AINDY/*.json` is already globbed into the wheel — no packaging change needed.

        This is the property the whole request depends on: a consumer reads the inventory
        for the version they installed, without booting the runtime.
        """
        assert _INVENTORY.parent.name == "AINDY", (
            "the inventory must live directly under AINDY/ or the package-data glob "
            "(`\"AINDY\" = [\"*.json\", …]`) will not ship it"
        )
        import tomllib

        pyproject = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))
        patterns = pyproject["tool"]["setuptools"]["package-data"]["AINDY"]
        assert "*.json" in patterns

    def test_the_prefix_is_not_an_ownership_boundary(self):
        """Pins the finding, because a consumer's guard may assume the opposite.

        `check_api_reference.py` app-side treats `/apps/*` as the app's surface. Some of
        those routes are served by the runtime with no plugins loaded, so that assumption
        does not hold — and the inventory is what lets a consumer compute the real split.
        """
        apps_prefixed = [
            entry for entry in _committed()["routes"] if entry["path"].startswith("/apps/")
        ]
        assert apps_prefixed, (
            "no /apps/* route is runtime-served any more — if that is deliberate, delete "
            "this test and the note in SDK_CONTRACT.md that depends on it"
        )
