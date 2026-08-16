"""ROUTE-EFFECT-BYPASS-1 (C) — `sys.v1.memory.link`, and why it earns its own capability.

`POST /memory/links` reached `MemoryNodeDAO.create_link` directly, so building the memory graph
passed no capability check, no tenant-isolation check and no effect ledger.

★ **The syscall carries `memory.link`, not `memory.write`.** A syscall that adds mediation but no
authority granularity is not worth the public surface — it would just relocate the same
undifferentiated power behind a longer call path. Writing a *node* and wiring the *graph between
nodes* are different powers, and `memory.delete` already set the precedent of a memory capability
that `memory.write` does not grant.

`test_link_capability_is_not_granted_by_memory_write` is the test that makes that claim real
rather than decorative.
"""
from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.runtime_only

ROUTER = pathlib.Path(__file__).resolve().parents[2] / "AINDY" / "routes" / "memory_router.py"


class _FakeSession:
    def flush(self): pass
    def commit(self): pass
    def rollback(self): pass
    def close(self): pass


class _Ctx:
    user_id = "tenant-a"
    execution_unit_id = "eu-1"
    metadata = {"_db": _FakeSession()}


def _entry():
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY

    return SYSCALL_REGISTRY["sys.v1.memory.link"]


# --------------------------------------------------------------------------------------
# ★ The authority claim
# --------------------------------------------------------------------------------------


def test_link_capability_is_not_granted_by_memory_write():
    """The whole justification for the syscall existing.

    If linking rode on `memory.write`, this call would add a mediation hop and no authority
    distinction — a caller able to store a node could rewire the graph, and the syscall would
    be surface for its own sake.
    """
    entry = _entry()

    assert entry.capability == "memory.link"
    assert entry.capability != "memory.write", (
        "linking must not ride on memory.write — a node write and a graph edit are different "
        "powers, and reusing the capability makes this syscall pure overhead"
    )


def test_the_dispatcher_actually_enforces_that_distinction():
    """Behavioural half — the declaration above is inert unless the check reads it.

    A context holding only `memory.write` must be refused, or the separate capability is a
    label rather than a boundary.
    """
    from AINDY.kernel.syscall_dispatcher import SyscallContext, get_dispatcher

    ctx = SyscallContext(
        execution_unit_id="",
        user_id="tenant-a",
        capabilities=["memory.write"],  # deliberately NOT memory.link
        trace_id="",
    )
    envelope = get_dispatcher().dispatch(
        "sys.v1.memory.link", {"source_id": "a", "target_id": "b"}, ctx
    )

    assert envelope.get("status") != "success", (
        "a memory.write-only caller reached memory.link — the capability split is decorative"
    )


def test_capability_inference_resolves_to_the_same_name():
    """Otherwise the syscall is unreachable via the inference-based dispatch paths.

    `memory.delete` shows the failure mode: inference yields `memory.write` while the entry
    requires `memory.delete`, so an inferred dispatch of delete is always denied. Linking must
    not inherit that mismatch, or the route rewire would 403 on every call.
    """
    from AINDY.kernel.syscall_dispatcher import _infer_dispatch_capability

    assert _infer_dispatch_capability("sys.v1.memory.link") == _entry().capability


# --------------------------------------------------------------------------------------
# Registration contract
# --------------------------------------------------------------------------------------


def test_link_is_declared_exactly_once():
    """`create_link` inserts a row, so a retry builds a SECOND edge between the same pair."""
    assert _entry().execution_guarantee == "EXACTLY_ONCE"


def test_registry_floor_was_raised():
    from AINDY.kernel.syscall_registry import (
        SYSCALL_REGISTRY,
        SYSCALL_REGISTRY_MIN_COUNT,
    )

    assert len(SYSCALL_REGISTRY) >= SYSCALL_REGISTRY_MIN_COUNT
    assert SYSCALL_REGISTRY_MIN_COUNT >= 24, "the floor must account for the new syscall"


def test_link_is_not_in_the_sdk_rename_guard():
    """Deliberately experimental: the graph surface is still moving.

    Pinned so adding it to the guard is a decision someone makes, not something that happens by
    copy-paste from a stable neighbour.
    """
    assert _entry().stable is False


def test_link_is_deliberately_off_the_public_dispatch_surface():
    """★ An omission stated as a decision, so nobody "fixes" it by accident.

    `POST /platform/syscall` grants only capabilities listed in `_DISPATCH_CAPABILITY_SCOPES`;
    everything else yields an empty grant and the dispatcher denies it. `memory.link` is
    **not** listed, so the syscall is reachable from the HTTP route (which is where it already
    had a caller) and not from the SDK dispatch surface.

    That is the conservative order for a `stable=False` entry: publishing an experimental
    syscall to SDK callers is the half that cannot be withdrawn. Adding it later means adding a
    `Scopes.MEMORY_LINK` of its own — mapping it onto `MEMORY_WRITE` would undo at the scope
    layer exactly the authority split the capability makes above.
    """
    from AINDY.routes.platform.platform_ops_router import _DISPATCH_CAPABILITY_SCOPES

    assert "memory.link" not in _DISPATCH_CAPABILITY_SCOPES
    assert "memory.delete" in _DISPATCH_CAPABILITY_SCOPES, (
        "sanity: the map is still the mechanism this test is asserting against"
    )


def test_an_sdk_dispatch_of_link_gets_no_grant():
    """Behavioural half — the map above is only meaningful if the resolver reads it."""
    from AINDY.routes.platform.platform_ops_router import _resolve_dispatch_capabilities

    granted = _resolve_dispatch_capabilities(
        "sys.v1.memory.link", {"sub": "u1", "auth_type": "jwt"}
    )

    assert granted == [], (
        "an off-surface syscall must yield an empty grant so the dispatcher denies it with its "
        "own canonical error"
    )


# --------------------------------------------------------------------------------------
# Tenant scoping — a foreign node must be indistinguishable from a missing one
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["source", "target"])
def test_a_node_the_caller_cannot_see_is_reported_as_not_found(missing, monkeypatch):
    """★ Not merely "rejected" — reported identically to a node that does not exist.

    Distinguishing the two would make this route an existence oracle for other tenants' ids,
    which is the `/auth/register` enumeration shape in a different place.
    """
    from AINDY.kernel import syscall_registry as sr

    seen = {}

    class _FakeDAO:
        def __init__(self, db):
            pass

        def get_by_id(self, node_id, user_id=None):
            seen[node_id] = user_id
            # The "foreign" node resolves to None *because* the lookup is tenant-scoped.
            return None if node_id == missing else {"id": node_id}

        def create_link(self, *a, **k):  # pragma: no cover - must not be reached
            raise AssertionError("create_link ran despite an unresolvable endpoint")

    import AINDY.db.dao.memory_node_dao as dao_mod

    original = dao_mod.MemoryNodeDAO
    dao_mod.MemoryNodeDAO = _FakeDAO
    try:
        with pytest.raises(LookupError, match="not found"):
            sr._handle_memory_link(
                {"source_id": "source", "target_id": "target"}, _Ctx()
            )
    finally:
        dao_mod.MemoryNodeDAO = original

    assert seen["source"] == "tenant-a", "endpoint lookup was not tenant-scoped"


def test_missing_ids_are_rejected_before_any_db_work():
    from AINDY.kernel import syscall_registry as sr

    with pytest.raises(ValueError, match="source_id"):
        sr._handle_memory_link({"source_id": "", "target_id": "b"}, _Ctx())


# --------------------------------------------------------------------------------------
# The route
# --------------------------------------------------------------------------------------


def test_route_dispatches_and_keeps_its_status_contract():
    """404 for an absent node, 422 for a refused link — not both collapsed to 400.

    A client cannot tell "your node is gone" from "your link is invalid" by a single status,
    which is the ROUTE-GUARD-1 lesson.
    """
    source = ROUTER.read_text(encoding="utf-8")
    block = source[source.index('@router.post("/links"') : source.index('@router.get("/nodes/{node_id}/traverse")')]

    assert "MemoryNodeDAO" not in block, "create_link still reaches the DAO directly"
    assert '_dispatch_memory(\n                "sys.v1.memory.link"' in block
    assert "status_code=404" in block and "status_code=422" in block


def test_the_reference_doc_documents_it_and_does_not_overstate_it():
    """`SYSCALL-STABILITY-1` was exactly this: the reference claimed `stable` for four syscalls
    registered `stable=False`. A doc claim with no test is the `DOCS-COVERAGE-CLAIM-1` shape.
    """
    doc = (
        pathlib.Path(__file__).resolve().parents[2]
        / "docs" / "runtime" / "SYSCALL_REFERENCE.md"
    ).read_text(encoding="utf-8")

    section = doc[doc.index("### `sys.v1.memory.link`") : doc.index("### `sys.v1.memory.search`")]

    assert "**Capability:** `memory.link`" in section
    assert "experimental" in section, "the doc must not imply a pinned name for a stable=False entry"
    assert "**Stability:** stable" not in section
    assert "POST /platform/syscall" in section, (
        "the off-surface fact is the one an SDK reader most needs; it must be stated where they "
        "will look, not only in the changelog"
    )


def test_only_the_search_route_still_bypasses():
    """Was 2 after A+B; C leaves exactly one.

    `search_similar_nodes` stays because it calls `dao.find_similar` with `min_similarity`,
    which `sys.v1.memory.search` neither accepts nor uses — rewiring it would change search
    semantics under cover of a mediation fix.
    """
    source = ROUTER.read_text(encoding="utf-8")

    assert len(re.findall(r"MemoryNodeDAO\(db\)", source)) == 1
