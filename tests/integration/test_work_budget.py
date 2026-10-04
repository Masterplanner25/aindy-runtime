"""PERF-BASELINE-1 — work budgets on the real paths, counted, on real Postgres.

The entry asked for "a per-effect and per-turn number on the real path, recorded, with a
regression bound", and warned against two failure modes: a wall-clock threshold on shared CI (a
flake generator, `FLAKY-1`), and a check that is green because it measured nothing (variant 9).
So every number here is COUNTED WORK (`tests/integration/work_counter.py`), and every bound has a
control that makes it fail.

Three paths, the two that regressed once and the one the flag backlog is waiting on:

1. **Recall's query count does not grow with its candidate set** (`MEM-RECALL-N1-1`, fixed #458:
   it issued 3 queries per candidate). Asserted through the full `MemoryNodeDAO.recall()`, not a
   helper.
2. **Recall holds no connection across the query-embedding call** (`RT-MEMTXN-LEAK-1`: a held
   connection sat `idle in transaction` through a seconds-long API call and drained the pool).
   Asserted as a HOLD COUNT sampled inside the call, with a control that holds one on purpose.
3. **The durable path's per-effect cost**, the number the eight "soak then flip" items had no
   instrument for: an `EXACTLY_ONCE` dispatch through the real dispatcher and effect ledger. The
   gate's overhead (gate on minus gate off) is RECORDED as a budget, and the per-effect count must
   not grow as the ledger fills.

Budgets are exact counts observed on Postgres 15 when this was written. A change that ADDS work
fails here and must either be fixed or raise the budget in the same PR, where review can see it.
A change that removes work should lower the budget, so that it stays tight.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from tests.integration.work_counter import count_work

pytestmark = pytest.mark.integration


@pytest.fixture
def engine(test_engine):
    if test_engine.dialect.name != "postgresql":
        pytest.skip("work budgets are recorded on Postgres")
    return test_engine


# ── 1. recall: no per-candidate queries ─────────────────────────────────────────────────────


def _seed_tagged(session, user_id, tag: str, n: int) -> None:
    from AINDY.memory.memory_persistence import MemoryNodeModel

    # Owned rows: recall's owner scope does not return ownerless ones.
    for i in range(n):
        session.add(MemoryNodeModel(content=f"note {i} about {tag}", tags=[tag], node_type="insight",
                                    memory_type="insight", user_id=user_id,
                                    embedding_pending=False, embedding_status="skipped"))
    session.flush()


def _recall_queries(engine, session, user_id, tag: str) -> tuple[int, int]:
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

    with count_work(engine) as work:
        results = MemoryNodeDAO(session).recall(tags=[tag], limit=5, user_id=str(user_id))
    work.assert_saw_work()
    return work.queries, len(results)


def test_recall_query_count_does_not_grow_with_candidates(engine, db_session, test_user):
    small, large = f"few-{uuid.uuid4().hex[:6]}", f"many-{uuid.uuid4().hex[:6]}"
    _seed_tagged(db_session, test_user.id, small, 2)
    _seed_tagged(db_session, test_user.id, large, 15)  # limit=5: the tag path takes up to limit*3 = 15

    few_q, few_n = _recall_queries(engine, db_session, test_user.id, small)
    many_q, many_n = _recall_queries(engine, db_session, test_user.id, large)
    print(f"[work-budget] recall(tags, limit=5): {few_q} queries for {few_n} results; "
          f"{many_q} queries for {many_n} results")

    assert few_n == 2 and many_n == 5, "control: the seeded rows are what recall returned"
    assert many_q == few_q, (
        f"recall issued {many_q} queries over 15 candidates and {few_q} over 2: the count grows with "
        f"the candidate set, so a per-candidate query is back (MEM-RECALL-N1-1)"
    )
    assert many_q <= RECALL_TAG_QUERY_BUDGET, (many_q, RECALL_TAG_QUERY_BUDGET)


# ── 2. recall: no connection held across the embedding call ─────────────────────────────────


def _held_during_embedding(engine, session) -> int:
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

    seen: list[int] = []
    with count_work(engine) as work:
        def _embed(_query):
            seen.append(work.held)
            return None  # unusable: recall falls back to text matching, which still queries
        with patch("AINDY.memory.embedding_service.generate_query_embedding", _embed):
            MemoryNodeDAO(session).recall(query="how did we handle auth", limit=3)
    work.assert_saw_work()
    assert seen, "the embedding call was never reached; the probe measured nothing"
    return seen[0]


def test_recall_holds_no_connection_across_the_embedding_call(engine, testing_session_factory):
    session = testing_session_factory()
    try:
        assert _held_during_embedding(engine, session) == 0, (
            "a pooled connection was checked out while the query embedding was being generated; "
            "on a seconds-long API call that is RT-MEMTXN-LEAK-1's idle-in-transaction pool drain"
        )
    finally:
        session.close()


def test_control_a_session_already_in_a_transaction_is_seen_holding(engine, testing_session_factory):
    """Liveness for the probe above: a session that queried first DOES hold its connection, and
    the probe must say so, or the zero above would mean nothing."""
    from sqlalchemy import text

    session = testing_session_factory()
    try:
        with count_work(engine) as work:
            session.execute(text("SELECT 1"))
            held_after_query = work.held
        assert held_after_query == 1, "control: the counter does not see a held connection"
    finally:
        session.close()


# ── 3. the durable path: per-effect work ────────────────────────────────────────────────────


class _OkRm:
    def check_quota(self, _x):
        return True, None

    def record_usage(self, _x, _u):
        return None


def _register_probe():
    from AINDY.kernel import syscall_registry as R

    name = f"sys.v1.test.budget_{uuid.uuid4().hex[:8]}"
    runs: list[int] = []

    def handler(payload, ctx):
        runs.append(1)
        return {"ok": True}

    R.SYSCALL_REGISTRY[name] = R.SyscallEntry(handler=handler, capability="test.budget",
                                              execution_guarantee="EXACTLY_ONCE")
    return name, runs


def _dispatch_counted(engine, name: str, eu_id: str, payload: dict) -> int:
    from AINDY.kernel import syscall_dispatcher as D
    from AINDY.kernel import syscall_registry as R

    dispatcher = D.SyscallDispatcher()
    ctx = R.SyscallContext(execution_unit_id=eu_id, user_id=str(uuid.uuid4()),
                           capabilities=["test.budget"], trace_id="budget")
    with patch.object(D, "_get_rm", lambda: _OkRm()), count_work(engine) as work:
        envelope = dispatcher.dispatch(name, payload, ctx)
    assert envelope.get("status") == "success", envelope
    return work.queries


def test_the_gate_overhead_per_effect_is_recorded(engine, monkeypatch):
    """The per-effect number: what the effect ledger adds to one EXACTLY_ONCE dispatch."""
    name, runs = _register_probe()
    eu = str(uuid.uuid4())

    monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY", "false")
    off = _dispatch_counted(engine, name, eu, {"n": "off"})
    monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY", "true")
    on = _dispatch_counted(engine, name, eu, {"n": "on"})
    replay = _dispatch_counted(engine, name, eu, {"n": "on"})
    print(f"[work-budget] EXACTLY_ONCE dispatch: gate off {off} queries; gate on {on} "
          f"(overhead {on - off}); replay {replay}")

    assert len(runs) == 2, "control: the replay must not re-run the handler, or the gate is not engaged"
    assert on > off, "the gate added no queries; it did not run, and the overhead below is vacuous"
    assert on - off <= GATE_OVERHEAD_BUDGET, (on - off, GATE_OVERHEAD_BUDGET)
    assert on <= EFFECT_QUERY_BUDGET, (on, EFFECT_QUERY_BUDGET)
    assert replay <= REPLAY_QUERY_BUDGET, (replay, REPLAY_QUERY_BUDGET)


def test_per_effect_work_does_not_grow_as_the_ledger_fills(engine, monkeypatch):
    monkeypatch.setenv("AINDY_SYSCALL_IDEMPOTENCY", "true")
    name, runs = _register_probe()
    eu = str(uuid.uuid4())

    first = _dispatch_counted(engine, name, eu, {"n": 0})
    for i in range(1, 26):
        _dispatch_counted(engine, name, eu, {"n": i})
    later = _dispatch_counted(engine, name, eu, {"n": 26})
    print(f"[work-budget] EXACTLY_ONCE effect #1: {first} queries; effect #27: {later}")

    assert len(runs) == 27, "control: every distinct payload is a distinct effect"
    assert later == first, (
        f"effect #27 cost {later} queries and effect #1 cost {first}: per-effect work grows with "
        f"the ledger, so the gate is scanning rather than keying"
    )


# Budgets: exact counts observed on Postgres 15 when this file was written (see module docstring).
# recall(tags, limit=5): 3 queries whatever the candidate count: one `memory_nodes` SELECT and two
# grouped `memory_links` SELECTs (MEM-RECALL-N1-1's batched connectivity).
RECALL_TAG_QUERY_BUDGET = 3
# One EXACTLY_ONCE dispatch. Gate off: 1 query (the `syscall.executed` system_events INSERT). Gate on:
# 5, so the effect ledger costs 4 per effect: an `effect_records` lookup, the claim INSERT, a
# read-back SELECT and the completing UPDATE. A replay is 1 `effect_records` SELECT and emits no
# `syscall.executed` event.
GATE_OVERHEAD_BUDGET = 4
EFFECT_QUERY_BUDGET = 5
REPLAY_QUERY_BUDGET = 1
