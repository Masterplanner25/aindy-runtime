"""flow_history.sequence_number: monotonic per-run node ordinal (DUR-4)

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-12

DUR-4 (Durable Execution) makes ``flow_history`` a deterministically ordered, fold-able
event log by adding a nullable monotonic ``sequence_number`` (per ``flow_run_id``) plus a
composite index ``ix_flow_history_run_seq``. Purely additive — nullable, no backfill; the
folder falls back to ``created_at`` then ``id`` for pre-DUR-4 rows.

Idempotent and blank-DB safe (ALEMBIC-FRESH-DB-1). On a blank database ``alembic upgrade
head`` runs before the ORM ``create_all`` schema guard, so ``flow_history`` may not exist
yet; the table-existence guard skips and Phase 5 bootstraps the full table (column + index)
from ORM metadata. On an existing deployment the block runs and ``IF NOT EXISTS`` no-ops if
already present. ``downgrade()`` drops the index then the column.
"""

from alembic import op


revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename='flow_history' AND schemaname='public'
          ) THEN
            ALTER TABLE flow_history ADD COLUMN IF NOT EXISTS sequence_number INTEGER;
            CREATE INDEX IF NOT EXISTS ix_flow_history_run_seq
                ON flow_history (flow_run_id, sequence_number);
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_flow_history_run_seq")
    op.execute("ALTER TABLE flow_history DROP COLUMN IF EXISTS sequence_number")
