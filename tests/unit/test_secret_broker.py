"""AGENT-HARDEN-9 (PR1) — just-in-time, capability-scoped secrets broker."""
from __future__ import annotations

import pytest

from AINDY.platform_layer.secret_broker import (
    EnvSecretBroker,
    SecretBroker,
    SecretRef,
    clear_secret_scopes,
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
