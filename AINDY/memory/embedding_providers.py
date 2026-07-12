"""
Embedding provider abstraction — ECOGAP-3 Phase 1.

Decouples memory embedding generation from a single vendor. OpenAI is the default
provider (behavior unchanged); a local sentence-transformers provider enables
offline / air-gapped / cost-sensitive deployments.

The provider is selected via ``AINDY_EMBEDDING_PROVIDER``. The public funnel in
``embedding_service`` (``generate_embedding`` / ``generate_query_embedding``)
delegates the raw "produce a vector for this text" step here, while keeping the
cross-provider orchestration (empty/testing short-circuit, retry loop, metrics,
dimension validation) in the service — so no call site changes.

Symmetric with the chat-side seam in ``platform_layer/llm_client.py``
(``LLMClient`` Protocol + ``get_llm_client``) — deliberately the same dispatch
shape, not a second pattern.

Dimensionality note (ECOGAP-3 Phase 1, follow-up increment): the ``memory_nodes``
pgvector column is fixed at ``MEMORY_EMBEDDING_COLUMN_DIMENSIONS`` (1536). A provider
whose vectors have a different dimension is fail-closed here until the deferred
schema-configurable-dimension + re-embed migration lands (see
``docs/runtime/PROVIDER_BREADTH_PROGRAM.md`` §3.2).
"""
from __future__ import annotations

import threading
from typing import Protocol, runtime_checkable

from AINDY.config import settings

# The default pgvector column dimension (OpenAI ada-002 / 3-small are both 1536).
DEFAULT_EMBEDDING_DIMENSIONS = 1536


def resolve_embedding_column_dimensions() -> int:
    """The dimension the persisted ``memory_nodes.embedding`` pgvector column is
    declared with — the schema truth an active provider must match. Configurable via
    ``AINDY_EMBEDDING_DIMENSIONS`` so a deployment can pin a non-1536 model; the ORM
    column, the DAO similarity cast, and provider validation all read this one source.

    Changing it on an existing deployment requires running the re-embed migration
    (``aindy-runtime memory reembed``) to ALTER the column and regenerate vectors —
    ``create_all`` never alters an existing table. See PROVIDER_BREADTH_PROGRAM.md §3.2."""
    return int(getattr(settings, "AINDY_EMBEDDING_DIMENSIONS", 0) or DEFAULT_EMBEDDING_DIMENSIONS)

# Preserve the historically-live OpenAI embedding model. The service passed
# "text-embedding-ada-002" explicitly on every call, so that — not the openai_client
# default of "text-embedding-3-small" — is the model in production. Both are 1536-dim.
DEFAULT_OPENAI_EMBEDDING_MODEL = "text-embedding-ada-002"
DEFAULT_LOCAL_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class EmbeddingProviderError(RuntimeError):
    """Configuration/availability problem selecting or building a provider."""


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Produces a single dense vector for a text. Batch orchestration, retries, and
    metrics live in the calling service, not here."""

    name: str
    dimensions: int

    def embed_one(self, text: str) -> list[float]:
        ...


class OpenAIEmbeddingProvider:
    """Default provider — wraps the existing OpenAI embedding path unchanged, including
    the circuit-breaker-guarded external call."""

    name = "openai"

    def __init__(self) -> None:
        self.model = (
            str(getattr(settings, "AINDY_EMBEDDING_OPENAI_MODEL", "") or "").strip()
            or DEFAULT_OPENAI_EMBEDDING_MODEL
        )
        # OpenAI ada-002 / 3-small always emit 1536-dim vectors, independent of the
        # column setting. Validation (below) fails closed if the operator points the
        # column at a different dimension while using OpenAI.
        self.dimensions = DEFAULT_EMBEDDING_DIMENSIONS
        self._client = None
        self._lock = threading.Lock()

    def _get_client(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    from AINDY.platform_layer.openai_client import get_openai_client

                    self._client = get_openai_client()
        return self._client

    def has_live_client(self) -> bool:
        return self._client is not None

    def testing_short_circuit(self) -> bool:
        # Mirrors the prior guard: in tests with no OpenAI client wired, return a
        # zero vector instead of attempting a real API call.
        return self._client is None

    def embed_one(self, text: str) -> list[float]:
        from AINDY.platform_layer.external_call_service import perform_external_call
        from AINDY.platform_layer.openai_client import create_embedding

        client = self._get_client()
        response = perform_external_call(
            service_name="openai",
            endpoint="embeddings.create",
            model=self.model,
            method="openai.embeddings",
            extra={"purpose": "embedding_generation"},
            operation=lambda: create_embedding(
                client,
                input=text,
                model=self.model,
                timeout=settings.OPENAI_EMBEDDING_TIMEOUT_SECONDS,
            ),
        )
        return response.data[0].embedding


class LocalEmbeddingProvider:
    """Offline provider backed by sentence-transformers. Optional dependency —
    ``pip install aindy-runtime[embeddings-local]``. Enables air-gapped deployments.

    The declared ``dimensions`` must match the model's output *and* the persisted
    column dimension; see ``validate_embedding_configuration``."""

    name = "local"

    def __init__(self) -> None:
        self.model_name = (
            str(getattr(settings, "AINDY_EMBEDDING_LOCAL_MODEL", "") or "").strip()
            or DEFAULT_LOCAL_EMBEDDING_MODEL
        )
        self.dimensions = int(getattr(settings, "AINDY_EMBEDDING_LOCAL_DIMENSIONS", 0) or 384)
        self.device = str(getattr(settings, "AINDY_EMBEDDING_LOCAL_DEVICE", "") or "").strip() or None
        self._model = None
        self._lock = threading.Lock()

    def _get_model(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    try:
                        from sentence_transformers import SentenceTransformer
                    except ImportError as exc:  # pragma: no cover - dep-optional
                        raise EmbeddingProviderError(
                            "AINDY_EMBEDDING_PROVIDER=local requires sentence-transformers; "
                            "install with: pip install 'aindy-runtime[embeddings-local]'"
                        ) from exc
                    self._model = SentenceTransformer(self.model_name, device=self.device)
        return self._model

    def testing_short_circuit(self) -> bool:
        # Local embeddings are deterministic and offline — no reason to short-circuit
        # in tests.
        return False

    def embed_one(self, text: str) -> list[float]:
        model = self._get_model()
        vector = model.encode([text], convert_to_numpy=True)[0]
        return [float(x) for x in vector]


_PROVIDER_BUILDERS = {
    "openai": OpenAIEmbeddingProvider,
    "local": LocalEmbeddingProvider,
}


def build_embedding_provider(name: str | None = None) -> EmbeddingProvider:
    """Construct the provider named by *name* (or ``AINDY_EMBEDDING_PROVIDER``)."""
    resolved = (name or str(getattr(settings, "AINDY_EMBEDDING_PROVIDER", "") or "openai")).strip().lower()
    builder = _PROVIDER_BUILDERS.get(resolved)
    if builder is None:
        raise EmbeddingProviderError(
            f"Unknown AINDY_EMBEDDING_PROVIDER={resolved!r}; "
            f"supported: {sorted(_PROVIDER_BUILDERS)}"
        )
    return builder()


def validate_embedding_configuration(provider: EmbeddingProvider) -> None:
    """Fail-closed guard: the active provider's vector dimension must match the
    configured ``memory_nodes`` column dimension. A mismatch would reject writes
    against the pgvector column. To run a differently-dimensioned provider, set
    ``AINDY_EMBEDDING_DIMENSIONS`` to match it AND run the re-embed migration so the
    persisted column is actually altered (``create_all`` won't alter an existing
    column)."""
    column_dimensions = resolve_embedding_column_dimensions()
    if int(provider.dimensions) != column_dimensions:
        raise EmbeddingProviderError(
            f"Embedding provider {provider.name!r} produces {provider.dimensions}-dim vectors, "
            f"but the configured memory_nodes column dimension (AINDY_EMBEDDING_DIMENSIONS) is "
            f"{column_dimensions}. Set AINDY_EMBEDDING_DIMENSIONS to {provider.dimensions} and run "
            "`aindy-runtime memory reembed` to alter the column + re-embed "
            "(see docs/runtime/PROVIDER_BREADTH_PROGRAM.md §3.2)."
        )
