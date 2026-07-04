"""agent_runs: add wait_state JSONB column (RTR-1 Phase 2e cross-restart durability)

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-04

Adds a nullable ``wait_state`` JSONB column to ``agent_runs``. It holds the
durable descriptor of a mid-plan WAIT — ``{"event_type", "correlation_key",
"resume_segment_index"}`` — so a waiting agent run (status="waiting") can be
rehydrated and resumed after a process restart. NULL when the run is not parked.

Fresh-database safety (ALEMBIC-FRESH-DB-1): on a blank Docker deployment
``agent_runs`` may not exist yet when this migration runs (alembic runs before
create_all). The table-existence guard skips silently; the server's create_all
then bootstraps ``agent_runs`` with ``wait_state`` from the ORM model. On an
existing deployment the ADD COLUMN IF NOT EXISTS runs normally and is idempotent.
"""

from alembic import op


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename = 'agent_runs' AND schemaname = 'public'
          ) THEN
            ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS wait_state JSONB;
          END IF;
        END $$
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename = 'agent_runs' AND schemaname = 'public'
          ) THEN
            ALTER TABLE agent_runs DROP COLUMN IF EXISTS wait_state;
          END IF;
        END $$
    """)
