"""runtime_baseline

Revision ID: 0001
Revises:
Create Date: 2026-05-23

Baseline migration for aindy-runtime Alembic history. All 32 runtime-owned
tables already exist in production via schema_contract bootstrap. This
migration is intentionally empty — stamping it records the current schema
as the known-good state and lets 0002 apply idempotency constraints cleanly.
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
