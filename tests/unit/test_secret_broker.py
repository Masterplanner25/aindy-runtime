"""AGENT-HARDEN-9 (PR1) — just-in-time, capability-scoped secrets broker."""
from __future__ import annotations

import pytest

from AINDY.platform_layer.secret_broker import (
    ChainSecretBroker,
    EnvSecretBroker,
    FileSecretBroker,
    SecretBroker,
    SecretRef,
    VaultSecretBroker,
    capability_scope,
    clear_secret_scopes,
    current_capabilities,
    get_secret_broker,
    register_secret_scope,
    resolve_secret,
    set_secret_broker,
)

pytestmark = pytest.mark.runtime_only


@pytest.fixture(autouse=True)
def _clean():
    clear_secret_scopes()
    set_secret_broker(None)
    yield
    clear_secret_scopes()
    set_secret_broker(None)


class _FakeBroker(SecretBroker):
    def __init__(self, values):
        self._values = values

    def fetch(self, name):
        return self._values.get(name)


# --------------------------------------------------------------------------- #
# EnvSecretBroker — controlled namespace, not arbitrary env
# --------------------------------------------------------------------------- #

def test_env_broker_reads_only_prefixed_namespace(monkeypatch):
    monkeypatch.setenv("AINDY_SECRET_STRIPE_KEY", "sk_live_x")
    monkeypatch.setenv("STRIPE_KEY", "raw-env-should-not-be-read")
    broker = EnvSecretBroker()
    assert broker.fetch("stripe_key") == "sk_live_x"      # AINDY_SECRET_STRIPE_KEY
    assert broker.fetch("nonexistent") is None


# --------------------------------------------------------------------------- #
# resolve_secret — JIT + capability scoping
# --------------------------------------------------------------------------- #

def test_resolve_denied_without_gating_capability():
    register_secret_scope("stripe_key", "payments.charge")
    set_secret_broker(_FakeBroker({"stripe_key": "sk_live_x"}))

    denied = resolve_secret("stripe_key", capabilities=["memory.read"])
    assert denied["ok"] is False
    assert "requires capability 'payments.charge'" in denied["error"]
    assert "value" not in denied  # never leaks the secret on denial


def test_resolve_allowed_with_gating_capability():
    register_secret_scope("stripe_key", "payments.charge")
    set_secret_broker(_FakeBroker({"stripe_key": "sk_live_x"}))

    ok = resolve_secret("stripe_key", capabilities=["payments.charge", "memory.read"])
    assert ok == {"ok": True, "value": "sk_live_x"}


def test_resolve_explicit_required_capability_overrides_registry():
    set_secret_broker(_FakeBroker({"k": "v"}))
    denied = resolve_secret("k", capabilities=[], required_capability="secrets.read")
    assert denied["ok"] is False
    ok = resolve_secret("k", capabilities=["secrets.read"], required_capability="secrets.read")
    assert ok["value"] == "v"


def test_resolve_ungated_secret_is_open_in_dev():
    set_secret_broker(_FakeBroker({"k": "v"}))
    assert resolve_secret("k", capabilities=[])["value"] == "v"  # no scope registered


def test_resolve_missing_secret_fails_closed():
    set_secret_broker(_FakeBroker({}))
    r = resolve_secret("absent", capabilities=[])
    assert r["ok"] is False and "not found" in r["error"]


def test_resolve_backend_error_fails_closed():
    class _Boom(SecretBroker):
        def fetch(self, name):
            raise RuntimeError("vault down")

    set_secret_broker(_Boom())
    r = resolve_secret("k", capabilities=[])
    assert r["ok"] is False and "secret broker error" in r["error"]


# --------------------------------------------------------------------------- #
# Pluggability
# --------------------------------------------------------------------------- #

def test_default_broker_is_env_backed():
    assert isinstance(get_secret_broker(), EnvSecretBroker)


def test_set_and_reset_broker():
    fake = _FakeBroker({"k": "v"})
    set_secret_broker(fake)
    assert get_secret_broker() is fake
    set_secret_broker(None)
    assert isinstance(get_secret_broker(), EnvSecretBroker)


def test_secret_ref_holds_no_value():
    ref = SecretRef(name="stripe_key", required_capability="payments.charge")
    assert ref.name == "stripe_key" and ref.required_capability == "payments.charge"
    assert not hasattr(ref, "value")


# --------------------------------------------------------------------------- #
# FileSecretBroker — Docker/K8s mounted secrets
# --------------------------------------------------------------------------- #

def test_file_broker_reads_mounted_secret(tmp_path):
    (tmp_path / "db_password").write_text("s3cr3t\n", encoding="utf-8")
    broker = FileSecretBroker(root=str(tmp_path))
    assert broker.fetch("db_password") == "s3cr3t"
    assert broker.fetch("absent") is None


def test_file_broker_blocks_path_traversal(tmp_path):
    broker = FileSecretBroker(root=str(tmp_path))
    assert broker.fetch("../etc/passwd") is None
    assert broker.fetch("a/b") is None


# --------------------------------------------------------------------------- #
# VaultSecretBroker — KV v2 over httpx (contract-tested with respx)
# --------------------------------------------------------------------------- #

def test_vault_broker_reads_kv_v2():
    import httpx
    import respx

    with respx.mock:
        route = respx.get("https://vault.local:8200/v1/secret/data/api_key").mock(
            return_value=httpx.Response(200, json={"data": {"data": {"value": "vault-secret"}}})
        )
        broker = VaultSecretBroker(addr="https://vault.local:8200", token="vault-token")
        assert broker.fetch("api_key") == "vault-secret"
        assert route.calls.last.request.headers["x-vault-token"] == "vault-token"


def test_vault_broker_missing_returns_none():
    import httpx
    import respx

    with respx.mock:
        respx.get("https://vault.local:8200/v1/secret/data/nope").mock(
            return_value=httpx.Response(404, json={"errors": []})
        )
        broker = VaultSecretBroker(addr="https://vault.local:8200", token="t")
        assert broker.fetch("nope") is None


# --------------------------------------------------------------------------- #
# ChainSecretBroker — ordered fallback
# --------------------------------------------------------------------------- #

def test_chain_returns_first_non_empty():
    chain = ChainSecretBroker(_FakeBroker({}), _FakeBroker({"k": "from-second"}), _FakeBroker({"k": "third"}))
    assert chain.fetch("k") == "from-second"
    assert chain.fetch("absent") is None


def test_chain_skips_failing_backend():
    class _Boom(SecretBroker):
        def fetch(self, name):
            raise RuntimeError("down")

    chain = ChainSecretBroker(_Boom(), _FakeBroker({"k": "ok"}))
    assert chain.fetch("k") == "ok"


# --------------------------------------------------------------------------- #
# Ambient capability scope (the tool seam)
# --------------------------------------------------------------------------- #

def test_resolve_secret_uses_ambient_capability_scope():
    register_secret_scope("k", "db.read")
    set_secret_broker(_FakeBroker({"k": "v"}))

    # Outside any scope → denied.
    assert resolve_secret("k")["ok"] is False
    with capability_scope(["db.read"]):
        assert current_capabilities() == ("db.read",)
        assert resolve_secret("k") == {"ok": True, "value": "v"}
    # Scope is restored afterward.
    assert resolve_secret("k")["ok"] is False


def test_execute_tool_scopes_secret_to_token_capabilities(monkeypatch):
    from AINDY.agents import tool_registry as tr

    register_secret_scope("db_password", "db.read")
    set_secret_broker(_FakeBroker({"db_password": "s3cr3t"}))

    seen = {}

    def _tool(args, user_id, db):
        seen["secret"] = resolve_secret("db_password")  # no caps arg → ambient token scope
        return {"ran": True}

    monkeypatch.setattr(tr, "_ensure_tools_loaded", lambda: None)
    monkeypatch.setattr(tr, "queue_system_event", lambda **kw: None)
    monkeypatch.setattr("AINDY.agents.capability_policy.has_capability_policies", lambda: False)
    monkeypatch.setitem(tr.TOOL_REGISTRY, "db_tool", {"fn": _tool})

    # Token grants db.read → the tool resolves the secret.
    monkeypatch.setattr(
        "AINDY.agents.capability_service.check_tool_capability",
        lambda **kw: {"ok": True, "allowed_capabilities": ["db.read", "misc"]},
    )
    ok = tr.execute_tool("db_tool", {}, user_id="u", db=object(), run_id="r", execution_token={"token_hash": "h"})
    assert ok["success"] is True
    assert seen["secret"] == {"ok": True, "value": "s3cr3t"}

    # Token WITHOUT db.read → the same tool is denied the secret (fail-closed).
    seen.clear()
    monkeypatch.setattr(
        "AINDY.agents.capability_service.check_tool_capability",
        lambda **kw: {"ok": True, "allowed_capabilities": ["misc"]},
    )
    tr.execute_tool("db_tool", {}, user_id="u", db=object(), run_id="r", execution_token={"token_hash": "h"})
    assert seen["secret"]["ok"] is False and "requires capability 'db.read'" in seen["secret"]["error"]
