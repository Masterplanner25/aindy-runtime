"""ROUTE-EFFECT-BYPASS-1 — memory routes must reach effects through the dispatcher.

Four routes in `memory_router.py` called `MemoryNodeDAO` directly with the request's own
session, so the effect passed **no capability check, no tenant-isolation check, no quota
accounting and no effect ledger**. A scope decorator would not have helped: the effect never
reached the chokepoint that reads scopes.

Two are rewired here (`POST /nodes`, `POST /recall`). Two are not, for reasons that are
asserted rather than left in a comment — see `test_remaining_direct_dao_calls_are_the_expected_two`.

★ The sharp test is `test_caller_supplied_extra_survives_the_write`. `_handle_memory_write`
hard-set `extra={"execution_unit_id": ...}`, discarding any caller `extra`. The route passes
`extra=body.extra`, so a naive rewire would have been **silent data loss** — a 201 with the
field quietly gone, not a failure. That is the failure mode this whole entry is about, one
level down.
"""
from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.runtime_only

ROUTER = pathlib.Path(__file__).resolve().parents[2] / "AINDY" / "routes" / "memory_router.py"


class _FakeSession:
    """Enough of a Session for `_acquire_handler_db` / `_finish_handler_write`."""

    def flush(self):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


# --------------------------------------------------------------------------------------
# The write path
# --------------------------------------------------------------------------------------


def test_caller_supplied_extra_survives_the_write():
    """★ The trap. `memory.write` used to replace `extra`, not merge it.

    Routing `POST /nodes` through the syscall without this fix would have dropped
    `body.extra` on every create — a silent regression behind a 201.
    """
    from AINDY.kernel import syscall_registry as sr

    captured = {}

    class _FakeDAO:
        def __init__(self, db):
            pass

        def save(self, **kwargs):
            captured.update(kwargs)
            return {"id": "n1"}

    class _Ctx:
        user_id = "u1"
        execution_unit_id = "eu-1"
        # A bare object() fails in `_finish_handler_write` with a confusing AttributeError on
        # flush — the fake has to be faithful enough that the test fails on its assertion.
        metadata = {"_db": _FakeSession()}

    import AINDY.db.dao.memory_node_dao as dao_mod

    original = dao_mod.MemoryNodeDAO
    dao_mod.MemoryNodeDAO = _FakeDAO
    try:
        sr._handle_memory_write(
            {"content": "c", "extra": {"caller_key": "kept"}}, _Ctx()
        )
    finally:
        dao_mod.MemoryNodeDAO = original

    assert captured["extra"]["caller_key"] == "kept", (
        "the caller's `extra` was discarded — routing the route through this syscall would be "
        "silent data loss behind a 201"
    )
    assert captured["extra"]["execution_unit_id"] == "eu-1", (
        "provenance must still be recorded alongside the caller's data"
    )


def test_execution_id_wins_a_key_collision():
    """Provenance is not caller-writable — a caller cannot forge `execution_unit_id`."""
    from AINDY.kernel import syscall_registry as sr

    captured = {}

    class _FakeDAO:
        def __init__(self, db):
            pass

        def save(self, **kwargs):
            captured.update(kwargs)
            return {"id": "n1"}

    class _Ctx:
        user_id = "u1"
        execution_unit_id = "real-eu"
        metadata = {"_db": _FakeSession()}

    import AINDY.db.dao.memory_node_dao as dao_mod

    original = dao_mod.MemoryNodeDAO
    dao_mod.MemoryNodeDAO = _FakeDAO
    try:
        sr._handle_memory_write(
            {"content": "c", "extra": {"execution_unit_id": "forged"}}, _Ctx()
        )
    finally:
        dao_mod.MemoryNodeDAO = original

    assert captured["extra"]["execution_unit_id"] == "real-eu"


# --------------------------------------------------------------------------------------
# The rewiring itself
# --------------------------------------------------------------------------------------


def test_dispatch_helper_grants_least_privilege():
    """The helper must grant the syscall's own capability, not a blanket memory grant.

    A helper that granted `memory.write` for every call would make the capability check
    vacuous — the thing the bypass was already achieving by accident.
    """
    from AINDY.kernel.syscall_dispatcher import _infer_dispatch_capability

    assert _infer_dispatch_capability("sys.v1.memory.write") == "memory.write"
    assert _infer_dispatch_capability("sys.v1.memory.read") == "memory.read"


def test_dispatch_helper_reuses_the_request_session():
    """Opening a second session per request is the RT-MEMTXN-LEAK-1 shape.

    The helper must hand the request's session to the handler via `_db`, so the write stays in
    the caller's transaction rather than taking a concurrent connection.
    """
    source = ROUTER.read_text(encoding="utf-8")
    helper = source[source.index("def _dispatch_memory") : source.index("def _mem_run_flow")]

    assert '"_db": db' in helper, (
        "the dispatch helper does not pass the request session — a second connection per "
        "request is what RT-MEMTXN-LEAK-1 traced to pool exhaustion"
    )


def test_dispatch_helper_raises_rather_than_returning_an_error_body():
    """A failed effect must be a status code, not a 200 carrying an error (ROUTE-GUARD-1)."""
    source = ROUTER.read_text(encoding="utf-8")
    helper = source[source.index("def _dispatch_memory") : source.index("def _mem_run_flow")]

    assert "HTTPException" in helper
    assert 'envelope.get("status") != "success"' in helper


# --------------------------------------------------------------------------------------
# What is deliberately NOT rewired — asserted, not commented
# --------------------------------------------------------------------------------------


def test_remaining_direct_dao_calls_are_the_expected_two():
    """Pins which routes still bypass, and therefore what is left to do.

    `create_link` has **no syscall equivalent** (a build, not a rewire) and
    `search_similar_nodes` calls `dao.find_similar` with `min_similarity`, which
    `sys.v1.memory.search` neither accepts nor uses — it calls `dao.recall`. Rewiring that one
    would change search *semantics* under cover of a mediation fix.

    If this count drops, the remaining work landed. If it rises, a new bypass was introduced.
    """
    source = ROUTER.read_text(encoding="utf-8")
    direct = len(re.findall(r"MemoryNodeDAO\(db\)", source))

    assert direct == 2, (
        f"expected exactly 2 remaining direct-DAO routes (create_link, search_similar_nodes), "
        f"found {direct}. A drop means the remaining work landed; a rise means a new bypass."
    )


def test_rewired_routes_no_longer_construct_a_dao():
    """The two rewired handlers must not have kept a DAO path alongside the dispatch."""
    source = ROUTER.read_text(encoding="utf-8")

    create = source[source.index("async def create_node") : source.index("async def get_node")]
    recall = source[source.index("async def recall_memories") : source.index("async def recall_v3")]

    for name, block in (("create_node", create), ("recall_memories", recall)):
        # Match the CONSTRUCTION, not the bare name — the explanatory comment in these
        # handlers legitimately mentions `MemoryNodeDAO.save`, and a name check flagged it.
        assert "MemoryNodeDAO(db)" not in block, f"{name} still constructs a DAO directly"
        assert "_dispatch_memory(" in block, f"{name} does not dispatch"
