"""nodus_scheduled_jobs misfire_policy column (ECOGAP-5a)

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-12

ECOGAP-5a adds a per-job ``misfire_policy`` column to ``nodus_scheduled_jobs`` so a
schedule can opt into a one-shot catch-up run for a fire that was due while the process
was down (``run_once``) instead of silently skipping it (``skip``, the prior/default
behavior). Purely additive with a server default, so existing rows read ``skip``.

Idempotent and blank-DB safe (ALEMBIC-FRESH-DB-1). On a blank database ``alembic upgrade
head`` runs before the ORM ``create_all`` schema guard, so ``nodus_scheduled_jobs`` may not
exist yet; the table-existence guard skips and Phase 5 bootstraps the full table (column
included) from ORM metadata. On an existing deployment the block runs and ``ADD COLUMN IF
NOT EXISTS`` no-ops if the column is already present. ``downgrade()`` drops the column.
"""

from alembic import op


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename='nodus_scheduled_jobs' AND schemaname='public'
          ) THEN
            ALTER TABLE nodus_scheduled_jobs
              ADD COLUMN IF NOT EXISTS misfire_policy VARCHAR(16) NOT NULL DEFAULT 'skip';
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE nodus_scheduled_jobs DROP COLUMN IF EXISTS misfire_policy")
