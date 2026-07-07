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

import httpx
import pytest
import respx

from AINDY.platform_layer.deepseek_client import DeepSeekLLMClient

pytestmark = pytest.mark.runtime_only

_CASSETTES = Path(__file__).resolve().parent.parent / "fixtures" / "cassettes"


def _cassette(name: str) -> dict:
    return json.loads((_CASSETTES / name).read_text(encoding="utf-8"))


@respx.mock
def test_deepseek_chat_hits_deepseek_host_contract():
    route = respx.post("https://api.deepseek.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_cassette("deepseek_chat_completion.json"))
    )
    client = DeepSeekLLMClient(api_key="sk-deepseek-contract")

    text = client.chat([{"role": "user", "content": "hi"}], model="deepseek-chat")

    # Response handling.
    assert text == "Hello from DeepSeek contract!"

    # Wire shape — the endpoint host is the contract this guards.
    assert route.called
    request = route.calls.last.request
    assert request.url.host == "api.deepseek.com"
    assert request.headers["authorization"] == "Bearer sk-deepseek-contract"
    body = json.loads(request.content)
    assert body["model"] == "deepseek-chat"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
