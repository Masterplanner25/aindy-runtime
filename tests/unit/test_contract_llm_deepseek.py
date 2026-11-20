"""AGENT-HARDEN-7 — recorded-cassette contract test for the DeepSeek HTTP boundary.

Freezes the DeepSeek adapter's wire contract: the request must go to the DeepSeek
host (api.deepseek.com), not OpenAI's. This test surfaced a real bug — the client
constructed the OpenAI SDK with no base_url, so DeepSeek calls were being sent to
api.openai.com; the fix sets base_url from settings.DEEPSEEK_BASE_URL, and this
test guards against that drift.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx2
import pytest

from AINDY.platform_layer import deepseek_client
from AINDY.platform_layer.deepseek_client import DeepSeekLLMClient
from tests.fixtures.sdk_transport import sdk_transport  # noqa: F401 — fixture

pytestmark = pytest.mark.runtime_only

_CASSETTES = Path(__file__).resolve().parent.parent / "fixtures" / "cassettes"


def _cassette(name: str) -> dict:
    return json.loads((_CASSETTES / name).read_text(encoding="utf-8"))


def test_deepseek_chat_hits_deepseek_host_contract(sdk_transport, monkeypatch):  # noqa: F811
    # Only api.deepseek.com is routed: a request to api.openai.com (the bug this guards) is
    # recorded as unexpected and fails the test at teardown.
    sdk_transport.add(
        "POST", "https://api.deepseek.com/v1/chat/completions",
        httpx2.Response(200, json=_cassette("deepseek_chat_completion.json")),
    )
    sdk_transport.install(monkeypatch, deepseek_client)
    client = DeepSeekLLMClient(api_key="sk-deepseek-contract")

    text = client.chat([{"role": "user", "content": "hi"}], model="deepseek-chat")

    # Response handling.
    assert text == "Hello from DeepSeek contract!"

    # Wire shape — the endpoint host is the contract this guards.
    assert len(sdk_transport.calls) == 1
    request = sdk_transport.calls[-1]
    assert request.url.host == "api.deepseek.com"
    assert request.headers["authorization"] == "Bearer sk-deepseek-contract"
    body = json.loads(request.content)
    assert body["model"] == "deepseek-chat"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
