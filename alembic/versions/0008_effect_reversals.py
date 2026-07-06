"""effect_reversals: append-only compensating-undo audit log (AGENT-HARDEN-3)

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-05

Adds the ``effect_reversals`` table — one row per attempt to reverse a completed
``EffectRecord`` during an "undo run" pass (status reversed / irreversible /
failed). Append-only audit trail of what was and could not be undone.

Fresh-database safety (ALEMBIC-FRESH-DB-1): on a blank Docker deployment the FK
parents (``effect_records``, ``execution_units``) may not exist yet when this
migration runs (alembic runs before create_all). The guard skips creation when
``effect_records`` is absent; the server's create_all then bootstraps
``effect_reversals`` (with its FKs) from the ORM model. On an existing deployment
the parents exist and the CREATE runs normally; ``IF NOT EXISTS`` keeps it
idempotent.
"""

from alembic import op


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename = 'effect_records' AND schemaname = 'public'
          ) THEN
            CREATE TABLE IF NOT EXISTS effect_reversals (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                effect_record_id UUID REFERENCES effect_records(id) ON DELETE SET NULL,
                run_id VARCHAR(72),
                execution_id UUID REFERENCES execution_units(id) ON DELETE SET NULL,
                action_type VARCHAR(256) NOT NULL,
                status VARCHAR(32) NOT NULL,
                detail TEXT,
                receipt JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS ix_effect_reversals_run_id
                ON effect_reversals (run_id);
            CREATE INDEX IF NOT EXISTS ix_effect_reversals_execution_id
                ON effect_reversals (execution_id);
            CREATE INDEX IF NOT EXISTS ix_effect_reversals_effect_record_id
                ON effect_reversals (effect_record_id);
          END IF;
        END $$
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS effect_reversals")
