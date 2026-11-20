"""AGENT-HARDEN-7 — recorded-cassette contract tests for the OpenAI HTTP boundary.

VCR-style: a real response is recorded once (tests/fixtures/cassettes/*.json) and
replayed deterministically through an in-memory `httpx2.MockTransport` on the client
the adapter builds (`tests/fixtures/sdk_transport.py`; openai 3.x sends through httpx2,
which respx does not patch, DEC-138). Each test asserts BOTH the adapter's request wire shape and its response
handling, so it fails if either drifts — the contract the SDK/embedding boundary
must uphold, without a live network call.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx2
import pytest

from AINDY.platform_layer import openai_client
from AINDY.platform_layer.openai_client import OpenAILLMClient
from tests.fixtures.sdk_transport import sdk_transport  # noqa: F401 — fixture

pytestmark = pytest.mark.runtime_only

_CASSETTES = Path(__file__).resolve().parent.parent / "fixtures" / "cassettes"


def _cassette(name: str) -> dict:
    return json.loads((_CASSETTES / name).read_text(encoding="utf-8"))


def test_openai_chat_request_and_response_contract(sdk_transport, monkeypatch):  # noqa: F811
    sdk_transport.add(
        "POST", "https://api.openai.com/v1/chat/completions",
        httpx2.Response(200, json=_cassette("openai_chat_completion.json")),
    )
    sdk_transport.install(monkeypatch, openai_client)
    client = OpenAILLMClient(api_key="sk-test-contract")

    text = client.chat(
        [{"role": "user", "content": "hi"}], model="gpt-4o", temperature=0.2, max_tokens=32
    )

    # Response handling: assistant text is extracted from the recorded cassette.
    assert text == "Hello from the recorded contract!"

    # Request wire shape.
    assert len(sdk_transport.calls) == 1
    request = sdk_transport.calls[-1]
    assert request.headers["authorization"] == "Bearer sk-test-contract"
    body = json.loads(request.content)
    assert body["model"] == "gpt-4o"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 32


def test_openai_embedding_request_and_response_contract(sdk_transport, monkeypatch):  # noqa: F811
    sdk_transport.add(
        "POST", "https://api.openai.com/v1/embeddings",
        httpx2.Response(200, json=_cassette("openai_embedding.json")),
    )
    sdk_transport.install(monkeypatch, openai_client)
    client = OpenAILLMClient(api_key="sk-test-contract")

    response = client.create_embedding_response(
        input="hello world", model="text-embedding-3-small"
    )

    # Response handling: the embedding vector is parsed off the recorded cassette.
    assert response.data[0].embedding == [0.01, -0.02, 0.03, 0.04]
    assert response.model == "text-embedding-3-small"

    # Request wire shape.
    assert len(sdk_transport.calls) == 1
    body = json.loads(sdk_transport.calls[-1].content)
    assert body["input"] == "hello world"
    assert body["model"] == "text-embedding-3-small"


def test_openai_chat_error_status_raises_llm_call_error(sdk_transport):  # noqa: F811
    """A non-2xx from the boundary surfaces as the adapter's normalized error."""
    from openai import OpenAI

    from AINDY.platform_layer.openai_client import LLMCallError

    sdk_transport.add(
        "POST", "https://api.openai.com/v1/chat/completions",
        httpx2.Response(500, json={"error": {"message": "boom"}}),
    )
    # max_retries=0 so the SDK's real backoff doesn't slow the test.
    client = OpenAILLMClient(
        client=OpenAI(api_key="sk-test-contract", max_retries=0, http_client=sdk_transport.client())
    )

    with pytest.raises(LLMCallError):
        client.chat([{"role": "user", "content": "hi"}], model="gpt-4o")
    assert sdk_transport.calls, "the 500 must come from the boundary, not from a failure before it"
