"""An in-memory transport for the openai SDK's HTTP boundary (DEC-138).

openai 3.x sends through `httpx2`, which `respx` does not patch: a respx-mocked contract test on
openai 3 falls through to the real API (it went red only because the fixture key is fake). This
replaces it with `httpx2.MockTransport`, the route openai's own migration guide gives.

`install(monkeypatch, module, routes)` wraps `module.OpenAI` so every client the ADAPTER builds
gets the mock transport and keeps every argument the adapter passes (the DeepSeek test exists to
pin `base_url`, so injecting a ready-made client would skip what it guards). Fails closed: a
request to a route not listed is recorded and answered 599, never sent, and the `sdk_transport`
fixture fails the test on teardown if any happened. It answers instead of raising because the
SDK wraps a transport exception as a retryable `APIConnectionError`, which would hide it.
"""
from __future__ import annotations

from typing import Any

import httpx2
import pytest


class SdkTransport:
    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], httpx2.Response] = {}
        self.calls: list[httpx2.Request] = []
        self.unexpected: list[str] = []

    def add(self, method: str, url: str, response: httpx2.Response) -> None:
        self.routes[(method.upper(), url)] = response

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        key = (request.method, f"{request.url.scheme}://{request.url.host}{request.url.path}")
        if key not in self.routes:
            self.unexpected.append(f"{key[0]} {key[1]}")
            return httpx2.Response(599, json={"error": {"message": "unexpected request in a contract test"}})
        self.calls.append(request)
        return self.routes[key]

    def client(self) -> httpx2.Client:
        return httpx2.Client(transport=httpx2.MockTransport(self.handler))

    def install(self, monkeypatch, module: Any) -> None:
        real = module.OpenAI

        def _with_mock_transport(*args, **kwargs):
            assert "http_client" not in kwargs, "the adapter now passes its own http_client; update this fixture"
            kwargs["http_client"] = self.client()
            return real(*args, **kwargs)

        monkeypatch.setattr(module, "OpenAI", _with_mock_transport)


@pytest.fixture
def sdk_transport():
    transport = SdkTransport()
    yield transport
    assert not transport.unexpected, f"a contract test tried to reach the network: {transport.unexpected}"
