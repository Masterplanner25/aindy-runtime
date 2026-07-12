"""ECOGAP-3 Phase 2 — extensible LLM provider registry + Anthropic/Azure clients.

Pins: the provider registry is extensible (built-ins + register_llm_provider) and the
fallback chain resolves the new providers; the Anthropic client maps OpenAI-style
messages onto the Messages API (separate system, required max_tokens) and does NOT
forward temperature (sampling params 400 on current Claude models); the Azure client
speaks the OpenAI-compatible chat shape.
"""
from __future__ import annotations

import pytest

from AINDY.platform_layer import llm_client as mod
from AINDY.platform_layer.llm_client import (
    get_llm_client,
    register_llm_provider,
    registered_provider_names,
    resolve_provider_chain,
)

pytestmark = pytest.mark.runtime_only


# --- registry ---


def test_registry_includes_builtin_and_new_providers():
    names = set(registered_provider_names())
    assert {"openai", "deepseek", "anthropic", "azure_openai"} <= names


def test_resolve_chain_resolves_new_providers():
    assert resolve_provider_chain(["anthropic", "azure_openai"]) == ["anthropic", "azure_openai"]


def test_unknown_providers_still_dropped():
    # Unchanged contract: unknown names are dropped, empty resolves to openai.
    assert resolve_provider_chain(["bogus", "nope"]) == ["openai"]


def test_register_custom_provider():
    sentinel = object()
    register_llm_provider("MyProv", lambda: sentinel)
    try:
        assert "myprov" in registered_provider_names()
        assert resolve_provider_chain(["myprov"]) == ["myprov"]
        assert get_llm_client("myprov") is sentinel
    finally:
        mod._PROVIDER_FACTORIES.pop("myprov", None)


# --- Anthropic client (native Messages API) ---


class _FakeBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _FakeAnthropicMessages:
    def __init__(self, holder):
        self._holder = holder

    def create(self, **params):
        self._holder["params"] = params
        return type("_Resp", (), {"content": [_FakeBlock("hi from claude")]})()


class _FakeAnthropicClient:
    def __init__(self, holder):
        self.messages = _FakeAnthropicMessages(holder)


def test_anthropic_splits_system_and_omits_temperature():
    from AINDY.platform_layer.anthropic_client import AnthropicLLMClient

    holder: dict = {}
    client = AnthropicLLMClient(
        client=_FakeAnthropicClient(holder),
        api_key="k",
        default_model="claude-opus-4-8",
        max_tokens=1234,
    )
    out = client.chat(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
        temperature=0.9,
    )
    assert out == "hi from claude"
    params = holder["params"]
    assert params["system"] == "sys"
    assert params["messages"] == [{"role": "user", "content": "hi"}]
    assert params["max_tokens"] == 1234  # required by Anthropic, defaulted here
    assert params["model"] == "claude-opus-4-8"
    assert "temperature" not in params  # sampling params 400 on current Claude models
    assert client.is_available() is True


def test_anthropic_no_system_message():
    from AINDY.platform_layer.anthropic_client import AnthropicLLMClient

    holder: dict = {}
    client = AnthropicLLMClient(client=_FakeAnthropicClient(holder), api_key="k")
    client.chat([{"role": "user", "content": "hi"}])
    assert "system" not in holder["params"]


# --- Azure OpenAI client (OpenAI-compatible chat shape) ---


class _FakeAzureCompletions:
    def create(self, **kwargs):
        msg = type("_Msg", (), {"content": "azure says hi"})()
        choice = type("_Choice", (), {"message": msg})()
        return type("_Resp", (), {"choices": [choice]})()


class _FakeAzureClient:
    def __init__(self):
        self.chat = type("_Chat", (), {"completions": _FakeAzureCompletions()})()


def test_azure_openai_chat_returns_text():
    from AINDY.platform_layer.azure_openai_client import AzureOpenAILLMClient

    client = AzureOpenAILLMClient(
        client=_FakeAzureClient(),
        api_key="k",
        endpoint="https://x.openai.azure.com",
        deployment="gpt-4o",
    )
    assert client.chat([{"role": "user", "content": "hi"}]) == "azure says hi"
    assert client.is_available() is True


def test_azure_unavailable_without_endpoint():
    from AINDY.platform_layer.azure_openai_client import AzureOpenAILLMClient

    client = AzureOpenAILLMClient(client=_FakeAzureClient(), api_key="k", endpoint="")
    assert client.is_available() is False
