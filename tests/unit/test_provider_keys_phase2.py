"""FR-59 phase 2 (DEC-121): a user's own key pays, through its own client and breaker.

Reuses phase 1's private engine, users, master key and mode fixtures.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.unit.test_provider_keys import (  # noqa: F401  (fixtures)
    ADMIN,
    ALICE,
    BOB,
    SECRET,
    _bearer,
    _put,
    client,
    db_session_factory,
    master_key,
    mode,
    test_engine,
    testing_session_factory,
    users,
)

pytestmark = pytest.mark.runtime_only


class _FakeRaw:
    """A provider client. ``fail_with`` makes every call raise it."""

    def __init__(self, api_key):
        self.api_key = api_key
        self.calls = 0
        self.fail_with = None

    def chat(self, messages, model=None, temperature=0.7, max_tokens=None):
        self.calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        return "ok"

    def call_method(self, name, *args, **kwargs):
        return self.chat(*args, **kwargs)

    def is_available(self):
        return True


class _AuthError(Exception):
    status_code = 401


@pytest.fixture
def raw(monkeypatch):
    from AINDY.platform_layer import llm_client

    built: list[_FakeRaw] = []

    def _build(provider, api_key):
        client = _FakeRaw(api_key)
        built.append(client)
        return client

    monkeypatch.setattr(llm_client, "_raw_client_for_key", _build)
    monkeypatch.setattr(llm_client, "_user_clients", llm_client.OrderedDict())
    return built


@pytest.fixture
def server_client(monkeypatch):
    """The server's client, a fake, so a test can prove it was never touched."""
    from AINDY.platform_layer import llm_client

    server = llm_client.CircuitBreakerLLMClient(_FakeRaw("sk-server-key"), provider="openai")
    monkeypatch.setitem(llm_client._PROVIDER_FACTORIES, "openai", lambda: server)
    return server


def _for_user(db_session_factory, user, provider="openai"):
    from AINDY.platform_layer.llm_client import get_llm_client_for_user

    session = db_session_factory()
    try:
        return get_llm_client_for_user(provider, str(user) if user else None, db=session)
    finally:
        session.close()


# -- choosing the client ------------------------------------------------------------------------


def test_server_mode_returns_exactly_todays_client(client, db_session_factory, mode, raw, server_client):
    assert _put(client).status_code == 200
    mode("server")

    assert _for_user(db_session_factory, ALICE) is server_client
    assert raw == [], "no user-key client is built in server mode"


def test_per_user_mode_pays_with_the_users_own_key(client, db_session_factory, mode, raw, server_client):
    from AINDY.platform_layer.llm_client import client_key_source

    assert _put(client).status_code == 200
    mode("per_user")
    chosen = _for_user(db_session_factory, ALICE)

    assert chosen is not server_client
    assert client_key_source(chosen) == "user"
    assert chosen.chat([{"role": "user", "content": "hi"}]) == "ok"
    assert raw[0].api_key == SECRET
    assert server_client._client.calls == 0


def test_an_operator_without_a_key_and_a_call_with_no_user_use_the_servers(db_session_factory, mode, raw, server_client, users):
    mode("per_user")
    assert _for_user(db_session_factory, ADMIN) is server_client
    assert _for_user(db_session_factory, None) is server_client
    assert raw == []


def test_a_user_without_a_key_is_refused(db_session_factory, mode, raw, server_client, users):
    from AINDY.platform_layer.provider_keys import ProviderKeyRequired

    mode("per_user")
    with pytest.raises(ProviderKeyRequired):
        _for_user(db_session_factory, BOB)
    assert server_client._client.calls == 0


def test_a_provider_with_no_runtime_client_is_refused_not_misrouted(client, db_session_factory, mode, server_client):
    """Perplexity is in the store for the app's own calls; the runtime has no client to build."""
    assert _put(client, provider="perplexity").status_code == 200
    mode("per_user")

    with pytest.raises(ValueError, match="no model client"):
        _for_user(db_session_factory, ALICE, provider="perplexity")
    assert server_client._client.calls == 0


# -- the cache and the breaker ------------------------------------------------------------------


def test_one_client_per_key_and_a_new_one_when_the_key_changes(client, db_session_factory, mode, raw):
    assert _put(client).status_code == 200
    mode("per_user")
    first = _for_user(db_session_factory, ALICE)
    again = _for_user(db_session_factory, ALICE)
    assert _put(client, key=SECRET + "-new").status_code == 200
    changed = _for_user(db_session_factory, ALICE)

    assert first is again
    assert changed is not first
    assert len(raw) == 2


def test_the_cache_is_bounded(client, db_session_factory, mode, raw, monkeypatch):
    from AINDY.platform_layer import llm_client

    monkeypatch.setattr(llm_client, "_USER_CLIENT_CACHE_MAX", 1)
    mode("per_user")
    assert _put(client, user=ALICE).status_code == 200
    assert _put(client, user=BOB, key="sk-bob-key-0000").status_code == 200
    _for_user(db_session_factory, ALICE)
    _for_user(db_session_factory, BOB)

    assert len(llm_client._user_clients) == 1


def test_a_failing_user_key_opens_only_its_own_breaker(client, db_session_factory, mode, raw, server_client):
    from AINDY.kernel.circuit_breaker import CircuitState
    from AINDY.platform_layer.llm_client import LLMCallError

    assert _put(client).status_code == 200
    mode("per_user")
    chosen = _for_user(db_session_factory, ALICE)
    raw[0].fail_with = RuntimeError("provider down")

    for _ in range(6):
        with pytest.raises(LLMCallError):
            chosen.chat([{"role": "user", "content": "hi"}])

    from AINDY.platform_layer.openai_client import get_openai_circuit_breaker

    assert chosen.breaker.state is CircuitState.OPEN
    assert server_client.breaker.state is CircuitState.CLOSED, "a user's key must never open the shared breaker"
    assert chosen.breaker is not server_client.breaker
    assert chosen.breaker is not get_openai_circuit_breaker(), "the real shared OpenAI breaker either"


# -- a rejected key -----------------------------------------------------------------------------


def test_a_rejected_key_raises_and_never_falls_back_to_the_servers(client, db_session_factory, mode, raw, server_client):
    from AINDY.platform_layer.llm_client import LLMCallError
    from AINDY.platform_layer.provider_keys import ProviderKeyRejected

    assert _put(client).status_code == 200
    mode("per_user")
    chosen = _for_user(db_session_factory, ALICE)
    try:
        raise LLMCallError("openai chat completion failed") from _AuthError("invalid api key")
    except LLMCallError as wrapped:
        raw[0].fail_with = wrapped

    with pytest.raises(ProviderKeyRejected) as rejected:
        chosen.chat([{"role": "user", "content": "hi"}])

    assert isinstance(rejected.value, LLMCallError), "existing LLM error handling still catches it"
    assert rejected.value.key_hint == "zz9Q"
    assert SECRET not in str(rejected.value)
    assert server_client._client.calls == 0


def test_control_another_failure_is_not_called_a_rejected_key(client, db_session_factory, mode, raw):
    from AINDY.platform_layer.llm_client import LLMCallError
    from AINDY.platform_layer.provider_keys import ProviderKeyRejected

    assert _put(client).status_code == 200
    mode("per_user")
    chosen = _for_user(db_session_factory, ALICE)
    raw[0].fail_with = RuntimeError("timeout")

    with pytest.raises(LLMCallError) as failed:
        chosen.chat([{"role": "user", "content": "hi"}])
    assert not isinstance(failed.value, ProviderKeyRejected)


# -- the planner --------------------------------------------------------------------------------


@pytest.fixture
def planner(monkeypatch):
    from AINDY.agents.agent_runtime import planning

    sentinel = object()
    monkeypatch.setattr(planning, "get_openai_client", lambda: sentinel)
    seen: list[dict] = []

    def _external(**kwargs):
        seen.append(kwargs)
        return {"plan": "ok"}

    monkeypatch.setattr(planning, "perform_external_call", _external)
    return planning, sentinel, seen


def test_the_planner_keeps_todays_client_in_server_mode(planner, db_session_factory, mode, users):
    planning, sentinel, seen = planner
    session = db_session_factory()
    try:
        assert planning._planner_client(ALICE, session) == (sentinel, "server")
        planning._planner_chat(system_prompt="s", objective="o", user_id=str(ALICE), metadata={"a": 1}, db=session)
    finally:
        session.close()
    assert seen[0]["extra"] == {"a": 1, "key_source": "server"}


def test_the_planner_pays_with_the_users_key(client, planner, db_session_factory, mode, raw):
    planning, sentinel, seen = planner
    assert _put(client).status_code == 200
    mode("per_user")
    session = db_session_factory()
    try:
        chosen, source = planning._planner_client(ALICE, session)
        planning._planner_chat(system_prompt="s", objective="o", user_id=str(ALICE), metadata={}, db=session)
    finally:
        session.close()

    assert chosen is not sentinel and source == "user"
    assert seen[0]["extra"]["key_source"] == "user"


def test_the_planner_refuses_a_user_without_a_key(planner, db_session_factory, mode, users):
    from AINDY.platform_layer.provider_keys import ProviderKeyRequired

    planning, _sentinel, seen = planner
    mode("per_user")
    session = db_session_factory()
    try:
        with pytest.raises(ProviderKeyRequired):
            planning._planner_chat(system_prompt="s", objective="o", user_id=str(BOB), metadata={}, db=session)
    finally:
        session.close()
    assert seen == [], "no call was made on anyone's key"


# -- audit events -------------------------------------------------------------------------------


def _events(db_session_factory, kind):
    session = db_session_factory()
    try:
        return session.execute(text("SELECT payload FROM system_events WHERE type = :t"), {"t": kind}).all()
    finally:
        session.close()


def test_setting_and_deleting_a_key_are_audited_without_the_key(client, db_session_factory):
    assert _put(client).status_code == 200
    assert client.delete("/auth/provider-keys/openai", headers=_bearer()).status_code == 204

    set_events = _events(db_session_factory, "provider_key.set")
    deleted = _events(db_session_factory, "provider_key.deleted")
    assert len(set_events) == 1 and len(deleted) == 1
    assert "zz9Q" in str(set_events[0]) and "openai" in str(deleted[0])
    assert SECRET not in str(set_events) + str(deleted)
