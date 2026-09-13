from __future__ import annotations

import logging
from typing import Any, Callable, Protocol, runtime_checkable

from AINDY.kernel.circuit_breaker import CircuitBreaker, CircuitOpenError


class LLMCallError(Exception):
    """Normalized error for provider-backed LLM calls."""


class LLMCircuitOpenError(CircuitOpenError, LLMCallError):
    """Raised when the LLM circuit breaker rejects a call."""


class LLMBudgetExceededError(LLMCallError):
    """Raised BEFORE a provider call when the caller's token budget would be exceeded.

    COST-GOVERNOR-1 phase 4. A subclass of ``LLMCallError`` so every existing consumer's error
    handling sees one contract; the message starts with ``RESOURCE_LIMIT_EXCEEDED`` like every
    other quota refusal in the runtime. ★ Not a provider failure: it is raised outside the
    circuit breaker and never counts toward opening it.
    """

    def __init__(self, message: str, *, scope: str, subject: str, used: int, reserved: int, cap: int):
        super().__init__(message)
        self.scope = scope
        self.subject = subject
        self.used = used
        self.reserved = reserved
        self.cap = cap


def find_budget_refusal(exc: BaseException | None, *, depth: int = 8) -> "LLMBudgetExceededError | None":
    """The ``LLMBudgetExceededError`` behind ``exc``, walking ``__cause__``/``__context__``.

    Consumers rewrap seam errors — the app's planner raises ``AnthropicPlannerError(detail)
    from exc`` — so a typed refusal is usually one or two causes down by the time a route sees
    it. Found live: the first refused planner call reached the client as a 500 because the
    route checked only the outermost exception. Bounded walk; never raises.
    """
    seen = 0
    while exc is not None and seen < depth:
        if isinstance(exc, LLMBudgetExceededError):
            return exc
        exc = exc.__cause__ or exc.__context__
        seen += 1
    return None


@runtime_checkable
class LLMClient(Protocol):
    """Abstraction for all LLM provider calls."""

    def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> str:
        """Send a chat completion request. Returns the assistant message text."""
        ...

    def is_available(self) -> bool:
        """Return True if the underlying provider appears reachable."""
        ...


class CircuitBreakerLLMClient:
    """LLMClient wrapper that guards calls with a circuit breaker."""

    def __init__(
        self,
        client: LLMClient,
        *,
        provider: str,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        if not isinstance(client, LLMClient):
            raise TypeError(f"client must satisfy LLMClient protocol, got {type(client)!r}")
        self._client = client
        self._provider = provider
        self._breaker = breaker or CircuitBreaker(
            name=provider,
            failure_threshold=5,
            recovery_timeout_secs=60,
        )

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    def _call_with_breaker(self, func, *args, **kwargs):
        # COST-GOVERNOR-1 phase 4 — reserve → call → reconcile. The reservation sits OUTSIDE
        # the breaker on purpose: a budget refusal is not a provider failure and must never
        # count toward opening the circuit. Imported lazily to keep this module free of the
        # kernel at import time (llm_budget reads the resource manager).
        from AINDY.platform_layer.llm_budget import llm_budget_reservation
        from AINDY.platform_layer.token_meter import METERED_METHODS

        if getattr(func, "__name__", "") not in METERED_METHODS:
            # Not a token-spending call (an embedding, a listing): nothing to reserve against.
            return self._guarded_call(func, *args, **kwargs)
        with llm_budget_reservation(provider=self._provider, args=args, kwargs=kwargs):
            return self._guarded_call(func, *args, **kwargs)

    def _guarded_call(self, func, *args, **kwargs):
        try:
            return self._breaker.call(func, *args, **kwargs)
        except CircuitOpenError as exc:
            logging.warning("[LLM:%s] circuit open; rejecting call", self._provider)
            raise LLMCircuitOpenError(str(exc)) from exc
        except LLMCallError:
            raise
        except Exception as exc:  # pragma: no cover
            raise LLMCallError(f"{self._provider} call failed") from exc

    def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> str:
        return self._call_with_breaker(
            self._client.chat,
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def call_method(self, method_name: str, *args, **kwargs) -> Any:
        method = getattr(self._client, method_name)
        return self._call_with_breaker(method, *args, **kwargs)

    def is_available(self) -> bool:
        return self._client.is_available()


# ECOGAP-3 Phase 2: extensible provider registry. Each factory returns an
# already-wrapped LLMClient (typically a CircuitBreakerLLMClient) and imports its
# concrete client lazily, so llm_client has no import-time dependency on any provider
# module. Add a provider by registering a factory here (or via register_llm_provider).


def _openai_factory() -> "LLMClient":
    from AINDY.platform_layer.openai_client import get_openai_client

    return get_openai_client()


def _deepseek_factory() -> "LLMClient":
    from AINDY.platform_layer.deepseek_client import get_deepseek_client

    return get_deepseek_client()


def _anthropic_factory() -> "LLMClient":
    from AINDY.platform_layer.anthropic_client import get_anthropic_client

    return get_anthropic_client()


def _azure_openai_factory() -> "LLMClient":
    from AINDY.platform_layer.azure_openai_client import get_azure_openai_client

    return get_azure_openai_client()


_PROVIDER_FACTORIES: dict[str, Callable[[], "LLMClient"]] = {
    "openai": _openai_factory,
    "deepseek": _deepseek_factory,
    "anthropic": _anthropic_factory,
    "azure_openai": _azure_openai_factory,
}


def register_llm_provider(name: str, factory: Callable[[], "LLMClient"]) -> None:
    """Register (or override) a provider factory. The name is what appears in
    LLM_PROVIDER / LLM_FALLBACK_PROVIDERS. Lets a plugin add a provider without
    editing this module."""
    _PROVIDER_FACTORIES[str(name or "").strip().lower()] = factory


def registered_provider_names() -> tuple[str, ...]:
    """The provider names currently resolvable via get_llm_client / the fallback chain."""
    return tuple(sorted(_PROVIDER_FACTORIES))


class FallbackLLMClient:
    """LLMClient that tries an ordered chain of providers, failing over on error.

    Each entry is an already-wrapped provider client (typically a
    ``CircuitBreakerLLMClient``). On ``LLMCallError`` — which subsumes
    ``LLMCircuitOpenError``, so an open primary breaker fails over rather than
    surfacing — the next provider is tried; if every provider fails, the last
    error propagates. A successful provider short-circuits the chain.

    Satisfies the ``LLMClient`` protocol, so it is a drop-in wherever a single
    provider client is used today.
    """

    def __init__(self, clients: list[LLMClient], *, providers: list[str] | None = None) -> None:
        if not clients:
            raise ValueError("FallbackLLMClient requires at least one client")
        for client in clients:
            if not isinstance(client, LLMClient):
                raise TypeError(f"chain entry must satisfy LLMClient protocol, got {type(client)!r}")
        self._clients = list(clients)
        self._providers = list(providers) if providers else [f"provider_{i}" for i in range(len(clients))]

    @property
    def providers(self) -> list[str]:
        return list(self._providers)

    def _try_chain(self, op, *args, **kwargs):
        last_exc: LLMCallError | None = None
        for provider, client in zip(self._providers, self._clients):
            try:
                return op(client, *args, **kwargs)
            except LLMCallError as exc:
                last_exc = exc
                logging.warning(
                    "[LLM-fallback] provider '%s' failed (%s); trying next in chain",
                    provider,
                    type(exc).__name__,
                )
                continue
        # Chain exhausted — surface the final provider's error.
        assert last_exc is not None  # loop ran at least once (clients non-empty)
        raise last_exc

    def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> str:
        return self._try_chain(
            lambda client, *a, **k: client.chat(*a, **k),
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def call_method(self, method_name: str, *args, **kwargs) -> Any:
        def _invoke(client, *a, **k):
            caller = getattr(client, "call_method", None)
            if callable(caller):
                return caller(method_name, *a, **k)
            return getattr(client, method_name)(*a, **k)

        return self._try_chain(_invoke, *args, **kwargs)

    def is_available(self) -> bool:
        return any(client.is_available() for client in self._clients)


def get_llm_client(provider: str = "openai") -> LLMClient:
    """Return a circuit-breaker-wrapped LLM client for a single provider."""
    normalized = str(provider or "openai").strip().lower()
    factory = _PROVIDER_FACTORIES.get(normalized)
    if factory is None:
        raise ValueError(f"Unsupported LLM provider: {provider}")
    return factory()


def resolve_provider_chain(providers: list[str] | None = None) -> list[str]:
    """Resolve the ordered, de-duplicated provider chain (AGENT-HARDEN-5).

    When *providers* is given it is used verbatim (normalized); otherwise the
    chain is ``settings.LLM_PROVIDER`` followed by ``settings.LLM_FALLBACK_PROVIDERS``
    (comma-separated). Unknown providers are dropped; order is preserved; the
    result always has at least one entry (``"openai"`` as the ultimate default).
    """
    if providers:
        raw = list(providers)
    else:
        from AINDY.config import settings

        primary = str(getattr(settings, "LLM_PROVIDER", "") or "openai")
        fallbacks = str(getattr(settings, "LLM_FALLBACK_PROVIDERS", "") or "")
        raw = [primary, *fallbacks.split(",")]

    chain: list[str] = []
    seen: set[str] = set()
    for entry in raw:
        name = str(entry or "").strip().lower()
        if name and name in _PROVIDER_FACTORIES and name not in seen:
            seen.add(name)
            chain.append(name)
    return chain or ["openai"]


def get_llm_client_chain(providers: list[str] | None = None) -> LLMClient:
    """Return an LLM client that fails over across the configured provider chain.

    A single-provider chain returns that provider's client directly (unchanged
    behavior); a multi-provider chain returns a ``FallbackLLMClient``. Providers
    that cannot be constructed (e.g. missing SDK) are skipped so a broken
    secondary never blocks a healthy primary.
    """
    chain = resolve_provider_chain(providers)
    clients: list[LLMClient] = []
    resolved: list[str] = []
    for name in chain:
        try:
            clients.append(get_llm_client(name))
            resolved.append(name)
        except Exception as exc:
            logging.warning("[LLM-fallback] provider '%s' unavailable at resolve: %s", name, exc)

    if not clients:
        # Every configured provider failed to construct — fall back to the
        # single-provider default, which raises transparently if truly broken.
        return get_llm_client("openai")
    if len(clients) == 1:
        return clients[0]
    return FallbackLLMClient(clients, providers=resolved)
