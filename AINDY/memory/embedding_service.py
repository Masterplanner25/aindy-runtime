"""
Embedding Service

Generates vector embeddings via the configured EmbeddingProvider
(``AINDY_EMBEDDING_PROVIDER`` — OpenAI by default; local for offline/air-gapped).
This module owns the cross-provider orchestration (empty/testing short-circuit,
retry loop, metrics, dimension validation); the raw model call lives in
``embedding_providers`` (ECOGAP-3 Phase 1).
Uses C++ kernel for cosine similarity when available.
Falls back to pure Python.
"""
import logging
import os
import sys
import time
import threading

from AINDY.config import settings
from AINDY.kernel.circuit_breaker import CircuitOpenError
from AINDY.memory.native_bridge import load_bridge
from AINDY.memory.embedding_providers import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    DEFAULT_OPENAI_EMBEDDING_MODEL,
    OpenAIEmbeddingProvider,
    build_embedding_provider,
    validate_embedding_configuration,
)
from AINDY.platform_layer.metrics import (
    embedding_generation_latency_seconds,
    embedding_generation_retries_total,
    embedding_generation_total,
)

# Backward-compat module constants. The live model/dimension now come from the
# active provider; these remain for any external importer and reflect the OpenAI
# default (unchanged production behavior).
EMBEDDING_MODEL = DEFAULT_OPENAI_EMBEDDING_MODEL
EMBEDDING_DIMENSIONS = DEFAULT_EMBEDDING_DIMENSIONS
logger = logging.getLogger(__name__)


class EmbeddingFailedError(RuntimeError):
    """
    Raised by generate_embedding() when the provider call fails after all
    retry attempts. Callers in the async-job path let this propagate so the
    worker can leave the node deferred in a pending state for later retry.
    Query-path callers
    (generate_query_embedding) catch this and return a zero vector so that
    similarity searches degrade gracefully rather than crashing.
    """


_provider = None
_provider_lock = threading.Lock()


def get_embedding_provider():
    """Return the process-wide embedding provider, building + validating it once."""
    global _provider
    if _provider is None:
        with _provider_lock:
            if _provider is None:
                provider = build_embedding_provider()
                validate_embedding_configuration(provider)
                _provider = provider
    return _provider


def reset_embedding_provider() -> None:
    """Drop the cached provider (test/reconfiguration hook)."""
    global _provider
    with _provider_lock:
        _provider = None


def get_client():
    """Backward-compat: return a raw OpenAI client. Prefer get_embedding_provider().
    Only meaningful when the active provider is OpenAI."""
    provider = get_embedding_provider()
    if isinstance(provider, OpenAIEmbeddingProvider):
        return provider._get_client()
    from AINDY.platform_layer.openai_client import get_openai_client

    return get_openai_client()


def generate_embedding(text: str) -> list:
    """
    Generate an embedding for *text* via the configured provider.

    Returns a zero vector immediately when *text* is empty — that is an
    intentional no-op, not a failure.

    Raises EmbeddingFailedError when the provider call fails after all
    retry attempts, so callers (e.g. process_embedding_job) receive the
    actual error and can keep the memory node pending for background retry.
    """
    provider = get_embedding_provider()
    if not text or not text.strip():
        return [0.0] * provider.dimensions
    if settings.is_testing and getattr(provider, "testing_short_circuit", lambda: False)():
        return [0.0] * provider.dimensions

    text = text[:32000]
    last_exc: Exception | None = None
    started_at = time.perf_counter()
    max_attempts = max(1, int(settings.OPENAI_MAX_RETRIES or 1))
    backoff_base = max(0.0, float(settings.OPENAI_RETRY_BACKOFF_BASE_SECONDS or 0.0))

    for attempt in range(max_attempts):
        try:
            embedding = provider.embed_one(text)
            if len(embedding) != provider.dimensions:
                raise ValueError(
                    f"provider {provider.name!r} returned {len(embedding)}-dim vector, "
                    f"expected {provider.dimensions}"
                )
            if attempt:
                embedding_generation_retries_total.inc(attempt)
            embedding_generation_total.labels(outcome="success").inc()
            embedding_generation_latency_seconds.observe(time.perf_counter() - started_at)
            return embedding
        except CircuitOpenError as e:
            embedding_generation_total.labels(outcome="failure").inc()
            embedding_generation_latency_seconds.observe(time.perf_counter() - started_at)
            raise EmbeddingFailedError(
                f"Embedding generation failed fast because the provider circuit is open: {e}"
            ) from e
        except Exception as e:
            last_exc = e
            logger.warning(
                "[EmbeddingService] embedding attempt %s/%s failed: %s",
                attempt + 1,
                max_attempts,
                e,
            )
            if attempt < max_attempts - 1:
                time.sleep(backoff_base * (2 ** attempt))

    # All 3 attempts failed — raise a typed error so callers can mark the
    # node as failed rather than silently storing a zero vector.
    # All attempts failed: raise a typed error so callers can defer retry
    # rather than silently storing a zero vector.
    if max_attempts > 1:
        embedding_generation_retries_total.inc(max_attempts - 1)
    embedding_generation_total.labels(outcome="failure").inc()
    embedding_generation_latency_seconds.observe(time.perf_counter() - started_at)
    logger.error(
        "[EmbeddingService] embedding generation failed after %s attempts: %s",
        max_attempts,
        last_exc,
    )
    raise EmbeddingFailedError(
        f"Embedding generation failed after {max_attempts} attempts: {last_exc}"
    ) from last_exc


def generate_query_embedding(query: str) -> list:
    """
    Generate an embedding for a similarity query.

    Degrades gracefully: returns a zero vector when the API is unavailable
    so that search callers get empty results rather than a 500 error.
    """
    try:
        return generate_embedding(query)
    except EmbeddingFailedError as exc:
        logging.warning(
            "Query embedding failed — returning zero vector for graceful degradation: %s", exc
        )
        return [0.0] * get_embedding_provider().dimensions


def cosine_similarity_python(a: list, b: list) -> float:
    """Pure Python cosine similarity fallback."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def cosine_similarity(a: list, b: list) -> float:
    """Cosine similarity via the C++ kernel when built, else pure Python.

    NATIVE-DISCOVERY-1: this used to search ``target/debug`` only — while
    ``native_scorer`` searched release *and* debug — so on any ``--release`` build
    (what CI produces, and any deployment) the kernel was unreachable from here while
    the scorer in the same process used it. Both now go through
    ``memory.native_bridge``, which also caches the import instead of re-inserting a
    ``sys.path`` entry on every call.

    Returns ``0.0`` for mismatched lengths rather than raising: the sole caller is the
    recall fallback in ``MemoryNodeDAO``, where a node re-embedded at a different
    dimension is genuinely incomparable, not a programming error. That matches
    ``cosine_similarity_python``.
    """
    bridge = load_bridge()
    if bridge is None:
        return cosine_similarity_python(a, b)

    try:
        return bridge.semantic_similarity(a, b)
    except ValueError:
        # Ragged input — the extension's documented error. The Python implementation
        # defines this as 0.0; defer to it rather than duplicating the rule.
        return cosine_similarity_python(a, b)
    except Exception as exc:
        # Previously `except (ImportError, AttributeError, Exception)` — which is just
        # `except Exception` and swallowed everything silently. Anything reaching here
        # is unexpected, so say so before falling back.
        logger.warning(
            "[embedding] native semantic_similarity failed (%s) — using the Python "
            "fallback", exc,
        )
        return cosine_similarity_python(a, b)

