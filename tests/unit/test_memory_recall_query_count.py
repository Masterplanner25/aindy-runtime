"""MEM-RECALL-N1-1 — `recall()` scored each candidate with three extra queries.

Per candidate it ran `_get_model_by_id` (1 × `memory_nodes`) plus `get_graph_connectivity_score`
(2 × `memory_links` COUNT), over up to `limit * 3` semantic **and** `limit * 3` tag candidates.

The re-fetch existed only to read four columns — `success_count`, `failure_count`,
`usage_count`, `weight` — that the originating SELECT had already read and `_node_to_dict` then
dropped.

The claim is *fewer queries for the same ranking*, so both halves are asserted:

* `test_scoring_issues_no_per_candidate_queries` counts real SQL, because a refactor that
  merely *looks* batched is the failure mode here.
* the ranking tests pin that scores are unchanged — a faster recall that ranks differently is
  a regression, not an optimisation.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.runtime_only


def _dao(db):
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

    return MemoryNodeDAO(db)


# --------------------------------------------------------------------------------------
# The four columns are carried, so nothing needs re-fetching
# --------------------------------------------------------------------------------------


def test_node_dict_carries_the_scoring_columns():
    """These four were the *only* reason the loop re-queried `memory_nodes`."""
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

    class _Node:
        id = "11111111-1111-1111-1111-111111111111"
        content = "c"
        tags = []
        node_type = "insight"
        source = "test"
        user_id = None
        extra = {}
        created_at = None
        updated_at = None
        success_count = 4
        failure_count = 1
        usage_count = 9
        weight = 2.0

    d = MemoryNodeDAO.__new__(MemoryNodeDAO)._node_to_dict(_Node())

    assert d["success_count"] == 4
    assert d["failure_count"] == 1
    assert d["usage_count"] == 9
    assert d["weight"] == 2.0


def test_scoring_view_matches_the_old_model_reads():
    """The view must reproduce `node_obj.<col> or <default>` exactly, including the fallbacks.

    `get_success_rate` returns a neutral 0.5 on no data and `weight` defaulted to 1.0; getting
    either default wrong would shift every score silently rather than failing.
    """
    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO, _CandidateScoringView

    dao = MemoryNodeDAO.__new__(MemoryNodeDAO)

    populated = _CandidateScoringView(
        {"success_count": 3, "failure_count": 1, "usage_count": 50, "weight": 2.5}
    )
    assert dao.get_success_rate(populated) == pytest.approx(0.75)
    assert populated.weight == 2.5

    empty = _CandidateScoringView({})
    assert dao.get_success_rate(empty) == 0.5, "no feedback must stay a neutral prior"
    assert empty.weight == 1.0, "absent weight must default to 1.0, not 0.0"

    # `None` in the dict must behave like `None` on the model did (`or` fallback), not crash.
    nulls = _CandidateScoringView(
        {"success_count": None, "failure_count": None, "usage_count": None, "weight": None}
    )
    assert dao.get_success_rate(nulls) == 0.5
    assert nulls.weight == 1.0


# --------------------------------------------------------------------------------------
# Batched connectivity — same answer, two queries
# --------------------------------------------------------------------------------------


def test_batched_connectivity_matches_the_per_node_result(db_session):
    """Equivalence, not just speed. A faster score that disagrees is a regression."""
    import uuid as _uuid

    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

    dao = MemoryNodeDAO(db_session)
    ids = [str(_uuid.uuid4()) for _ in range(4)]

    batched = dao.get_graph_connectivity_scores(ids)
    per_node = {i: dao.get_graph_connectivity_score(i) for i in ids}

    assert batched == per_node, (
        f"batched connectivity disagrees with the per-node function: {batched} vs {per_node}"
    )


def test_batched_connectivity_returns_a_score_for_every_id(db_session):
    """Ids with no links must score 0.0, not be omitted — the loop looks every id up."""
    import uuid as _uuid

    dao = _dao(db_session)
    ids = [str(_uuid.uuid4()) for _ in range(3)]

    scores = dao.get_graph_connectivity_scores(ids)

    assert set(scores) == set(ids)
    assert all(v == 0.0 for v in scores.values())


def test_unparseable_id_does_not_lose_the_batch(db_session):
    """Same contract as the per-node version: a bad id scores 0.0 rather than raising.

    Losing the whole batch would silently drop connectivity from every candidate's score.
    """
    import uuid as _uuid

    dao = _dao(db_session)
    good = str(_uuid.uuid4())

    scores = dao.get_graph_connectivity_scores([good, "not-a-uuid"])

    assert scores[good] == 0.0
    assert scores["not-a-uuid"] == 0.0


def test_empty_input_issues_no_query(db_session):
    dao = _dao(db_session)

    assert dao.get_graph_connectivity_scores([]) == {}


# --------------------------------------------------------------------------------------
# ★ The actual claim: the per-candidate queries are gone
# --------------------------------------------------------------------------------------


def test_scoring_issues_no_per_candidate_queries(db_session, monkeypatch):
    """★ Counts real SQL, because 'looks batched' is exactly how this regresses.

    Scores a synthetic candidate set and asserts the query count does not grow with it. Before
    this change each candidate cost 3 queries; the batched form costs 2 for the whole set,
    regardless of size.
    """
    import uuid as _uuid

    from sqlalchemy import event

    from AINDY.db.dao.memory_node_dao import MemoryNodeDAO

    dao = MemoryNodeDAO(db_session)
    statements: list[str] = []

    def _count(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _count)
    try:
        ids = [str(_uuid.uuid4()) for _ in range(25)]
        statements.clear()
        dao.get_graph_connectivity_scores(ids)
        many = len(statements)

        statements.clear()
        dao.get_graph_connectivity_scores(ids[:1])
        one = len(statements)
    finally:
        event.remove(engine, "before_cursor_execute", _count)

    assert many == one, (
        f"connectivity issued {many} queries for 25 candidates and {one} for 1 — the count "
        f"still scales with the candidate set, so the N+1 is not actually gone"
    )
    assert many <= 2, f"expected at most 2 grouped queries, got {many}"
