"""
Azure OpenAI LLM client — ECOGAP-3 Phase 2.

A concrete ``LLMClient`` behind the existing provider seam, selectable via
``LLM_PROVIDER=azure_openai`` / ``LLM_FALLBACK_PROVIDERS``. Azure OpenAI is
OpenAI-API-compatible, so this reuses the already-installed ``openai`` SDK
(``AzureOpenAI``) — no new dependency.

Azure specifics: the endpoint, api-version, and a *deployment name* (used as the
``model`` argument) come from ``AZURE_OPENAI_*`` settings. Sampling params
(temperature / max_tokens) are accepted by Azure's GPT deployments and forwarded
as for OpenAI.
"""
from __future__ import annotations

import logging
from typing import Any

from AINDY.kernel.circuit_breaker import CircuitBreaker
from AINDY.platform_layer.llm_client import (
    CircuitBreakerLLMClient,
    LLMCallError,
    LLMClient,
)
from AINDY.platform_layer.token_meter import observe_llm_usage

logger = logging.getLogger(__name__)

DEFAULT_AZURE_API_VERSION = "2024-10-21"

_azure_openai_breaker = CircuitBreaker(
    name="azure_openai",
    failure_threshold=5,
    recovery_timeout_secs=60,
)


def _extract_message_text(response: Any) -> str:
    try:
        return str(response.choices[0].message.content or "")
    except Exception as exc:  # pragma: no cover
        raise LLMCallError("azure openai response did not contain assistant text") from exc


class AzureOpenAILLMClient(LLMClient):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        endpoint: str | None = None,
        api_version: str | None = None,
        deployment: str | None = None,
        client: Any | None = None,
        chat_timeout: float = 30.0,
    ) -> None:
        from AINDY.config import settings

        self._api_key = api_key if api_key is not None else getattr(settings, "AZURE_OPENAI_API_KEY", "")
        self._endpoint = endpoint or str(getattr(settings, "AZURE_OPENAI_ENDPOINT", "") or "").strip()
        self._api_version = (
            api_version or str(getattr(settings, "AZURE_OPENAI_API_VERSION", "") or "").strip() or DEFAULT_AZURE_API_VERSION
        )
        # The Azure "deployment name" is passed where OpenAI passes the model id.
        self._deployment = deployment or str(getattr(settings, "AZURE_OPENAI_DEPLOYMENT", "") or "").strip()
        self._chat_timeout = chat_timeout
        if client is not None:
            self._client = client
        else:
            from openai import AzureOpenAI

            self._client = AzureOpenAI(
                api_key=self._api_key or "missing-azure-openai-api-key",
                azure_endpoint=self._endpoint or "https://missing.openai.azure.com",
                api_version=self._api_version,
            )

    def chat_completion_response(
        self,
        *,
        model: str,
        messages: list[dict],
        timeout: float | None = None,
        **kwargs: Any,
    ) -> Any:
        try:
            return self._client.chat.completions.create(
                model=model,
                messages=messages,
                timeout=self._chat_timeout if timeout is None else timeout,
                **kwargs,
            )
        except Exception as exc:
            raise LLMCallError("azure openai chat completion failed") from exc

    def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> str:
        response = self.chat_completion_response(
            model=model or self._deployment,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        # COST-GOVERNOR-1: the usage object exists here and was discarded one line later.
        observe_llm_usage(provider="azure_openai", model=str(model), response=response)
        return _extract_message_text(response)

    def is_available(self) -> bool:
        return bool(str(self._api_key or "").strip() and str(self._endpoint or "").strip())


def get_azure_openai_circuit_breaker() -> CircuitBreaker:
    return _azure_openai_breaker


_client: LLMClient | None = None


def get_azure_openai_client() -> LLMClient:
    global _client
    if _client is None:
        provider = AzureOpenAILLMClient()
        _client = CircuitBreakerLLMClient(
            provider,
            provider="azure_openai",
            breaker=get_azure_openai_circuit_breaker(),
        )
    return _client
