"""FR-50 — a memory route's optional field left out must not fail the syscall's input schema.

`_dispatch_memory` forwarded the request model's `None` defaults, and `sys.v1.memory.read` /
`sys.v1.memory.write` type those fields (`query: string`, `tags: list`, `node_type: string`). The
dispatcher refused them before the handler ran, so three ordinary calls answered 400:

* `POST /memory/recall` with only `query` (the app's filing, found testing its Work model);
* `POST /memory/recall` with only `tags` (the same defect, not in the filing);
* `POST /memory/nodes` without `node_type` (the create route, for its own optional field).

The existing scope test's write probe (`{"content": "c"}`) asserted only "not 403", which is how
the 400 stayed invisible. These tests CALL THE ROUTES with the real dispatcher and its schema
validation. Only the DAO underneath is stubbed (no pgvector here), and each asserts both the
status and what the handler received.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.runtime_only

_USER = "00000000-0000-0000-0000-000000000050"


@pytest.fixture
def client_with_memory_scopes(runtime_only_app, runtime_only_client):
    from AINDY.services.auth_service import get_current_user

    runtime_only_app.dependency_overrides[get_current_user] = lambda: {
        "sub": _USER, "user_id": _USER, "auth_type": "jwt",
        "session_scopes": ["memory.read", "memory.write"],
    }
    yield runtime_only_client
    runtime_only_app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def dao_calls(monkeypatch):
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

    calls: dict = {}

    # A list: the request pipeline's own per-request recall (FR-49's path) calls `recall` too.
    def _recall(self, **kw):
        calls.setdefault("recall", []).append(kw)
        return [{"id": "n1", "content": "found"}]

    def _save(self, **kw):
        calls["save"] = kw
        return {"id": "n2", "content": kw.get("content"), "node_type": kw.get("node_type")}

    monkeypatch.setattr(MemoryNodeDAO, "recall", _recall, raising=True)
    monkeypatch.setattr(MemoryNodeDAO, "save", _save, raising=True)
    return calls


@pytest.mark.parametrize("body, expect", [
    ({"query": "published writing"}, {"query": "published writing", "tags": None, "node_type": None}),
    ({"tags": ["work"]}, {"query": None, "tags": ["work"], "node_type": None}),
    ({"query": "q", "tags": [], "node_type": "insight"}, {"query": "q", "tags": [], "node_type": "insight"}),
], ids=["query-only (the filing)", "tags-only", "all fields (control)"])
def test_recall_with_optional_fields_left_out(client_with_memory_scopes, dao_calls, body, expect):
    response = client_with_memory_scopes.post("/memory/recall", json=body)
    assert response.status_code == 200, response.text[:400]
    payload = response.json()
    data = payload.get("data", payload)
    assert data["count"] == 1 and data["results"][0]["id"] == "n1"
    matching = [c for c in dao_calls["recall"] if all(c.get(k) == v for k, v in expect.items())]
    assert matching, f"no recall reached the DAO with {expect}: {dao_calls['recall']}"


def test_recall_with_neither_query_nor_tags_is_still_400(client_with_memory_scopes, dao_calls):
    """The route's own guard is unchanged: dropping None must not make an empty recall legal."""
    response = client_with_memory_scopes.post("/memory/recall", json={"limit": 3})
    assert response.status_code == 400, response.text[:300]
    assert not any(c.get("limit") == 3 for c in dao_calls.get("recall", [])), dao_calls


def test_create_node_without_node_type(client_with_memory_scopes, dao_calls):
    response = client_with_memory_scopes.post("/memory/nodes", json={"content": "a note"})
    assert response.status_code in (200, 201), response.text[:400]
    assert dao_calls["save"]["content"] == "a note"


def test_the_dispatcher_never_receives_a_none(monkeypatch, client_with_memory_scopes, dao_calls):
    """The mechanism: what reaches `dispatch` carries no None value."""
    from AINDY.kernel import syscall_dispatcher

    real_get = syscall_dispatcher.get_dispatcher
    seen: list = []

    class _Spy:
        def __init__(self, inner):
            self._inner = inner

        def dispatch(self, name, payload, ctx):
            seen.append((name, dict(payload)))
            return self._inner.dispatch(name, payload, ctx)

    monkeypatch.setattr(syscall_dispatcher, "get_dispatcher", lambda: _Spy(real_get()))
    client_with_memory_scopes.post("/memory/recall", json={"query": "q"})
    client_with_memory_scopes.post("/memory/nodes", json={"content": "c"})
    assert {name for name, _ in seen} == {"sys.v1.memory.read", "sys.v1.memory.write"}, seen
    for name, payload in seen:
        assert None not in payload.values(), (name, payload)
