"""ECOGAP-3 Phase 1 (Increment 2) — re-embed migration guards.

The destructive column ALTER + re-embed needs PostgreSQL to verify end-to-end, but the
fail-closed guards run before any DB mutation and are unit-testable: a provider/dimension
mismatch is refused before touching the schema, and a non-PostgreSQL engine is rejected.
"""
from __future__ import annotations

import pytest

from AINDY.config import settings
from AINDY.memory import embedding_service
from AINDY.memory.embedding_migration import reembed_all_memory_nodes
from AINDY.memory.embedding_providers import EmbeddingProviderError

pytestmark = pytest.mark.runtime_only


@pytest.fixture(autouse=True)
def _reset_provider():
    embedding_service.reset_embedding_provider()
    yield
    embedding_service.reset_embedding_provider()


def test_reembed_fails_closed_on_dimension_mismatch(monkeypatch):
    # OpenAI is 1536; column pinned to 384 → refused before any DDL.
    monkeypatch.setattr(settings, "AINDY_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setattr(settings, "AINDY_EMBEDDING_DIMENSIONS", 384)
    with pytest.raises(EmbeddingProviderError):
        reembed_all_memory_nodes()


def test_reembed_requires_postgres(monkeypatch):
    # Default provider/dimension validate fine, but the test engine is SQLite → refused
    # before touching data (the pgvector column ALTER has no SQLite equivalent).
    monkeypatch.setattr(settings, "AINDY_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setattr(settings, "AINDY_EMBEDDING_DIMENSIONS", 1536)
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        reembed_all_memory_nodes(dry_run=True)
