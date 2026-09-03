"""
Anthropic (Claude) LLM client — ECOGAP-3 Phase 2.

A concrete ``LLMClient`` behind the existing provider seam, selectable via
``LLM_PROVIDER=anthropic`` / ``LLM_FALLBACK_PROVIDERS``. Uses the official
``anthropic`` SDK Messages API — NOT an OpenAI-compatible shim. Optional dependency:
``pip install aindy-runtime[anthropic]``.

Messages-API specifics handled here (they differ from the OpenAI chat shape):
  * the system prompt is a separate ``system=`` argument, not a ``role: "system"``
    message — ``chat()`` splits OpenAI-style messages accordingly;
  * ``max_tokens`` is REQUIRED (defaulted from ``ANTHROPIC_MAX_TOKENS``);
  * the response is a list of content blocks — text is joined from ``type == "text"``;
  * ``temperature`` is deliberately NOT forwarded: current Claude models
    (Opus 4.8 / Sonnet 5 / etc.) reject sampling parameters with a 400. Default model
    is ``claude-opus-4-8``.
"""
from __future__ import annotations

import logging
from typing import Any

from AINDY.kernel.circuit_breaker import CircuitBreaker, CircuitOpenError  # noqa: F401
from AINDY.platform_layer.llm_client import (
    CircuitBreakerLLMClient,
    LLMCallError,
    LLMClient,
)
from AINDY.platform_layer.token_meter import observe_llm_usage

logger = logging.getLogger(__name__)

DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-8"
DEFAULT_ANTHROPIC_MAX_TOKENS = 4096

_anthropic_breaker = CircuitBreaker(
    name="anthropic",
    failure_threshold=5,
    recovery_timeout_secs=60,
)


def _split_system(messages: list[dict]) -> tuple[str | None, list[dict]]:
    """Anthropic takes the system prompt as a separate parameter, so pull any
    ``role: "system"`` entries out of the OpenAI-style message list."""
    system_parts: list[str] = []
    conversation: list[dict] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content", "")
        if role == "system":
            text = content if isinstance(content, str) else str(content)
            if text:
                system_parts.append(text)
        else:
            conversation.append({"role": role, "content": content})
    system = "\n\n".join(system_parts) if system_parts else None
    return system, conversation


def _extract_message_text(response: Any) -> str:
    try:
        blocks = getattr(response, "content", []) or []
        return "".join(
            block.text for block in blocks if getattr(block, "type", None) == "text"
        )
    except Exception as exc:  # pragma: no cover
        raise LLMCallError("anthropic response did not contain assistant text") from exc


class AnthropicLLMClient(LLMClient):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        default_model: str | None = None,
        client: Any | None = None,
        max_tokens: int | None = None,
    ) -> None:
        from AINDY.config import settings

        self._api_key = api_key if api_key is not None else getattr(settings, "ANTHROPIC_API_KEY", "")
        self._default_model = (
            default_model or str(getattr(settings, "ANTHROPIC_MODEL", "") or "").strip() or DEFAULT_ANTHROPIC_MODEL
        )
        self._max_tokens = int(
            max_tokens or getattr(settings, "ANTHROPIC_MAX_TOKENS", 0) or DEFAULT_ANTHROPIC_MAX_TOKENS
        )
        if client is not None:
            self._client = client
        else:
            try:
                import anthropic
            except ImportError as exc:
                raise LLMCallError(
                    "anthropic SDK not installed; install with: pip install 'aindy-runtime[anthropic]'"
                ) from exc
            base_url = str(getattr(settings, "ANTHROPIC_BASE_URL", "") or "").strip() or None
            self._client = anthropic.Anthropic(
                api_key=self._api_key or "missing-anthropic-api-key",
                base_url=base_url,
            )

    def messages_create(
        self,
        *,
        model: str,
        messages: list[dict],
        system: str | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> Any:
        # temperature is intentionally not accepted/forwarded — sampling params 400 on
        # current Claude models.
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": int(max_tokens or self._max_tokens),
            "messages": messages,
        }
        if system:
            params["system"] = system
        try:
            response = self._client.messages.create(**params, **kwargs)
            # COST-GOVERNOR-1 phase 0: the RAW path a structured caller uses. Metering
            # only chat() left this unmetered — and chat() is not the method a caller
            # needing tool blocks can use, so the one real consumer would route through
            # the seam and still measure nothing.
            observe_llm_usage(provider="anthropic", model=str(model), response=response)
            return response
        except Exception as exc:
            raise LLMCallError("anthropic messages.create failed") from exc

    def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.7,  # accepted for interface parity, not forwarded
        max_tokens: int | None = None,
    ) -> str:
        system, conversation = _split_system(messages)
        response = self.messages_create(
            model=model or self._default_model,
            messages=conversation,
            system=system,
            max_tokens=max_tokens,
        )
        # NOT metered here: chat() delegates to the raw response method above, which
        # meters. Counting in both places would DOUBLE-COUNT every chat call, and a
        # fabricated measurement is the one failure the meter's design rejects outright.
        return _extract_message_text(response)

    def is_available(self) -> bool:
        return bool(str(self._api_key or "").strip())


def get_anthropic_circuit_breaker() -> CircuitBreaker:
    return _anthropic_breaker


_client: LLMClient | None = None


def get_anthropic_client() -> LLMClient:
    global _client
    if _client is None:
        provider = AnthropicLLMClient()
        _client = CircuitBreakerLLMClient(
            provider,
            provider="anthropic",
            breaker=get_anthropic_circuit_breaker(),
        )
    return _client
