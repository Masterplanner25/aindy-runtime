"""AGENT-HARDEN-7 — recorded-cassette contract tests for the OpenAI HTTP boundary.

VCR-style: a real response is recorded once (tests/fixtures/cassettes/*.json) and
replayed deterministically via respx (which intercepts the openai SDK's httpx
calls). Each test asserts BOTH the adapter's request wire shape and its response
handling, so it fails if either drifts — the contract the SDK/embedding boundary
must uphold, without a live network call.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from AINDY.platform_layer.openai_client import OpenAILLMClient

pytestmark = pytest.mark.runtime_only

_CASSETTES = Path(__file__).resolve().parent.parent / "fixtures" / "cassettes"


def _cassette(name: str) -> dict:
    return json.loads((_CASSETTES / name).read_text(encoding="utf-8"))


@respx.mock
def test_openai_chat_request_and_response_contract():
    route = respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_cassette("openai_chat_completion.json"))
    )
    client = OpenAILLMClient(api_key="sk-test-contract")

    text = client.chat(
        [{"role": "user", "content": "hi"}], model="gpt-4o", temperature=0.2, max_tokens=32
    )

    # Response handling: assistant text is extracted from the recorded cassette.
    assert text == "Hello from the recorded contract!"

    # Request wire shape.
    assert route.called
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer sk-test-contract"
    body = json.loads(request.content)
    assert body["model"] == "gpt-4o"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 32


@respx.mock
def test_openai_embedding_request_and_response_contract():
    route = respx.post("https://api.openai.com/v1/embeddings").mock(
        return_value=httpx.Response(200, json=_cassette("openai_embedding.json"))
    )
    client = OpenAILLMClient(api_key="sk-test-contract")

    response = client.create_embedding_response(
        input="hello world", model="text-embedding-3-small"
    )

    # Response handling: the embedding vector is parsed off the recorded cassette.
    assert response.data[0].embedding == [0.01, -0.02, 0.03, 0.04]
    assert response.model == "text-embedding-3-small"

    # Request wire shape.
    assert route.called
    body = json.loads(route.calls.last.request.content)
    assert body["input"] == "hello world"
    assert body["model"] == "text-embedding-3-small"


@respx.mock
def test_openai_chat_error_status_raises_llm_call_error():
    """A non-2xx from the boundary surfaces as the adapter's normalized error."""
    from openai import OpenAI

    from AINDY.platform_layer.openai_client import LLMCallError

    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(500, json={"error": {"message": "boom"}})
    )
    # max_retries=0 so the SDK's real backoff doesn't slow the test.
    client = OpenAILLMClient(client=OpenAI(api_key="sk-test-contract", max_retries=0))

    with pytest.raises(LLMCallError):
        client.chat([{"role": "user", "content": "hi"}], model="gpt-4o")
