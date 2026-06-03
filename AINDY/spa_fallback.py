"""
SPA browser-navigation fallback for the /platform client routes.

PROBLEM (BLOCKER-2)
-------------------
The platform SPA is served from a StaticFiles mount at `/platform` (registered
LAST in routing.py). Several SPA *client* routes share an exact path with
backend *API* routes also under `/platform` (e.g. `/platform/flows`,
`/platform/observability/...`). Because the API routers are registered before
the static mount, a raw browser navigation/refresh to `/platform/flows` is a
GET that matches the API route first -> 401 (no Bearer on an address-bar nav)
-> the SPA never loads ("black screen"). Client-side <NavLink> navigation works
(no full page load), but deep-links and refresh do not.

FIX
---
A thin ASGI middleware that runs BEFORE routing. For a request that is:
  - method GET (or HEAD),
  - whose path is one of the known SPA client routes (explicit allowlist),
  - and whose `Accept` header prefers `text/html` (i.e. a browser navigation,
    not an XHR/fetch issuing `application/json` or `*/*`),
we rewrite the ASGI scope's path to the SPA mount's index so the static app
serves `index.html` and client-side routing takes over.

Everything else -- all non-GET methods (POST /platform/flows, etc.), all API
clients (curl, fetch, Swagger), all non-allowlisted paths -- passes through
completely untouched to the normal router stack.

WHY ASGI (not BaseHTTPMiddleware)
---------------------------------
BaseHTTPMiddleware buffers responses, can interfere with streaming, and
complicates ContextVar / execution_context propagation that the platform
routers depend on. This middleware only ever inspects the scope and either
mutates `scope["path"]`/`scope["raw_path"]` or calls the downstream app
unchanged -- no body buffering, no exception interference.
"""

from __future__ import annotations

from typing import Iterable

# Mount prefix the SPA is served under. Keep in sync with routing.py.
_PLATFORM_PREFIX = "/platform"

# The SPA's index path under the StaticFiles mount. A request rewritten to this
# path resolves to dist/index.html via the mount, and client-side routing then
# renders the correct screen from the ORIGINAL URL (the browser's location is
# unchanged; only the server-side resolution target is rewritten).
_SPA_INDEX_PATH = f"{_PLATFORM_PREFIX}/index.html"

# Explicit allowlist of SPA client routes that collide with (or could collide
# with) API paths. These are the 8 screens mounted in PlatformApp.tsx, expressed
# as their fully-prefixed server paths. Add a screen here when you add a route.
#
# NOTE: this is an EXACT-match set, not a prefix set. We deliberately do NOT
# blanket-serve the SPA for every `/platform/*` GET -- doing so would turn
# genuine API 404s into 200-with-SPA and mask bugs. Only these known paths are
# rewritten; everything else 404s honestly.
_SPA_CLIENT_PATHS: frozenset[str] = frozenset(
    {
        f"{_PLATFORM_PREFIX}",            # bare /platform -> home redirect
        f"{_PLATFORM_PREFIX}/",           # trailing slash
        f"{_PLATFORM_PREFIX}/agent",
        f"{_PLATFORM_PREFIX}/flows",
        f"{_PLATFORM_PREFIX}/observability",
        f"{_PLATFORM_PREFIX}/health",
        f"{_PLATFORM_PREFIX}/executions",
        f"{_PLATFORM_PREFIX}/approvals",
        f"{_PLATFORM_PREFIX}/registry",
        f"{_PLATFORM_PREFIX}/trace",
    }
)


def _header_value(headers: Iterable[tuple[bytes, bytes]], name: bytes) -> str:
    """Return the (last) value of an ASGI header, decoded, lowercased, or ''."""
    value = b""
    for key, val in headers:
        if key.lower() == name:
            value = val
    return value.decode("latin-1").lower()


def _prefers_html(accept: str) -> bool:
    """
    True when the client prefers an HTML response -- i.e. a browser navigation.

    A browser address-bar nav / refresh sends:
        Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8
    The SPA's own API calls (authRequest/adminRequest) send:
        Accept: application/json
    or bare `*/*`. We require an explicit `text/html` token. We also reject the
    case where `application/json` is explicitly preferred, as a defensive guard
    against an API client that happens to also list text/html.
    """
    if "text/html" not in accept:
        return False
    # If a caller explicitly asks for JSON, treat it as an API client even if it
    # also lists text/html -- let the real API route answer.
    if "application/json" in accept:
        return False
    return True


class SPAFallbackMiddleware:
    """
    Pure-ASGI middleware. Rewrites browser-navigation GETs to known SPA client
    routes so the StaticFiles mount serves index.html, instead of the colliding
    API route claiming the path first.

    Install this OUTERMOST (added last to the app, so it wraps everything) so it
    runs before the router resolves the path:

        app.add_middleware(SPAFallbackMiddleware)

    It is a no-op for every request that isn't a GET/HEAD browser navigation to
    an allowlisted path, so it is safe to wrap the whole app.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        if method not in ("GET", "HEAD"):
            await self.app(scope, receive, send)
            return

        # `scope["path"]` is already URL-decoded and basename-agnostic at the
        # ASGI layer here -- it is the full server path, e.g. "/platform/flows".
        path = scope.get("path", "")
        if path not in _SPA_CLIENT_PATHS:
            await self.app(scope, receive, send)
            return

        accept = _header_value(scope.get("headers", []), b"accept")
        if not _prefers_html(accept):
            # API/XHR client hitting a colliding path -> let the real route run.
            await self.app(scope, receive, send)
            return

        # Browser navigation to a known SPA route. Rewrite the resolution target
        # to the SPA index so the StaticFiles mount serves index.html. The
        # browser's visible URL is unaffected; client-side routing reads it and
        # renders the right screen.
        new_scope = dict(scope)
        new_scope["path"] = _SPA_INDEX_PATH
        new_scope["raw_path"] = _SPA_INDEX_PATH.encode("latin-1")
        await self.app(new_scope, receive, send)
