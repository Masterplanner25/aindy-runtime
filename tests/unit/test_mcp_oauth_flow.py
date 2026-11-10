"""OAuth for the MCP endpoint, end to end (FR-66 phase 2, DEC-129).

The real app from `build_http_app()` with `AINDY_MCP_OAUTH` on, served under uvicorn, driven the
way ChatGPT and claude.ai drive it: discovery from a 401, dynamic registration, `/authorize` with
PKCE, the sign-in and consent page, `/token`, then a real MCP call with the bearer token through
the SDK client. Then each refusal the design lists (§8), each beside a control that passes.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from sqlalchemy.orm import sessionmaker

from tests.fixtures.db import build_private_engine

pytest.importorskip("nodus_mcp_aindy")
uvicorn = pytest.importorskip("uvicorn")

from mcp import ClientSession  # noqa: E402

try:
    from mcp.client.streamable_http import streamable_http_client
except ImportError:  # mcp 1.x before the rename
    streamable_http_client = None
    from mcp.client.streamable_http import streamablehttp_client

try:  # mcp 2.x's client is built on httpx2; 1.x on httpx
    import httpx2 as _httpx
except ImportError:
    import httpx as _httpx

pytestmark = pytest.mark.runtime_only

ALICE = uuid.UUID("00000000-0000-0000-0000-0000000a1d0a")
EMAIL, PASSWORD = "alice-oauth@example.test", "correct horse battery"
TOOL = "sys.v1.memory.list"
REDIRECT = "http://127.0.0.1:9/callback"


# -- serving -----------------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Served:
    def __init__(self, app, port: int):
        self.port = port
        self._server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self):
        self._thread.start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/nothing", timeout=1)
                return self
            except urllib.error.HTTPError:
                return self
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("uvicorn did not come up")

    def __exit__(self, *exc):
        self._server.should_exit = True
        self._thread.join(10)


@pytest.fixture(autouse=True)
def _scopes_column_works_on_sqlite(monkeypatch):
    from sqlalchemy import JSON

    from AINDY.db.models.api_key import PlatformAPIKey

    monkeypatch.setattr(PlatformAPIKey.__table__.c.scopes, "type", JSON())


@pytest.fixture
def factory(tmp_path, monkeypatch):
    from AINDY.db.models.user import User
    from AINDY.services.auth_service import hash_password

    engine = build_private_engine(tmp_path / "mcp_oauth.db")
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("AINDY.db.database.SessionLocal", session_factory)
    db = session_factory()
    db.add(User(id=ALICE, email=EMAIL, hashed_password=hash_password(PASSWORD), is_active=True))
    db.commit()
    db.close()
    try:
        yield session_factory
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def _clean_state():
    from AINDY.kernel import effect_ledger
    from AINDY.platform_layer import mcp_oauth

    token = effect_ledger.set_effect_attribution(tenant_id=None, session_id=None)
    mcp_oauth._grant_cache.clear()
    mcp_oauth._attempts.clear()
    try:
        yield
    finally:
        effect_ledger.reset_effect_attribution(token)


@pytest.fixture
def server(factory, monkeypatch):
    """The OAuth-enabled HTTP app on a free port, its public URL pointing at itself."""
    from AINDY.platform_layer import mcp_server

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    for name in ("AINDY_MCP_SERVER_MULTI_TENANT", "AINDY_MCP_SERVER_USER_ID", "AINDY_MCP_SERVER_ALLOWED_HOSTS",
                 "AINDY_MCP_SERVER_ALLOW_WRITES", "AINDY_MCP_OAUTH_CLIENTS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AINDY_MCP_OAUTH", "on")
    monkeypatch.setenv("AINDY_MCP_PUBLIC_URL", base)
    monkeypatch.setenv("AINDY_MCP_SERVER_TOOLS", TOOL)
    app, _ = mcp_server.build_http_app(host="127.0.0.1")
    with _Served(app, port):
        yield base


# -- the client side of the flow ---------------------------------------------------------------


def _http(**kwargs):
    return _httpx.Client(timeout=30, follow_redirects=False, **kwargs)


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def _register(base: str, redirect: str = REDIRECT, **extra) -> dict:
    body = {
        "redirect_uris": [redirect], "client_name": "Test Connector",
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], **extra,
    }
    with _http() as c:
        return c.post(f"{base}/register", json=body).json()


def _authorize(base: str, client_id: str, challenge: str, **overrides):
    params = {
        "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": "st-1",
        "resource": f"{base}/mcp", **overrides,
    }
    with _http() as c:
        return c.get(f"{base}/authorize?{urlencode(params)}")


def _consent(base: str, consent_url: str, *, action="approve", scopes=("memory.read",), password=PASSWORD,
             csrf_override=None):
    with _http() as c:
        page = c.get(consent_url)
        assert page.status_code == 200, page.text
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
        pending = re.search(r'name="p" value="([^"]+)"', page.text).group(1)
        form = {"p": pending, "csrf": csrf_override or csrf, "email": EMAIL, "password": password,
                "action": action, "scope": list(scopes)}
        return c.post(f"{base}/oauth/consent", data=form), page


def _token(base: str, **form):
    with _http() as c:
        return c.post(f"{base}/token", data=form)


def _authorized(base: str, scopes=("memory.read",)) -> dict:
    """The whole dance; returns the token response plus what was used to get it."""
    client = _register(base)
    verifier, challenge = _pkce()
    auth = _authorize(base, client["client_id"], challenge)
    assert auth.status_code == 302, auth.text
    done, _ = _consent(base, auth.headers["location"], scopes=scopes)
    assert done.status_code == 302, done.text
    query = parse_qs(urlparse(done.headers["location"]).query)
    tokens = _token(base, grant_type="authorization_code", code=query["code"][0], redirect_uri=REDIRECT,
                    client_id=client["client_id"], code_verifier=verifier)
    assert tokens.status_code == 200, tokens.text
    return {"client": client, "verifier": verifier, "code": query["code"][0], **tokens.json()}


async def _with_session(url: str, headers: dict, fn):
    if streamable_http_client is None:
        async with streamablehttp_client(url, headers=headers) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                return await fn(session)
    async with _httpx.AsyncClient(headers=headers, timeout=30) as http:
        async with streamable_http_client(url, http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                return await fn(session)


def _call_tool(base: str, access_token: str) -> dict:
    async def _fn(session):
        tools = await session.list_tools()
        result = await session.call_tool(tools.tools[0].name, {"path": f"/memory/{ALICE}/**"})
        return json.loads("".join(getattr(c, "text", "") for c in result.content))

    return asyncio.run(_with_session(f"{base}/mcp", {"Authorization": f"Bearer {access_token}"}, _fn))


def _mcp_status(base: str, token: str | None) -> int:
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
    with _http() as c:
        return c.post(f"{base}/mcp", json=body, headers=headers).status_code


def _claims(token: str) -> dict:
    """An access token's claims, read in the test without checking the audience."""
    import jwt

    from AINDY.platform_layer import mcp_oauth

    key = mcp_oauth._domain_keys(mcp_oauth._ACCESS_DOMAIN)[0]
    return jwt.decode(token, key, algorithms=["HS256"], options={"verify_aud": False})


# -- the flow ----------------------------------------------------------------------------------


def test_discovery_starts_from_a_401_on_the_mcp_endpoint(server):
    with _http() as c:
        refused = c.post(f"{server}/mcp", json={})
        assert refused.status_code == 401
        challenge = refused.headers.get("www-authenticate", "")
        assert "resource_metadata=" in challenge
        resource = c.get(f"{server}/.well-known/oauth-protected-resource/mcp").json()
        assert resource["resource"].rstrip("/") == f"{server}/mcp"
        metadata = c.get(f"{server}/.well-known/oauth-authorization-server").json()
        assert metadata["issuer"].rstrip("/") == server
        assert "S256" in metadata["code_challenge_methods_supported"]


def test_the_whole_flow_ends_in_a_real_tool_call(server):
    tokens = _authorized(server)

    assert tokens["token_type"].lower() == "bearer"
    assert tokens["scope"] == "memory.read"
    envelope = _call_tool(server, tokens["access_token"])
    assert envelope["status"] == "success", envelope


def test_a_grant_without_the_tools_scope_is_refused_at_the_tool(server, factory, monkeypatch):
    """Consent to nothing the tool needs is impossible from the page (only offered scopes), so hold a
    token whose scope the tool doesn't accept: refused with `permission`, like a key (DEC-126)."""
    from AINDY.platform_layer import mcp_oauth

    tokens = _authorized(server)
    claims = _claims(tokens["access_token"])
    claims["scope"] = "flow.execute"
    forged_scope = mcp_oauth._sign(claims, mcp_oauth._ACCESS_DOMAIN)  # validly signed, wrong scope

    envelope = _call_tool(server, forged_scope)
    assert envelope["status"] == "error"
    assert envelope["failure_class"] == "permission"


# -- refusals (design §8), each with its control -----------------------------------------------


def test_a_wrong_pkce_verifier_is_refused(server):
    client = _register(server)
    _, challenge = _pkce()
    auth = _authorize(server, client["client_id"], challenge)
    done, _ = _consent(server, auth.headers["location"])
    code = parse_qs(urlparse(done.headers["location"]).query)["code"][0]

    wrong = _token(server, grant_type="authorization_code", code=code, redirect_uri=REDIRECT,
                   client_id=client["client_id"], code_verifier=secrets.token_urlsafe(48))
    assert wrong.status_code == 400
    assert wrong.json()["error"] == "invalid_grant"


def test_a_code_works_once(server):
    tokens = _authorized(server)  # control: the code worked
    again = _token(server, grant_type="authorization_code", code=tokens["code"], redirect_uri=REDIRECT,
                   client_id=tokens["client"]["client_id"], code_verifier=tokens["verifier"])
    assert again.status_code == 400
    assert again.json()["error"] == "invalid_grant"


def test_an_unregistered_redirect_is_refused(server):
    client = _register(server)
    _, challenge = _pkce()
    refused = _authorize(server, client["client_id"], challenge, redirect_uri="http://127.0.0.1:9/callbacK")
    assert refused.status_code == 400


def test_a_foreign_resource_is_refused(server):
    client = _register(server)
    _, challenge = _pkce()
    refused = _authorize(server, client["client_id"], challenge, resource="https://elsewhere.example/mcp")
    location = refused.headers.get("location", "")
    assert refused.status_code in (302, 400)
    assert refused.status_code == 400 or "error=invalid_request" in location


def test_a_registration_with_a_plain_http_redirect_is_refused(server):
    refused = _register(server, redirect="http://evil.example/cb")
    assert refused.get("error") == "invalid_redirect_uri"
    assert "client_id" in _register(server)  # control


def test_closed_registration_refuses(server, monkeypatch, factory):
    """`AINDY_MCP_OAUTH_CLIENTS=closed` is read per call by the store (the route's own switch is
    fixed at build time); an operator registers with the CLI instead."""
    monkeypatch.setenv("AINDY_MCP_OAUTH_CLIENTS", "closed")
    refused = _register(server)
    assert "client_id" not in refused

    from AINDY.platform_layer.mcp_oauth import load_client, register_client_cli

    made = register_client_cli(name="Operator client", redirect_uris=[REDIRECT], public=True)
    assert load_client(made["client_id"]).client_name == "Operator client"


def test_deny_sends_access_denied_back(server):
    client = _register(server)
    _, challenge = _pkce()
    auth = _authorize(server, client["client_id"], challenge)
    denied, _ = _consent(server, auth.headers["location"], action="deny")
    assert denied.status_code == 302
    query = parse_qs(urlparse(denied.headers["location"]).query)
    assert query["error"] == ["access_denied"] and query["state"] == ["st-1"]


def test_a_wrong_password_is_refused_and_issues_nothing(server):
    client = _register(server)
    _, challenge = _pkce()
    auth = _authorize(server, client["client_id"], challenge)
    refused, _ = _consent(server, auth.headers["location"], password="wrong")
    assert refused.status_code == 401
    assert "location" not in refused.headers


def test_a_forged_csrf_field_is_refused(server):
    client = _register(server)
    _, challenge = _pkce()
    auth = _authorize(server, client["client_id"], challenge)
    refused, _ = _consent(server, auth.headers["location"], csrf_override="forged")
    assert refused.status_code == 400


def test_the_consent_page_shows_the_redirect_host_and_cannot_be_framed(server):
    client = _register(server)
    _, challenge = _pkce()
    auth = _authorize(server, client["client_id"], challenge)
    with _http() as c:
        page = c.get(auth.headers["location"])
    assert "127.0.0.1:9" in page.text
    assert page.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]


def test_a_session_jwt_is_not_an_mcp_token(server):
    from AINDY.services.auth_service import create_access_token

    session_jwt = create_access_token({"sub": str(ALICE)})
    assert _mcp_status(server, session_jwt) == 401


def test_an_mcp_token_is_not_a_session_token(server):
    from fastapi import HTTPException

    from AINDY.services.auth_service import decode_access_token

    tokens = _authorized(server)
    with pytest.raises(HTTPException):
        decode_access_token(tokens["access_token"])


def test_refresh_rotates_and_a_replayed_refresh_revokes_the_grant(server):
    tokens = _authorized(server)
    client_id = tokens["client"]["client_id"]

    fresh = _token(server, grant_type="refresh_token", refresh_token=tokens["refresh_token"], client_id=client_id)
    assert fresh.status_code == 200, fresh.text  # control: refresh works
    new = fresh.json()
    assert new["refresh_token"] != tokens["refresh_token"]
    assert _mcp_status(server, new["access_token"]) != 401

    replay = _token(server, grant_type="refresh_token", refresh_token=tokens["refresh_token"], client_id=client_id)
    assert replay.status_code == 400
    assert _mcp_status(server, new["access_token"]) == 401  # the grant is gone


def test_revoking_the_grant_ends_the_access_token(server):
    from AINDY.platform_layer import mcp_oauth

    tokens = _authorized(server)
    assert _mcp_status(server, tokens["access_token"]) != 401  # control
    claims = _claims(tokens["access_token"])

    assert mcp_oauth.revoke_grant_id(claims["gid"]) is True
    assert _mcp_status(server, tokens["access_token"]) == 401


def test_a_password_change_ends_every_grant(server, factory):
    from AINDY.db.models.user import User
    from AINDY.platform_layer import mcp_oauth

    tokens = _authorized(server)
    db = factory()
    user = db.get(User, ALICE)
    user.token_version = (user.token_version or 0) + 1
    db.commit()
    db.close()
    mcp_oauth._grant_cache.clear()  # what GRANT_CHECK_CACHE_S would do within seconds

    assert _mcp_status(server, tokens["access_token"]) == 401
    refreshed = _token(server, grant_type="refresh_token", refresh_token=tokens["refresh_token"],
                       client_id=tokens["client"]["client_id"])
    assert refreshed.status_code == 400


def test_secrets_are_never_stored_in_the_clear(server, factory):
    from AINDY.db.models.mcp_oauth import McpOAuthClient, McpOAuthGrant

    client = _register(server, token_endpoint_auth_method="client_secret_post")
    assert client.get("client_secret")
    tokens = _authorized(server)

    db = factory()
    try:
        stored = db.get(McpOAuthClient, client["client_id"])
        assert client["client_secret"] not in (stored.client_info + (stored.client_secret_ciphertext or ""))
        grants = db.query(McpOAuthGrant).all()
        dump = " ".join(str(getattr(g, c.name)) for g in grants for c in McpOAuthGrant.__table__.columns)
        assert tokens["refresh_token"] not in dump and tokens["code"] not in dump
    finally:
        db.close()


# -- the audit record: one event per grant made and per grant ended, with why --------------------


def _grant_events(factory) -> list[tuple[str, dict]]:
    from AINDY.db.models.system_event import SystemEvent

    db = factory()
    try:
        rows = (db.query(SystemEvent).filter(SystemEvent.type.like("mcp_grant.%"))
                .order_by(SystemEvent.timestamp).all())
        return [(r.type, dict(r.payload or {})) for r in rows]
    finally:
        db.close()


def _revocations(factory) -> list[str]:
    return [p["reason"] for t, p in _grant_events(factory) if t == "mcp_grant.revoked"]


def test_consent_records_the_grant_and_nothing_secret(server, factory):
    tokens = _authorized(server)
    claims = _claims(tokens["access_token"])

    events = _grant_events(factory)
    assert [t for t, _ in events] == ["mcp_grant.created"]
    payload = events[0][1]
    assert payload == {
        "grant_id": claims["gid"], "client_id": tokens["client"]["client_id"],
        "scopes": ["memory.read"], "redirect_host": "127.0.0.1:9",
    }
    dump = repr(events)
    for secret in (tokens["code"], tokens["refresh_token"], tokens["access_token"], tokens["verifier"]):
        assert secret not in dump


def test_consenting_again_records_the_earlier_grant_as_superseded(server, factory):
    client = _register(server)
    for _ in range(2):
        _, challenge = _pkce()
        auth = _authorize(server, client["client_id"], challenge)
        done, _ = _consent(server, auth.headers["location"])
        assert done.status_code == 302, done.text

    events = _grant_events(factory)
    assert [t for t, _ in events] == ["mcp_grant.created", "mcp_grant.revoked", "mcp_grant.created"]
    assert events[1][1]["reason"] == "superseded"
    assert events[1][1]["grant_id"] == events[0][1]["grant_id"] != events[2][1]["grant_id"]


def test_a_replayed_refresh_is_recorded_as_the_reason(server, factory):
    tokens = _authorized(server)
    client_id = tokens["client"]["client_id"]
    assert _token(server, grant_type="refresh_token", refresh_token=tokens["refresh_token"],
                  client_id=client_id).status_code == 200
    assert _revocations(factory) == []  # control: a rotation ends nothing

    _token(server, grant_type="refresh_token", refresh_token=tokens["refresh_token"], client_id=client_id)
    assert _revocations(factory) == ["refresh_replay"]


def test_a_password_change_is_recorded_when_the_refresh_is_refused(server, factory):
    from AINDY.db.models.user import User

    tokens = _authorized(server)
    db = factory()
    user = db.get(User, ALICE)
    user.token_version = (user.token_version or 0) + 1
    db.commit()
    db.close()

    assert _token(server, grant_type="refresh_token", refresh_token=tokens["refresh_token"],
                  client_id=tokens["client"]["client_id"]).status_code == 400
    assert _revocations(factory) == ["account_changed"]


def test_a_client_revoking_is_recorded(server, factory):
    from AINDY.platform_layer import mcp_oauth

    by_access = _authorized(server)
    assert mcp_oauth.revoke_grant_id(_claims(by_access["access_token"])["gid"]) is True
    by_refresh = _authorized(server)
    with _http() as c:
        revoked = c.post(f"{server}/revoke", data={
            "token": by_refresh["refresh_token"], "token_type_hint": "refresh_token",
            "client_id": by_refresh["client"]["client_id"],
            # mcp 1.x declares `client_secret: str | None` with no default, so even a public
            # client must send the field; empty is what one would send.
            "client_secret": "",
        })
    assert revoked.status_code == 200, revoked.text

    assert _revocations(factory) == ["client", "client"]
    assert mcp_oauth.revoke_grant_id(_claims(by_access["access_token"])["gid"]) is False  # already ended
    assert _revocations(factory) == ["client", "client"]  # ending it twice records it once
