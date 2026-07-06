"""AGENT-HARDEN-5 — cross-provider LLM fallback chain.

Pins the resilience contract: on a primary-provider breaker-open / call error,
``FallbackLLMClient`` transparently fails over to the next configured provider;
chain resolution honors config, de-dupes, and drops unknown providers.
"""
from __future__ import annotations

import pytest

from AINDY.kernel.circuit_breaker import CircuitBreaker
from AINDY.platform_layer import llm_client as mod
from AINDY.platform_layer.llm_client import (
    CircuitBreakerLLMClient,
    FallbackLLMClient,
    LLMCallError,
    LLMCircuitOpenError,
    get_llm_client_chain,
    resolve_provider_chain,
)

pytestmark = pytest.mark.runtime_only


class _FakeClient:
    """Minimal LLMClient: returns a fixed reply, or raises a configured error."""

    def __init__(self, reply: str = "", *, raises: Exception | None = None, available: bool = True):
        self._reply = reply
        self._raises = raises
        self._available = available
        self.calls = 0

    def chat(self, messages, model=None, temperature=0.7, max_tokens=None) -> str:
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._reply

    def is_available(self) -> bool:
        return self._available


# --------------------------------------------------------------------------- #
# FallbackLLMClient — failover semantics
# --------------------------------------------------------------------------- #

def test_failover_on_llm_call_error():
    primary = _FakeClient(raises=LLMCallError("primary down"))
    secondary = _FakeClient(reply="from-secondary")
    chain = FallbackLLMClient([primary, secondary], providers=["openai", "deepseek"])

    assert chain.chat([{"role": "user", "content": "hi"}]) == "from-secondary"
    assert primary.calls == 1 and secondary.calls == 1


def test_primary_success_short_circuits():
    primary = _FakeClient(reply="from-primary")
    secondary = _FakeClient(reply="from-secondary")
    chain = FallbackLLMClient([primary, secondary], providers=["openai", "deepseek"])

    assert chain.chat([{"role": "user", "content": "hi"}]) == "from-primary"
    assert secondary.calls == 0  # never reached


def test_all_providers_fail_raises_last_error():
    primary = _FakeClient(raises=LLMCallError("primary down"))
    secondary = _FakeClient(raises=LLMCircuitOpenError("secondary open"))
    chain = FallbackLLMClient([primary, secondary], providers=["openai", "deepseek"])

    with pytest.raises(LLMCircuitOpenError):
        chain.chat([{"role": "user", "content": "hi"}])


def test_is_available_true_if_any_provider_available():
    chain = FallbackLLMClient(
        [_FakeClient(available=False), _FakeClient(available=True)],
    )
    assert chain.is_available() is True


def test_empty_chain_rejected():
    with pytest.raises(ValueError):
        FallbackLLMClient([])


# --------------------------------------------------------------------------- #
# Open primary breaker → secondary used (the close trigger)
# --------------------------------------------------------------------------- #

def test_open_primary_breaker_fails_over_to_secondary():
    # Primary: a breaker-wrapped client whose provider always errors, with a
    # threshold of 1 so a single failure opens it.
    primary_breaker = CircuitBreaker(name="primary", failure_threshold=1, recovery_timeout_secs=3600)
    primary = CircuitBreakerLLMClient(
        _FakeClient(raises=RuntimeError("upstream 500")),
        provider="primary",
        breaker=primary_breaker,
    )
    secondary = CircuitBreakerLLMClient(
        _FakeClient(reply="from-secondary"),
        provider="secondary",
    )

    # Trip the primary breaker so it is OPEN going into the fallback call.
    with pytest.raises(LLMCallError):
        primary.chat([{"role": "user", "content": "warmup"}])
    assert primary_breaker.state.value == "open"

    chain = FallbackLLMClient([primary, secondary], providers=["primary", "secondary"])
    # Now the primary rejects with LLMCircuitOpenError (breaker open) and the
    # chain transparently uses the secondary.
    assert chain.chat([{"role": "user", "content": "hi"}]) == "from-secondary"


# --------------------------------------------------------------------------- #
# resolve_provider_chain — config-driven, de-duped, unknowns dropped
# --------------------------------------------------------------------------- #

def test_resolve_chain_explicit_dedupe_and_drop_unknown():
    assert resolve_provider_chain(["openai", "deepseek", "openai", "bogus"]) == ["openai", "deepseek"]


def test_resolve_chain_empty_defaults_to_openai():
    assert resolve_provider_chain(["nope", ""]) == ["openai"]


def test_resolve_chain_from_settings(monkeypatch):
    from AINDY.config import settings

    monkeypatch.setattr(settings, "LLM_PROVIDER", "deepseek", raising=False)
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDERS", "openai, deepseek", raising=False)
    # primary deepseek, then openai; duplicate deepseek dropped.
    assert resolve_provider_chain() == ["deepseek", "openai"]


# --------------------------------------------------------------------------- #
# get_llm_client_chain — factory wiring
# --------------------------------------------------------------------------- #

def test_chain_factory_single_provider_returns_client_directly(monkeypatch):
    single = _FakeClient(reply="solo")
    monkeypatch.setattr(mod, "get_llm_client", lambda p: single)
    result = get_llm_client_chain(["openai"])
    assert result is single  # no FallbackLLMClient wrapper for a 1-provider chain


def test_chain_factory_builds_fallback_for_multi_provider(monkeypatch):
    fakes = {"openai": _FakeClient(raises=LLMCallError("down")), "deepseek": _FakeClient(reply="ds")}
    monkeypatch.setattr(mod, "get_llm_client", lambda p: fakes[p])
    result = get_llm_client_chain(["openai", "deepseek"])
    assert isinstance(result, FallbackLLMClient)
    assert result.chat([{"role": "user", "content": "hi"}]) == "ds"


def test_chain_factory_skips_unconstructable_provider(monkeypatch):
    def _resolver(p):
        if p == "openai":
            raise RuntimeError("no SDK")
        return _FakeClient(reply="ds")

    monkeypatch.setattr(mod, "get_llm_client", _resolver)
    # openai fails to construct → chain collapses to the single healthy deepseek.
    result = get_llm_client_chain(["openai", "deepseek"])
    assert result.chat([{"role": "user", "content": "hi"}]) == "ds"
