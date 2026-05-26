from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.runtime_only


def test_memory_bridge_constructs_with_user_id():
    from AINDY.nodus.runtime.memory_bridge import AINDYMemoryBridge

    bridge = AINDYMemoryBridge(user_id="test-user-123")
    assert bridge._user_id == "test-user-123"


def test_memory_bridge_safe_node_from_dict():
    from AINDY.nodus.runtime.memory_bridge import AINDYMemoryBridge

    node = {
        "id": "abc",
        "content": "hello",
        "tags": ["tag1"],
        "node_type": "insight",
        "significance": 0.8,
        "resonance_score": 0.7,
        "created_at": "2025-01-01T00:00:00Z",
        "source": "test",
        "memory_type": "explicit",
    }
    result = AINDYMemoryBridge._safe_node(node)
    assert result["id"] == "abc"
    assert result["content"] == "hello"
    assert result["tags"] == ["tag1"]
    assert result["node_type"] == "insight"


def test_memory_bridge_safe_node_from_object():
    from AINDY.nodus.runtime.memory_bridge import AINDYMemoryBridge

    node = SimpleNamespace(
        id="xyz",
        content="world",
        tags=["t"],
        node_type="decision",
        significance=0.5,
        resonance_score=0.6,
        created_at=None,
        source="nodus",
        memory_type="implicit",
    )
    result = AINDYMemoryBridge._safe_node(node)
    assert result["id"] == "xyz"
    assert result["content"] == "world"
    assert result["created_at"] is None


def test_memory_bridge_safe_node_null_tags_defaults_to_empty_list():
    from AINDY.nodus.runtime.memory_bridge import AINDYMemoryBridge

    result = AINDYMemoryBridge._safe_node({"id": "1", "content": "text", "tags": None})
    assert result["tags"] == []
