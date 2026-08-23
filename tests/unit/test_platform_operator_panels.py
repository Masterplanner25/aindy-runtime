"""FR-21 — the operator console's new panels must point at routes that exist.

The runtime serves an operator SPA; the app team independently grew a second one beside
it and offered it back. Of the five panels they called "clearly runtime", four already
existed here — the real gaps were **webhooks** and the **dead-letter queue**, both driving
runtime-owned routes this console did not expose. An operator should not open an app
repo's UI to drain a runtime DLQ.

**What this file can and cannot check, stated plainly** (CLAUDE.md's rule is that a route
test must call the route, and source assertions are a supplement, never the coverage):

* The **runtime half is behavioural**: every path the SPA will request is checked against
  the actual route table of a booted app. A typo in a URL string is otherwise invisible
  until an operator clicks the tab and gets a 404 — there is no build step that would
  catch it, because the string is data.
* The **SPA half is a source assertion**, because this repo has no JavaScript test
  runner. It checks the two things that make a panel *reachable*: a route in
  `PlatformApp.tsx` and an entry in the shell's nav. That combination is the failure the
  app team hit on their own surface — they record that 7 of 8 panels had no navigation
  until a later PR added it. A built, routed, unreachable panel is the specific way this
  work goes wrong.
"""
from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.runtime_only

_REPO = pathlib.Path(__file__).resolve().parents[2]
_ROUTES_JS = _REPO / "platform" / "src" / "api" / "_routes.js"
_APP_TSX = _REPO / "platform" / "src" / "PlatformApp.tsx"
_SHELL_TSX = _REPO / "platform" / "src" / "components" / "platform" / "PlatformShell.tsx"

#: A path segment built from a JS template expression, e.g. `${encodeURIComponent(id)}`.
_JS_INTERPOLATION = re.compile(r"\$\{[^}]*\}")
#: A FastAPI path parameter, e.g. `{subscription_id}`.
_PATH_PARAM = re.compile(r"\{[^}]*\}")


def _normalise(path: str) -> str:
    """Reduce both dialects to one comparable shape: parameters become `*`."""
    return _PATH_PARAM.sub("*", _JS_INTERPOLATION.sub("*", path))


def _declared_spa_paths() -> set[str]:
    """Every `/platform/...` path the SPA's RUNTIME_ROUTES block will request."""
    source = _ROUTES_JS.read_text(encoding="utf-8")
    start = source.index("export const RUNTIME_ROUTES")
    block = source[start:]
    # Both quoted literals and backtick templates, since the parameterised routes are
    # written as template functions.
    literals = re.findall(r"[\"'`](/platform/[^\"'`]*)[\"'`]", block)
    assert literals, "RUNTIME_ROUTES declares no /platform paths — did the block move?"
    return {_normalise(path) for path in literals}


def _served(app) -> dict[str, set[str]]:
    """`{path: {method, …}}` from the app's OpenAPI schema.

    ★ Two wrong sources were tried first, and both fail in ways worth recording:

    1. **Walking `app.routes`.** Under FastAPI >= 0.137 an included router is stored as a
       lazy `_IncludedRouter`, so the walk yields `/webhooks`, never `/platform/webhooks`
       — every path reads as missing while all seven are served. `HTTP-SCOPE-GAP-1`
       records the same trap producing a route census wrong by 56.
    2. **Probing over HTTP for a non-404.** `_SPAStaticFiles` is mounted at `/platform`
       and falls back to `index.html` for any unmatched path, so a GET to a route that
       does not exist answers **200 with HTML**. The probe cannot tell a served route
       from a typo, which is precisely the failure it was written to catch — it passed
       for the real paths and also for `/platform/webhooks-that-do-not-exist`.

    The schema is the published surface a client codes against, carries full prefixes,
    and knows the methods. It is the only one of the three that answers the question.
    """
    paths = app.openapi()["paths"]
    return {_normalise(path): {method.upper() for method in methods} for path, methods in paths.items()}


class TestTheSpaPointsAtRealRoutes:
    def test_every_declared_path_is_served(self, runtime_only_app):
        declared = _declared_spa_paths()
        served = _served(runtime_only_app)
        missing = sorted(declared - set(served))
        assert not missing, (
            f"the operator console would request {missing}, which no route serves — "
            "a URL string is data, so nothing else catches this until an operator clicks"
        )

    def test_the_panels_cover_both_adopted_surfaces(self):
        """Liveness control: the check above passes trivially on an empty declaration."""
        declared = _declared_spa_paths()
        assert any(path.startswith("/platform/webhooks") for path in declared)
        assert any(path.startswith("/platform/queue/dead-letters") for path in declared)

    def test_a_path_that_does_not_exist_is_absent(self, runtime_only_app):
        """Liveness control for the instrument — it must be able to say "not served"."""
        assert "/platform/webhooks-that-do-not-exist" not in _served(runtime_only_app)

    def test_every_action_the_panels_take_is_served_with_its_method(self, runtime_only_app):
        """Panels that can only read are the shape FR-21 exists to fix.

        Their report: our served bundle had zero occurrences of `webhook`, `dlq`,
        `dead-letter` or `drain` — the capability existed and the operator surface did
        not expose it. So assert the *write* actions, by method, not just that a path
        with that name exists somewhere.
        """
        served = _served(runtime_only_app)
        for method, path in (
            ("GET", "/platform/webhooks"),
            ("POST", "/platform/webhooks"),
            ("DELETE", "/platform/webhooks/*"),
            ("GET", "/platform/queue/health"),
            ("GET", "/platform/queue/dead-letters"),
            ("POST", "/platform/queue/dead-letters/drain"),
            ("POST", "/platform/queue/dead-letters/*/replay"),
            ("DELETE", "/platform/queue/dead-letters/*"),
        ):
            assert method in served.get(path, set()), (
                f"{method} {path} is not served; that panel action would fail"
            )


class TestThePanelsAreReachable:
    """Routed but not navigable is a panel nobody finds."""

    @pytest.mark.parametrize(
        ("route_path", "component"),
        [("/webhooks", "WebhooksPanel"), ("/dead-letters", "DeadLetterQueuePanel")],
    )
    def test_the_route_is_declared(self, route_path, component):
        source = _APP_TSX.read_text(encoding="utf-8")
        assert f'path="{route_path}"' in source
        assert f"import(\"./components/platform/{component}\")" in source

    @pytest.mark.parametrize("route_path", ["/webhooks", "/dead-letters"])
    def test_the_shell_offers_a_nav_entry(self, route_path):
        source = _SHELL_TSX.read_text(encoding="utf-8")
        assert f'to: "{route_path}"' in source, (
            f"{route_path} has a route but no nav entry — the app team shipped 7 of 8 "
            "panels in exactly that state before a later PR added navigation"
        )

    @pytest.mark.parametrize("component", ["WebhooksPanel", "DeadLetterQueuePanel"])
    def test_the_panel_guards_on_admin(self, component):
        """Both surfaces are admin-only server-side; the UI must not imply otherwise."""
        source = (
            _REPO / "platform" / "src" / "components" / "platform" / f"{component}.jsx"
        ).read_text(encoding="utf-8")
        assert "AdminAccessRequired" in source
        assert "isAdmin" in source
