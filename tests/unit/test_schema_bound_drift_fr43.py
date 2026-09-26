"""FR-43 — the schema contract compares a type's BOUND, not only its name.

`_normalize_type_name` keeps `compiled.split("(")[0]`, so `VARCHAR(32)` and `VARCHAR(128)` were
the same type to the drift check. On the app's stack, 2.22.0's widening of
`system_events.source` (Alembic 0020) was therefore reported as "no table changes", and
`bootstrap-schema` exited 0 and stamped `0020` over a `varchar(32)`. The revision that should
have widened the column was recorded as applied.

These pin the pure comparison and the classification that decides the exit code, with no
database. The end-to-end reproduction (a real PostgreSQL column, the real command, the stamp)
is `tests/integration/test_bootstrap_schema_widen_fr43.py`. SQLite does not enforce a declared
length, so the bound check is skipped there, and only PostgreSQL can reproduce the defect.
"""
from __future__ import annotations

import pytest
from sqlalchemy import Enum, Float, Numeric, String, Text
from sqlalchemy.dialects import postgresql as pg

pytestmark = pytest.mark.runtime_only


def _change(expected, actual):
    from AINDY.db.schema_contract import _bound_change

    return _bound_change(expected, actual)


class TestTheBoundComparison:
    def test_the_fr41_widening_is_seen(self):
        """★ The app's case: model String(128), column reflected as VARCHAR(32)."""
        assert _change(String(128), pg.VARCHAR(length=32)) == "widen"

    def test_a_narrowing_is_not_a_widening(self):
        assert _change(String(32), pg.VARCHAR(length=128)) == "incompatible"

    def test_equal_bounds_are_no_drift(self):
        assert _change(String(64), pg.VARCHAR(length=64)) is None

    def test_unbounded_model_over_a_bounded_column_widens(self):
        assert _change(String(), pg.VARCHAR(length=32)) == "widen"

    def test_bounded_model_over_an_unbounded_column_is_incompatible(self):
        assert _change(String(32), pg.VARCHAR()) == "incompatible"

    def test_text_carries_no_bound(self):
        assert _change(Text(), pg.TEXT()) is None

    def test_numeric_precision_up_at_the_same_scale_widens(self):
        assert _change(Numeric(12, 2), pg.NUMERIC(precision=10, scale=2)) == "widen"

    def test_numeric_scale_change_is_incompatible(self):
        """A scale change rewrites every value; it is an offline migration's decision."""
        assert _change(Numeric(12, 4), pg.NUMERIC(precision=10, scale=2)) == "incompatible"

    def test_float_is_not_compared(self):
        """PostgreSQL reflects DOUBLE PRECISION with a precision the model never declared."""
        assert _change(Float(), pg.DOUBLE_PRECISION(precision=53)) is None

    def test_enum_is_not_compared(self):
        """Enum subclasses String with a computed length; a native PG enum reflects without one."""
        assert _change(Enum("a", "bb", name="e"), pg.ENUM("a", "bb", name="e")) is None


class TestTheClassification:
    """The exit code is decided by `_classify_schema_state`; a widening must reach exit 3."""

    @staticmethod
    def _issue(code, *, reconcile_supported):
        from AINDY.db.schema_contract import (
            DRIFT_CLASS_ADDITIVE_COLUMN_WIDEN,
            DRIFT_CLASS_TYPE_MISMATCH,
            REMEDIATION_OFFLINE_MIGRATION,
            REMEDIATION_STARTUP_RECONCILE,
            SchemaIssue,
        )

        widen = code == "column_widen"
        return SchemaIssue(
            code=code, detail="d", table="system_events", column="source",
            reconcile_supported=reconcile_supported,
            drift_class=DRIFT_CLASS_ADDITIVE_COLUMN_WIDEN if widen else DRIFT_CLASS_TYPE_MISMATCH,
            remediation_category=REMEDIATION_STARTUP_RECONCILE if widen else REMEDIATION_OFFLINE_MIGRATION,
        )

    def test_a_widening_alone_is_upgrade_required_and_reconcilable(self):
        from AINDY.db.schema_contract import SCHEMA_STATE_UPGRADE_REQUIRED, _classify_schema_state

        state, reconcile_supported, *_rest, offline = _classify_schema_state(
            ("system_events",), (self._issue("column_widen", reconcile_supported=True),))
        assert state == SCHEMA_STATE_UPGRADE_REQUIRED
        assert reconcile_supported is True and offline is False

    def test_a_narrowing_beside_it_forces_the_offline_path(self):
        from AINDY.db.schema_contract import SCHEMA_STATE_INCOMPATIBLE_MANUAL, _classify_schema_state

        state, reconcile_supported, *_rest, offline = _classify_schema_state(
            ("system_events",),
            (self._issue("column_widen", reconcile_supported=True),
             self._issue("column_type_mismatch", reconcile_supported=False)),
        )
        assert state == SCHEMA_STATE_INCOMPATIBLE_MANUAL
        assert reconcile_supported is False and offline is True

    def test_the_published_contract_names_the_new_class(self):
        from AINDY.db.schema_contract import (
            DRIFT_CLASS_ADDITIVE_COLUMN_WIDEN,
            offline_migration_contract,
            runtime_schema_contract_metadata,
        )

        auto = runtime_schema_contract_metadata()["automatic_actions"]
        assert DRIFT_CLASS_ADDITIVE_COLUMN_WIDEN in auto["explicit_startup_reconcile_only"]
        assert DRIFT_CLASS_ADDITIVE_COLUMN_WIDEN not in auto["never_automatic"]
        assert DRIFT_CLASS_ADDITIVE_COLUMN_WIDEN in offline_migration_contract()["startup_reconcile_scope"]


class TestTheCommandsOutput:
    """The app's log said `(no table changes)` on the run that then stamped. That line is now
    printed only for a report that is ok."""

    @staticmethod
    def _run(monkeypatch, report):
        from AINDY import runtime_only

        stamped: list = []
        monkeypatch.setattr("AINDY.db.schema_contract.ensure_runtime_schema", lambda *a, **k: report)
        monkeypatch.setattr("AINDY.db.alembic_head.read_runtime_alembic_revision", lambda *a, **k: "0019")
        monkeypatch.setattr(
            "AINDY.db.alembic_head.stamp_runtime_alembic_head",
            lambda *a, **k: stamped.append("0020") or "0020",
        )
        with pytest.raises(SystemExit) as exc:
            runtime_only._bootstrap_schema(reconcile=False)
        return int(exc.value.code), stamped

    @staticmethod
    def _report(**overrides):
        from AINDY.db.schema_contract import SchemaReport

        base = dict(ok=False, bootstrapped=False, reconciled=False, state="upgrade_required",
                    reconcile_supported=True, operator_action="startup_reconcile", issues=(),
                    drift_classes=("additive_column_widen",), remediation_categories=(),
                    offline_migration_required=False, startup_reconcile_permitted=True)
        base.update(overrides)
        return SchemaReport(**base)

    def test_a_not_ok_report_neither_claims_no_changes_nor_stamps(self, monkeypatch, capsys):
        code, stamped = self._run(monkeypatch, self._report())
        out = capsys.readouterr()
        assert code == 3
        assert stamped == [], "a head was stamped over a schema that does not match it"
        assert "no table changes" not in out.out
        assert "widenings" in out.err

    def test_an_ok_report_stamps_and_names_the_revision_it_moved_from(self, monkeypatch, capsys):
        code, stamped = self._run(monkeypatch, self._report(
            ok=True, state="compatible", reconcile_supported=False, drift_classes=()))
        out = capsys.readouterr().out
        assert code == 0 and stamped == ["0020"]
        assert "no table changes" in out
        assert "from revision 0019 to 0020" in out
