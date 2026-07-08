"""agent_runs.flow_run_id index: speed the AgentRun↔FlowRun reconciliation join (RTR-3)

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-08

RTR-3 (AgentRun↔FlowRun status canonicalization) hardens the stuck-run recovery
surface. Every reconciliation join drives from the FlowRun side and looks the
linked AgentRun up by ``AgentRun.flow_run_id`` (see
``stuck_run_service._recover_agent_run``), which was an unindexed column scan.
This migration adds ``ix_agent_runs_flow_run_id``.

Idempotent and blank-DB safe: the ``CREATE INDEX`` is wrapped in a table-existence
guard (ALEMBIC-FRESH-DB-1). On a blank database ``alembic upgrade head`` runs
before the ORM ``create_all`` schema guard, so ``agent_runs`` may not exist yet;
the guard skips and Phase 5 bootstraps the index from ORM metadata. On an existing
deployment the block runs and ``IF NOT EXISTS`` no-ops if the index is already
present. ``downgrade()`` drops the index.
"""

from alembic import op


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename='agent_runs' AND schemaname='public'
          ) THEN
            CREATE INDEX IF NOT EXISTS ix_agent_runs_flow_run_id
                ON agent_runs (flow_run_id);
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_agent_runs_flow_run_id")
