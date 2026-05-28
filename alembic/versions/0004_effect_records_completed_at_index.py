"""effect_records: completed_at composite partial index (IDEM-9)

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-24

Adds a composite partial index on (completed_at, status) WHERE completed_at IS NOT NULL.

This index supports the IDEM-9 TTL cleanup job's batch DELETE queries, which filter on
completed_at < cutoff AND status != 'pending' AND completed_at IS NOT NULL.  Without
this index those queries perform a full table scan at production volume.

Safe to run against schemas bootstrapped via create_all; the IF NOT EXISTS guard makes
this migration idempotent.

Fresh-database safety: on a blank Docker deployment, `alembic upgrade head` runs
before the server's Phase 5 schema bootstrap (create_all). If effect_records has not
been created yet (because 0003 skipped on a blank database), this migration skips
silently. The server's create_all then creates effect_records with all three
ORM-declared indexes — including ix_effect_records_completed_at_status — correctly.
On existing deployments where effect_records already exists, the index is created
normally (IF NOT EXISTS makes it idempotent against schemas where create_all already
applied the index from __table_args__).
"""

from alembic import op


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Gate on effect_records existing. On a blank database (fresh Docker
    # deployment), effect_records may not exist yet when this migration runs
    # (because 0003 skipped). The server's create_all creates it at startup.
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename='effect_records' AND schemaname='public'
          ) THEN
            CREATE INDEX IF NOT EXISTS ix_effect_records_completed_at_status
            ON effect_records (completed_at, status)
            WHERE completed_at IS NOT NULL;
          END IF;
        END $$
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_effect_records_completed_at_status")
