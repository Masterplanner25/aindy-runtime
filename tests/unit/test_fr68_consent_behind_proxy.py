"""FR-68: behind a proxy, the MCP consent page's rate limit is one bucket for everyone.

The consent page limits sign-in attempts per `request.client.host`. Behind the HTTPS proxy the
docs require, that is the proxy's address, so ten bad sign-ins a minute from anyone lock the owner
out. uvicorn's own `ProxyHeadersMiddleware` (on by default) already answers the ask: it trusts the
peers in `FORWARDED_ALLOW_IPS` (CIDRs allowed) and rewrites `client.host` to the right-most
untrusted `X-Forwarded-For` hop. These tests drive the real OAuth app under uvicorn, configured
exactly as `serve_http`'s `uvicorn.run` configures it (no `forwarded_allow_ips` argument, so the
env var decides), with the test client standing in for the proxy at 127.0.0.1.
"""
from __future__ import annotations

import re

import pytest

pytest.importorskip("nodus_mcp_aindy")
pytest.importorskip("uvicorn")

from tests.unit.test_mcp_oauth_flow import (  # noqa: E402,F401  (fixtures are used by name)
    _authorize,
    _clean_state,
    _http,
    _pkce,
    _register,
    _scopes_column_works_on_sqlite,
    factory,
    server,
)
from tests.unit.test_mcp_oauth_flow import EMAIL  # noqa: E402

pytestmark = pytest.mark.runtime_only


def _bad_sign_in(base: str, consent_url: str, client_ip: str) -> int:
    """One wrong-password attempt arriving through the proxy for `client_ip`."""
    with _http() as c:
        page = c.get(consent_url)
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
        pending = re.search(r'name="p" value="([^"]+)"', page.text).group(1)
        form = {"p": pending, "csrf": csrf, "email": EMAIL, "password": "wrong",
                "action": "approve", "scope": ["memory.read"]}
        return c.post(f"{base}/oauth/consent", data=form,
                      headers={"X-Forwarded-For": client_ip}).status_code


def _consent_url(base: str) -> str:
    client = _register(base)
    _, challenge = _pkce()
    auth = _authorize(base, client["client_id"], challenge)
    assert auth.status_code == 302, auth.text
    return auth.headers["location"]


def _attacker_then_owner(base: str) -> tuple[list[int], int]:
    url = _consent_url(base)
    attacker = [_bad_sign_in(base, url, "203.0.113.66") for _ in range(11)]
    owner = _bad_sign_in(base, url, "198.51.100.7")
    return attacker, owner


@pytest.fixture
def proxy_untrusted(monkeypatch):
    """FR-68 as the app hit it: the proxy's address is not a trusted forwarder."""
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "192.0.2.1")


@pytest.fixture
def proxy_trusted(monkeypatch):
    """The answer: the operator names the proxy (here as a CIDR) in FORWARDED_ALLOW_IPS."""
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "127.0.0.0/8")


def test_reproduced_an_untrusted_proxy_puts_everyone_in_one_bucket(proxy_untrusted, server):
    attacker, owner = _attacker_then_owner(server)

    assert attacker[:10] == [401] * 10 and attacker[10] == 429, attacker
    assert owner == 429, "the owner is locked out by someone else's attempts (FR-68)"


def test_a_trusted_proxy_gives_each_client_its_own_bucket(proxy_trusted, server):
    attacker, owner = _attacker_then_owner(server)

    assert attacker[10] == 429, "the attacker is still limited"
    assert owner == 401, "the owner's wrong password is answered, not rate-limited"


def test_a_spoofed_hop_left_of_the_real_client_does_not_buy_a_new_bucket(proxy_trusted, server):
    """Right-most untrusted hop: a client cannot rotate a forged left-hand entry to dodge the limit."""
    url = _consent_url(server)
    codes = [_bad_sign_in(server, url, f"10.0.0.{i}, 203.0.113.66") for i in range(11)]

    assert codes[10] == 429, codes


def test_serve_http_leaves_proxy_trust_to_the_env(monkeypatch):
    """The tests above stand for production only if `serve_http` passes no override."""
    from AINDY.platform_layer import mcp_server

    seen = {}
    monkeypatch.setattr(mcp_server, "build_http_app", lambda host, path: (object(), "test"))
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: seen.update(kw))

    mcp_server.serve_http(host="127.0.0.1", port=1)

    assert seen, "liveness: serve_http reached uvicorn.run"
    assert "forwarded_allow_ips" not in seen and seen.get("proxy_headers", True) is True
