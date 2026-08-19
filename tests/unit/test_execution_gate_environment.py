"""EXEC-ENV-BIND-1 at the seam — does ``require_execution_unit`` let the answer out?

★ **These tests exist because the resolver being correct proves nothing here.**
``require_execution_unit`` ends in a broad ``except Exception`` that returns ``None``, and its
three call sites are documented not to block on that. A refusal that falls into that handler is
swallowed, and the recorded row says ``refused`` while the work runs — worse than no refusal.

That is `ROUTE-GUARD-1`'s rule applied to a non-route seam: reading the guard proves it was
written; only calling the function proves the caller receives its answer.

The DB session is a mock, matching the house style for EU unit tests — ``ExecutionUnit`` uses
JSONB columns, which SQLite cannot create.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from AINDY.core.execution_environment import (
    ASSURANCE_INSECURE_DEV,
    ASSURANCE_STRONG,
    ExecutionEnvironmentInvalid,
    ExecutionEnvironmentUnsatisfiable,
)
from AINDY.core.execution_gate import require_execution_unit

pytestmark = pytest.mark.runtime_only


class _FakeService:
    """Stands in for ExecutionUnitService, recording what create() was asked for."""

    instances: list["_FakeService"] = []

    def __init__(self, db):
        self.db = db
        self.created: list[dict] = []
        _FakeService.instances.append(self)

    def get_by_source(self, *_a, **_k):
        return None

    def create(self, **kwargs):
        self.created.append(kwargs)
        return MagicMock(id="eu-1", extra=kwargs.get("extra"))

    def update_status(self, *_a, **_k):
        return True


@pytest.fixture(autouse=True)
def _fresh_service_registry(monkeypatch):
    _FakeService.instances = []
    monkeypatch.setattr("AINDY.core.execution_unit_service.ExecutionUnitService", _FakeService)
    yield


def _weak_host(monkeypatch):
    monkeypatch.setattr(
        "AINDY.core.execution_environment._host_assurance",
        lambda: (ASSURANCE_INSECURE_DEV, "insecure-dev/no-isolation-guarantee"),
    )


def _call(**kw):
    return require_execution_unit(
        db=MagicMock(),
        eu_type="flow",
        user_id="user-1",
        source_type="flow_run",
        source_id="src-1",
        **kw,
    )


def _all_created():
    return [c for svc in _FakeService.instances for c in svc.created]


# ── Liveness control — run before the two absence assertions below ────────────


def test_liveness_the_seam_can_refuse_at_all(monkeypatch):
    """★ If this fails, both 'is not refused' tests below are vacuous."""
    _weak_host(monkeypatch)
    with pytest.raises(ExecutionEnvironmentUnsatisfiable):
        _call(env_spec={"min_assurance": ASSURANCE_STRONG})


# ── The critical property: the refusal escapes the non-fatal handler ──────────


def test_an_unsatisfiable_spec_propagates_and_is_not_swallowed(monkeypatch):
    """★ The whole point. A returned ``None`` here would be a silent acceptance."""
    _weak_host(monkeypatch)

    with pytest.raises(ExecutionEnvironmentUnsatisfiable) as exc:
        _call(env_spec={"min_assurance": ASSURANCE_STRONG})

    assert exc.value.required == ASSURANCE_STRONG
    assert exc.value.available == ASSURANCE_INSECURE_DEV


def test_a_malformed_spec_propagates_too(monkeypatch):
    """A caller bug must not become 'no EU, no binding, carry on'."""
    _weak_host(monkeypatch)
    with pytest.raises(ExecutionEnvironmentInvalid):
        _call(env_spec={"authority": {"network": "wide-open"}})


def test_a_refusal_writes_a_terminal_refused_row_before_raising(monkeypatch):
    """★ Raise-only loses the audit row at the moment it is most interesting."""
    _weak_host(monkeypatch)

    with pytest.raises(ExecutionEnvironmentUnsatisfiable):
        _call(env_spec={"min_assurance": ASSURANCE_STRONG})

    refusals = [c for c in _all_created() if c.get("status") == "refused"]
    assert len(refusals) == 1
    row = refusals[0]
    assert row["env_spec"]["min_assurance"] == ASSURANCE_STRONG
    assert row["env_applied"] is None
    assert row["env_evidence_class"] == f"refused/{ASSURANCE_INSECURE_DEV}"
    assert row["extra"]["environment_refusal"]["required"] == ASSURANCE_STRONG


def test_a_failed_audit_write_still_lets_the_refusal_propagate(monkeypatch):
    """★ Losing the audit row is bad; converting a refusal into an acceptance because the audit
    write failed would be far worse."""
    _weak_host(monkeypatch)

    def _explode(**_kw):
        raise RuntimeError("db is on fire")

    monkeypatch.setattr(_FakeService, "create", lambda self, **kw: _explode(**kw))

    with pytest.raises(ExecutionEnvironmentUnsatisfiable):
        _call(env_spec={"min_assurance": ASSURANCE_STRONG})


# ── Absence assertions — only meaningful because the liveness control passes ──


def test_no_spec_means_no_env_columns_and_no_refusal(monkeypatch):
    """Every pre-existing caller takes this path; it must be byte-for-byte what it was."""
    _weak_host(monkeypatch)

    eu = _call()

    assert eu is not None
    created = _all_created()
    assert len(created) == 1
    assert created[0].get("status") == "executing"
    for column in ("env_spec", "env_applied", "env_evidence_class"):
        assert column not in created[0], f"{column} must not be passed when nothing was declared"


def test_a_satisfiable_spec_is_recorded_on_the_row(monkeypatch):
    monkeypatch.setattr(
        "AINDY.core.execution_environment._host_assurance",
        lambda: (ASSURANCE_STRONG, "strong-sandbox-tier/kernel-observable-verified"),
    )

    eu = _call(env_spec={"min_assurance": ASSURANCE_STRONG, "authority": {"subprocess": False}})

    assert eu is not None
    created = _all_created()
    assert len(created) == 1
    row = created[0]
    assert row["status"] == "executing"
    assert row["env_spec"]["authority"]["subprocess"] is False
    assert row["env_applied"]["authority"]["subprocess"] is False
    assert row["env_evidence_class"] == "strong-sandbox-tier/kernel-observable-verified"


def test_the_evidence_class_is_what_says_whether_anything_was_enforced(monkeypatch):
    """★ Phase 1 applies nothing. A populated env_applied is NOT evidence of confinement —
    env_evidence_class is, and on the default dev runner it says so plainly."""
    _weak_host(monkeypatch)

    _call(env_spec={"authority": {"subprocess": False}})

    row = _all_created()[0]
    assert row["env_applied"]["authority"]["subprocess"] is False
    assert row["env_evidence_class"] == "insecure-dev/no-isolation-guarantee"
