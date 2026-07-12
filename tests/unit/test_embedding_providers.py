"""ECOGAP-3 Phase 1 — embedding provider abstraction.

Pins the seam: the memory embedding path dispatches through a configurable
``EmbeddingProvider``; OpenAI stays the default with unchanged behavior; unknown
providers and dimension-mismatched providers fail closed.
"""
from __future__ import annotations

import pytest

from AINDY.config import settings
from AINDY.memory import embedding_service
from AINDY.memory.embedding_providers import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    DEFAULT_OPENAI_EMBEDDING_MODEL,
    EmbeddingProviderError,
    LocalEmbeddingProvider,
    OpenAIEmbeddingProvider,
    build_embedding_provider,
    resolve_embedding_column_dimensions,
    validate_embedding_configuration,
)

pytestmark = pytest.mark.runtime_only


@pytest.fixture(autouse=True)
def _reset_provider():
    embedding_service.reset_embedding_provider()
    yield
    embedding_service.reset_embedding_provider()


def test_default_provider_is_openai_and_behavior_unchanged(monkeypatch):
    monkeypatch.setattr(settings, "AINDY_EMBEDDING_PROVIDER", "openai")
    provider = build_embedding_provider()
    assert isinstance(provider, OpenAIEmbeddingProvider)
    assert provider.name == "openai"
    assert provider.dimensions == DEFAULT_EMBEDDING_DIMENSIONS == 1536
    # Live model preserved: ada-002 (the value the service passed explicitly), NOT
    # the openai_client default of text-embedding-3-small.
    assert provider.model == DEFAULT_OPENAI_EMBEDDING_MODEL == "text-embedding-ada-002"


def test_local_provider_constructs_without_loading_model(monkeypatch):
    monkeypatch.setattr(settings, "AINDY_EMBEDDING_PROVIDER", "local")
    monkeypatch.setattr(settings, "AINDY_EMBEDDING_LOCAL_MODEL", "some/model")
    monkeypatch.setattr(settings, "AINDY_EMBEDDING_LOCAL_DIMENSIONS", 384)
    provider = build_embedding_provider()
    assert isinstance(provider, LocalEmbeddingProvider)
    assert provider.model_name == "some/model"
    assert provider.dimensions == 384
    # Model load is lazy — construction must not import/download anything.
    assert provider._model is None


def test_unknown_provider_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "AINDY_EMBEDDING_PROVIDER", "nope")
    with pytest.raises(EmbeddingProviderError):
        build_embedding_provider()


def test_validate_configuration_passes_when_dimensions_match():
    validate_embedding_configuration(OpenAIEmbeddingProvider())  # 1536 == column dim


def test_validate_configuration_fails_closed_on_dimension_mismatch():
    class _Weird:
        name = "weird"
        dimensions = 384

        def embed_one(self, text):  # pragma: no cover - not reached
            return [0.0] * 384

    with pytest.raises(EmbeddingProviderError):
        validate_embedding_configuration(_Weird())


def test_get_embedding_provider_caches(monkeypatch):
    monkeypatch.setattr(settings, "AINDY_EMBEDDING_PROVIDER", "openai")
    assert embedding_service.get_embedding_provider() is embedding_service.get_embedding_provider()


def test_get_embedding_provider_fails_closed_on_mismatch(monkeypatch):
    # local default (384-dim) against the 1536 column must refuse at first use.
    monkeypatch.setattr(settings, "AINDY_EMBEDDING_PROVIDER", "local")
    monkeypatch.setattr(settings, "AINDY_EMBEDDING_LOCAL_DIMENSIONS", 384)
    with pytest.raises(EmbeddingProviderError):
        embedding_service.get_embedding_provider()


def test_empty_text_returns_zero_vector(monkeypatch):
    monkeypatch.setattr(settings, "AINDY_EMBEDDING_PROVIDER", "openai")
    assert embedding_service.generate_embedding("   ") == [0.0] * 1536


def test_generate_embedding_delegates_to_provider(monkeypatch):
    class _Fake:
        name = "fake"
        dimensions = DEFAULT_EMBEDDING_DIMENSIONS
        calls = 0

        def embed_one(self, text):
            _Fake.calls += 1
            return [0.1] * DEFAULT_EMBEDDING_DIMENSIONS

    monkeypatch.setattr(embedding_service, "build_embedding_provider", lambda name=None: _Fake())
    embedding_service.reset_embedding_provider()
    out = embedding_service.generate_embedding("hello")
    assert len(out) == 1536
    assert _Fake.calls == 1


def test_generate_embedding_raises_typed_error_on_bad_dimension(monkeypatch):
    class _BadDim:
        name = "baddim"
        dimensions = DEFAULT_EMBEDDING_DIMENSIONS

        def embed_one(self, text):
            return [0.0] * 10  # wrong length → retried then EmbeddingFailedError

    monkeypatch.setattr(embedding_service, "build_embedding_provider", lambda name=None: _BadDim())
    monkeypatch.setattr(settings, "OPENAI_MAX_RETRIES", 1)
    monkeypatch.setattr(settings, "OPENAI_RETRY_BACKOFF_BASE_SECONDS", 0.0)
    embedding_service.reset_embedding_provider()
    with pytest.raises(embedding_service.EmbeddingFailedError):
        embedding_service.generate_embedding("hi")


# --- Increment 2: configurable column dimension ---


def test_resolve_column_dimensions_default_and_configured(monkeypatch):
    monkeypatch.setattr(settings, "AINDY_EMBEDDING_DIMENSIONS", 1536)
    assert resolve_embedding_column_dimensions() == 1536
    monkeypatch.setattr(settings, "AINDY_EMBEDDING_DIMENSIONS", 384)
    assert resolve_embedding_column_dimensions() == 384


def test_local_provider_usable_when_column_dimension_matches(monkeypatch):
    # With the column pinned to 384 to match a local model, validation + selection pass.
    monkeypatch.setattr(settings, "AINDY_EMBEDDING_PROVIDER", "local")
    monkeypatch.setattr(settings, "AINDY_EMBEDDING_LOCAL_DIMENSIONS", 384)
    monkeypatch.setattr(settings, "AINDY_EMBEDDING_DIMENSIONS", 384)
    provider = embedding_service.get_embedding_provider()
    assert isinstance(provider, LocalEmbeddingProvider)
    assert provider.dimensions == resolve_embedding_column_dimensions() == 384


def test_openai_fails_closed_when_column_not_1536(monkeypatch):
    # OpenAI is always 1536; pointing the column elsewhere must fail closed.
    monkeypatch.setattr(settings, "AINDY_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setattr(settings, "AINDY_EMBEDDING_DIMENSIONS", 384)
    with pytest.raises(EmbeddingProviderError):
        embedding_service.get_embedding_provider()
