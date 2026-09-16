"""`SYSEVENT-RETENTION-1` — a retention class per event TYPE, pruning LEAVES only.

Design: ``docs/design/SYSEVENT_RETENTION_DESIGN.md`` §2 (the FK rule), §3 (unclassified = keep),
§5 (the job), §9 (these tests — each guard has its liveness control).

★ On the SQLite harness `PRAGMA foreign_keys=OFF`, so the DATABASE would let every delete through.
That is the point: what these tests exercise is the module's own leaf predicate, which is the
guard that must hold where the FK would refuse (four `NO ACTION` columns) AND where it would not
(`event_edges` is `CASCADE`). Every "not selected" assertion is paired with the same row selected
once its referrer is gone — without the control the anti-joins could be vacuously true.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from AINDY.core import system_event_retention as ret
from AINDY.core.system_event_retention import (
    MODE_OFF,
    MODE_PRUNE,
    MODE_REPORT,
    RETENTION_AUDIT,
    RETENTION_KEEPALIVE,
    RETENTION_OPERATIONAL,
    count_prunable,
    prune_system_events,
    register_event_retention,
    reset_event_retention_registry,
    retention_class_for,
    retention_mode,
    select_prunable_ids,
)

pytestmark = [pytest.mark.runtime_only, pytest.mark.usefixtures("db_session")]

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
OLD = NOW - timedelta(days=400)
KEEPALIVE = "watchdog.scan.completed"
OPERATIONAL = "execution.completed"
AUDIT = "execution.failed"


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_event_retention_registry()
    yield
    reset_event_retention_registry()


def _event(db, etype=KEEPALIVE, when=OLD, parent=None):
    from AINDY.db.models.system_event import SystemEvent

    row = SystemEvent(id=uuid.uuid4(), type=etype, timestamp=when, parent_event_id=parent)
    db.add(row)
    db.flush()
    return row


CUTOFF = NOW - timedelta(days=30)  # the keepalive/operational ceilings both fall inside a 400-day-old row


def _ids(db, etype=KEEPALIVE, cutoff=CUTOFF, limit=100):
    return set(select_prunable_ids(db, event_type=etype, cutoff=cutoff, limit=limit))


# ---------------------------------------------------------------------------
# Classes and the registry (§3, §4)
# ---------------------------------------------------------------------------

def test_seed_classes_the_designed_examples():
    assert retention_class_for(KEEPALIVE) == RETENTION_KEEPALIVE
    assert retention_class_for("health.liveness.completed") == RETENTION_KEEPALIVE
    assert retention_class_for(OPERATIONAL) == RETENTION_OPERATIONAL
    assert retention_class_for("autonomy.decision") == RETENTION_OPERATIONAL  # DEC-028
    assert retention_class_for(AUDIT) == RETENTION_AUDIT
    assert retention_class_for("capability.denied") == RETENTION_AUDIT
    assert retention_class_for("auth.login.completed") == RETENTION_AUDIT


def test_failure_shaped_events_are_audit_even_inside_an_operational_family():
    """`execution.failed` (exact, audit) beats `execution.*` (glob, operational)."""
    assert retention_class_for("execution.started") == RETENTION_OPERATIONAL
    assert retention_class_for("execution.failed") == RETENTION_AUDIT
    assert retention_class_for("flow.node.completed") == RETENTION_OPERATIONAL
    assert retention_class_for("flow.node.failed") == RETENTION_AUDIT


def test_unclassified_is_none_which_means_keep():
    assert retention_class_for("app.something.new") is None


def test_register_overrides_seed_and_refuses_unknown_classes():
    register_event_retention("app.something.new", RETENTION_KEEPALIVE)
    assert retention_class_for("app.something.new") == RETENTION_KEEPALIVE
    register_event_retention("autonomy.decision", RETENTION_AUDIT)
    assert retention_class_for("autonomy.decision") == RETENTION_AUDIT
    with pytest.raises(ValueError):
        register_event_retention("app.x", "forever")  # a typo must not read as "keep"


def test_every_seed_class_is_known():
    assert set(ret.RUNTIME_RETENTION_SEED.values()) <= set(ret.RETENTION_CLASSES)


def test_every_enum_event_type_has_a_seed_class():
    """The runtime's own declared types must not start life unclassified (derived, non-empty)."""
    from AINDY.core.system_event_types import SystemEventTypes

    names = [v for k, v in vars(SystemEventTypes).items() if isinstance(v, str) and not k.startswith("_")]
    assert len(names) > 30
    missing = [n for n in names if retention_class_for(n) is None]
    assert not missing, missing


# ---------------------------------------------------------------------------
# Mode (§5) — unset is off, a typo is off
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    (None, MODE_OFF), ("", MODE_OFF), ("  ", MODE_OFF), ("delete", MODE_OFF), ("PRUNE ", MODE_PRUNE),
    ("report", MODE_REPORT), ("prune", MODE_PRUNE),
])
def test_retention_mode_parsing(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv(ret.ENV_MODE, raising=False)
    else:
        monkeypatch.setenv(ret.ENV_MODE, raw)
    assert retention_mode() == expected


def test_off_mode_touches_nothing(db_session, monkeypatch):
    monkeypatch.delenv(ret.ENV_MODE, raising=False)
    leaf = _event(db_session)
    report = prune_system_events(db=db_session, now=NOW)
    assert report["mode"] == MODE_OFF and report["deleted"] == 0
    assert leaf.id in _ids(db_session)


# ---------------------------------------------------------------------------
# ★ Leaves only (§2) — each referrer, with its control
# ---------------------------------------------------------------------------

def test_a_leaf_older_than_the_cutoff_is_selected_and_a_young_one_is_not(db_session):
    old = _event(db_session)
    young = _event(db_session, when=NOW - timedelta(hours=1))
    assert _ids(db_session) == {old.id}
    assert young.id not in _ids(db_session)


def test_a_parent_is_never_selected_until_its_child_is_gone(db_session):
    parent = _event(db_session)
    child = _event(db_session, parent=parent.id)
    assert parent.id not in _ids(db_session)
    assert child.id in _ids(db_session)  # the child is a leaf
    db_session.delete(child)
    db_session.flush()
    assert parent.id in _ids(db_session)  # control: referrer gone → eligible


def test_an_event_an_agent_event_cites_is_never_selected(db_session):
    from AINDY.db.models.agent_event import AgentEvent

    cited = _event(db_session)
    ref = AgentEvent(
        id=uuid.uuid4(), run_id=uuid.uuid4(), user_id=uuid.uuid4(), event_type="x",
        system_event_id=cited.id, occurred_at=OLD,
    )
    db_session.add(ref)
    db_session.flush()
    assert cited.id not in _ids(db_session)
    db_session.delete(ref)
    db_session.flush()
    assert cited.id in _ids(db_session)


@pytest.mark.parametrize("column", ["source_event_id", "root_event_id"])
def test_an_event_a_memory_node_was_derived_from_is_never_selected(db_session, column):
    from AINDY.memory.memory_persistence import MemoryNodeModel as MemoryNode

    origin = _event(db_session)
    node = MemoryNode(id=uuid.uuid4(), content="c", node_type="insight", **{column: origin.id})
    db_session.add(node)
    db_session.flush()
    assert origin.id not in _ids(db_session)
    db_session.delete(node)
    db_session.flush()
    assert origin.id in _ids(db_session)


@pytest.mark.parametrize("end", ["source_event_id", "target_event_id"])
def test_an_edge_endpoint_is_never_selected_even_though_the_database_would_cascade(db_session, end):
    """★ The one referrer the FK would NOT refuse — deleting it silently drops the causal edge."""
    from AINDY.db.models.event_edge import EventEdge

    endpoint = _event(db_session)
    other = _event(db_session, etype=OPERATIONAL)
    fields = {"source_event_id": other.id, "target_event_id": endpoint.id}
    if end == "source_event_id":
        fields = {"source_event_id": endpoint.id, "target_event_id": other.id}
    edge = EventEdge(id=uuid.uuid4(), relationship_type="caused", **fields)
    db_session.add(edge)
    db_session.flush()
    assert endpoint.id not in _ids(db_session)
    db_session.delete(edge)
    db_session.flush()
    assert endpoint.id in _ids(db_session)


# ---------------------------------------------------------------------------
# The job (§5)
# ---------------------------------------------------------------------------

def _seed_mixed(db):
    """3 old keepalives, 2 old operational, 1 old audit, 1 old unclassified, 1 young keepalive."""
    rows = {
        "keep": [_event(db) for _ in range(3)],
        "oper": [_event(db, etype=OPERATIONAL) for _ in range(2)],
        "audit": [_event(db, etype=AUDIT)],
        "unclassified": [_event(db, etype="app.custom.thing")],
        "young": [_event(db, when=NOW - timedelta(days=1))],
    }
    return rows


def _remaining_types(db):
    from AINDY.db.models.system_event import SystemEvent

    return sorted(str(t) for (t,) in db.query(SystemEvent.type).all())


def test_report_mode_counts_what_prune_would_delete_and_deletes_nothing(db_session):
    rows = _seed_mixed(db_session)
    before = _remaining_types(db_session)
    report = prune_system_events(mode=MODE_REPORT, db=db_session, now=NOW)
    assert report["deleted"] == 0 and report["batches"] == 0
    assert report["types"][KEEPALIVE]["eligible"] == 3
    assert report["types"][OPERATIONAL]["eligible"] == 2
    assert AUDIT not in report["types"]                 # never by age
    assert "app.custom.thing" not in report["types"]     # unclassified = keep
    assert report["unclassified_rows"] == 1
    assert _remaining_types(db_session) == before
    assert all(r.id in _ids(db_session) for r in rows["keep"])


def test_prune_mode_deletes_exactly_the_reported_rows(db_session):
    _seed_mixed(db_session)
    reported = prune_system_events(mode=MODE_REPORT, db=db_session, now=NOW)
    pruned = prune_system_events(mode=MODE_PRUNE, db=db_session, now=NOW)
    assert pruned["deleted"] == 5 == sum(t["eligible"] for t in reported["types"].values())
    assert pruned["types"][KEEPALIVE]["deleted"] == 3
    assert pruned["types"][OPERATIONAL]["deleted"] == 2
    remaining = _remaining_types(db_session)
    assert remaining == sorted([AUDIT, "app.custom.thing", KEEPALIVE])  # young keepalive survives
    assert count_prunable(db_session, event_type=KEEPALIVE, cutoff=NOW - timedelta(days=7)) == 0


def test_prune_commits_between_batches(db_session):
    """★ Committed batches, not one transaction: 5 rows at batch 2 → 3 batches, 3 commits."""
    for _ in range(5):
        _event(db_session)
    commits = {"n": 0}
    real_commit = db_session.commit

    def _spy():
        commits["n"] += 1
        real_commit()

    db_session.commit = _spy  # type: ignore[assignment]
    try:
        report = prune_system_events(mode=MODE_PRUNE, batch_size=2, db=db_session, now=NOW)
    finally:
        db_session.commit = real_commit  # type: ignore[assignment]
    assert report["types"][KEEPALIVE]["batches"] == 3
    assert commits["n"] == 3
    assert report["deleted"] == 5


def test_age_ceilings_come_from_the_class_and_env_overrides(db_session, monkeypatch):
    """A 30-day-old keepalive (7 d ceiling) goes; a 30-day-old operational (90 d) stays —
    until the operational ceiling is lowered by env."""
    thirty = NOW - timedelta(days=30)
    _event(db_session, when=thirty)
    _event(db_session, etype=OPERATIONAL, when=thirty)
    report = prune_system_events(mode=MODE_REPORT, db=db_session, now=NOW)
    assert report["types"].get(KEEPALIVE, {}).get("eligible") == 1
    assert OPERATIONAL not in report["types"]
    monkeypatch.setenv(ret.ENV_OPERATIONAL_DAYS, "10")
    report = prune_system_events(mode=MODE_REPORT, db=db_session, now=NOW)
    assert report["types"][OPERATIONAL]["eligible"] == 1
    assert report["types"][OPERATIONAL]["older_than_days"] == 10


def test_audit_has_no_age_override(monkeypatch):
    monkeypatch.setenv(ret.ENV_OPERATIONAL_DAYS, "1")
    monkeypatch.setenv(ret.ENV_KEEPALIVE_DAYS, "1")
    ages = ret.max_age_days()
    assert ages[RETENTION_AUDIT] is None
    assert ages[RETENTION_OPERATIONAL] == 1 and ages[RETENTION_KEEPALIVE] == 1


# ---------------------------------------------------------------------------
# The operator signals
# ---------------------------------------------------------------------------

def test_counter_and_gauge_move(db_session):
    from AINDY.platform_layer.metrics import REGISTRY

    _seed_mixed(db_session)
    before = REGISTRY.get_sample_value("aindy_system_events_pruned_total", {"type": KEEPALIVE}) or 0.0
    prune_system_events(mode=MODE_PRUNE, db=db_session, now=NOW)
    after = REGISTRY.get_sample_value("aindy_system_events_pruned_total", {"type": KEEPALIVE})
    assert after == before + 3
    assert REGISTRY.get_sample_value("aindy_system_events_unclassified_rows") == 1.0


# ---------------------------------------------------------------------------
# Wiring — the scheduler registers the job only when a mode is chosen
# ---------------------------------------------------------------------------

def test_scheduler_registers_the_job_only_when_a_mode_is_set(monkeypatch):
    from AINDY.platform_layer import scheduler_service

    class _Sched:
        def __init__(self):
            self.ids = []

        def add_job(self, func, trigger=None, id=None, **kw):
            self.ids.append(id)

    for raw, expect in ((None, False), ("report", True), ("prune", True)):
        if raw is None:
            monkeypatch.delenv(ret.ENV_MODE, raising=False)
        else:
            monkeypatch.setenv(ret.ENV_MODE, raw)
        sched = _Sched()
        scheduler_service._register_system_jobs(sched)
        assert ("system_event_retention" in sched.ids) is expect, (raw, sched.ids)
