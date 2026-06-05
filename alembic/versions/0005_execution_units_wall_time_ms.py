"""execution_units: rename cpu_time_ms column to wall_time_ms (AGENT-RESLIMIT-001)

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-05

Renames cpu_time_ms -> wall_time_ms in the execution_units table to match
what the field actually measures: wall-clock elapsed time (monotonic clock),
not CPU time. The field includes I/O wait such as embedding API calls.

Fresh-database safety: on a blank Docker deployment, execution_units may not
exist yet when this migration runs (alembic runs before create_all). The
table-existence guard skips silently in that case; the server's create_all
creates execution_units with the correct wall_time_ms column name.

On existing deployments the RENAME executes normally.
"""

from alembic import op


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename='execution_units' AND schemaname='public'
          ) THEN
            ALTER TABLE execution_units
              RENAME COLUMN cpu_time_ms TO wall_time_ms;
          END IF;
        END $$
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename='execution_units' AND schemaname='public'
          ) THEN
            ALTER TABLE execution_units
              RENAME COLUMN wall_time_ms TO cpu_time_ms;
          END IF;
        END $$
    """)
