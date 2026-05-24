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
"""

from alembic import op


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_effect_records_completed_at_status
        ON effect_records (completed_at, status)
        WHERE completed_at IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_effect_records_completed_at_status")
