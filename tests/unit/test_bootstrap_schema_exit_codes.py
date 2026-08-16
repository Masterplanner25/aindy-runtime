"""FR-14 — `bootstrap-schema` must tell a deploy entrypoint *which* problem it hit.

Every not-ready state exited **1**, the same code as "DATABASE_URL is not set". A container
entrypoint running the command bare under `set -e` therefore could not distinguish:

  * re-run me with `--reconcile` and I will succeed,
  * your configuration is wrong,
  * a human must perform an offline migration.

So it exited, `restart: unless-stopped` restarted it, and the only way to learn which had
happened was to read the container log. That took a live stack down on 2.1.0 (`FR-13`'s
additive columns).

**The report always carried the distinction** — `reconcile_supported`,
`offline_migration_required` — only the exit surface collapsed it. These tests pin the codes as
a public contract, because an entrypoint branching on them breaks silently if they move.

★ `test_reconcile_required_is_reproduced_end_to_end` is the one that matters: it builds a real
database, drops a column, and runs the real command. The mocked cases below pin the mapping;
only that one proves the mapping is reachable from the condition the app team actually hit.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.runtime_only


def _codes():
    from AINDY import runtime_only

    return runtime_only


def _run_with_report(monkeypatch, report) -> int:
    """Drive the real `_bootstrap_schema` with a supplied schema report."""
    from AINDY import runtime_only

    monkeypatch.setattr("AINDY.db.schema_contract.ensure_runtime_schema", lambda *a, **k: report)
    monkeypatch.setattr(
        "AINDY.db.alembic_head.stamp_runtime_alembic_head", lambda *a, **k: "0016"
    )
    with pytest.raises(SystemExit) as exc:
        runtime_only._bootstrap_schema(reconcile=False)
    return int(exc.value.code)


def _report(**overrides):
    from AINDY.db.schema_contract import SchemaReport

    base = dict(
        ok=False,
        bootstrapped=False,
        reconciled=False,
        state="upgrade_required",
        reconcile_supported=False,
        operator_action="test",
        issues=(),
        drift_classes=(),
        remediation_categories=(),
        offline_migration_required=False,
        startup_reconcile_permitted=False,
    )
    base.update(overrides)
    return SchemaReport(**base)


# --------------------------------------------------------------------------------------
# The contract
# --------------------------------------------------------------------------------------


def test_exit_codes_are_the_documented_values():
    """These are published in `--help` and branched on by a deploy entrypoint.

    Renumbering silently breaks every entrypoint that reads them, which is why the numbers
    are asserted literally rather than compared to themselves.
    """
    rt = _codes()

    assert rt.EXIT_SCHEMA_RECONCILE_REQUIRED == 3
    assert rt.EXIT_SCHEMA_OFFLINE_MIGRATION_REQUIRED == 4
    assert rt.EXIT_SCHEMA_MANUAL_REPAIR_REQUIRED == 5


def test_additive_drift_exits_3(monkeypatch):
    """The state the app team hit: re-running with --reconcile resolves it."""
    code = _run_with_report(monkeypatch, _report(reconcile_supported=True))

    assert code == 3


def test_offline_migration_exits_4_even_when_reconcile_is_also_supported(monkeypatch):
    """Ordering matters: 4 must win.

    If a report says both, the honest answer is the one `--reconcile` cannot fix. Reporting 3
    here would invite an entrypoint to auto-reconcile a database that needs a person — a
    worse outcome than the crash loop this change removes.
    """
    code = _run_with_report(
        monkeypatch, _report(reconcile_supported=True, offline_migration_required=True)
    )

    assert code == 4


def test_neither_reconcilable_nor_migratable_exits_5(monkeypatch):
    code = _run_with_report(monkeypatch, _report())

    assert code == 5


def test_success_still_exits_0(monkeypatch):
    code = _run_with_report(
        monkeypatch, _report(ok=True, state="compatible", bootstrapped=True)
    )

    assert code == 0


def test_missing_database_url_still_exits_1(monkeypatch):
    """Unchanged, and load-bearing: 1 must keep meaning 'fix your environment'.

    The whole value of 3/4/5 is that they are *not* 1. If a config error started sharing a
    schema code, an entrypoint would retry a broken environment forever.
    """
    from AINDY import runtime_only
    from AINDY.config import settings

    # Patched on the INSTANCE — `DATABASE_URL` is a pydantic field, so a class-level patch is
    # shadowed by the instance value and the test silently passes against a live URL. That is
    # the inverse of the `settings.is_testing` gotcha, which is a *property* and must be
    # patched on the class. Same settings object, opposite technique.
    monkeypatch.setattr(settings, "DATABASE_URL", "", raising=False)
    with pytest.raises(SystemExit) as exc:
        runtime_only._bootstrap_schema(reconcile=False)

    assert int(exc.value.code) == 1


def test_the_failure_message_names_the_code_and_the_remedy(monkeypatch, capsys):
    """A log reader must not have to know the table to act on it."""
    _run_with_report(monkeypatch, _report(reconcile_supported=True))
    err = capsys.readouterr().err

    assert "--reconcile" in err
    assert "exit_code=3" in err
    assert "state=" in err


def test_offline_migration_message_says_reconcile_will_not_help(monkeypatch, capsys):
    """The dangerous misreading is 'schema problem -> try --reconcile'. Say it explicitly."""
    _run_with_report(monkeypatch, _report(offline_migration_required=True))
    err = capsys.readouterr().err

    assert "will NOT fix this" in err or "NOT help" in err


# --------------------------------------------------------------------------------------
# The reproduction
# --------------------------------------------------------------------------------------


def test_reconcile_required_is_reproduced_end_to_end(monkeypatch, tmp_path):
    """★ Build a real schema, remove a column, and run the real command.

    Everything above mocks the report, which proves the *mapping* and nothing about whether
    exit 3 is reachable from the condition that actually took a stack down. This drives
    `ensure_runtime_schema` against a real database whose `agents` table is missing a column
    — the shape of `FR-13`'s additive change — and asserts the command exits 3.
    """
    import sqlalchemy as sa

    from AINDY.db.schema_contract import ensure_runtime_schema

    db_path = tmp_path / "fr14.db"
    engine = sa.create_engine(f"sqlite:///{db_path}")

    # 1. Build the runtime-owned surface as a prior release would have left it.
    built = ensure_runtime_schema(engine, allow_bootstrap=True, allow_reconcile=True)
    if not built.ok:
        pytest.skip(f"runtime schema will not build on sqlite here: {built.summary()}")

    # 2. Simulate the database predating an additive column.
    with engine.begin() as conn:
        cols = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(agents)").fetchall()]
        if "updated_at" not in cols:
            pytest.skip("agents.updated_at absent from this build; nothing to drop")
        conn.exec_driver_sql("ALTER TABLE agents DROP COLUMN updated_at")

    # 3. The state a deploy would now be in.
    drifted = ensure_runtime_schema(engine, allow_bootstrap=False, allow_reconcile=False)

    assert not drifted.ok, "dropping a required column did not register as drift"
    assert drifted.reconcile_supported, (
        "a missing additive column must be reconcile-supported, or exit 3 is unreachable "
        "from the very condition FR-14 was filed about"
    )

    # 4. The command must report it as branchable, not as a bare failure.
    monkeypatch.setattr(
        "AINDY.db.schema_contract.ensure_runtime_schema", lambda *a, **k: drifted
    )
    monkeypatch.setattr(
        "AINDY.db.alembic_head.stamp_runtime_alembic_head", lambda *a, **k: "0016"
    )
    from AINDY import runtime_only

    with pytest.raises(SystemExit) as exc:
        runtime_only._bootstrap_schema(reconcile=False)

    assert int(exc.value.code) == 3
