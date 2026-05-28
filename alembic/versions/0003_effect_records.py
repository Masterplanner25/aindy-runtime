"""effect_records

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-24

Creates the effect_records table, closing NF-1 from IDEMPOTENCY_AUDIT.md
§"Open Findings — Effect-Level Idempotency Layer".

NF-1: The runtime previously had no persistent record keyed to a specific
logical tool invocation (action_id).  Without such a record, a retry after a
lost response cannot distinguish "the effect already succeeded" from "the
effect has not yet run."

effect_records provides that primitive:
  - action_id (TEXT, UNIQUE) — content-addressed key produced by
    compute_action_id(action_type, input_payload, scope) in execution_gate.py
  - status ('pending' | 'success' | 'failed') — lifecycle marker
  - result_payload / external_receipt (JSONB) — cached result for replay
  - FK to execution_units(id) — links the effect to its execution context

All index creation steps use IF NOT EXISTS so this migration is safe to run
against schemas bootstrapped via create_all (which already applies __table_args__
indexes declared on the EffectRecord ORM model).

Fresh-database safety: on a blank Docker deployment, `alembic upgrade head` runs
before the server's Phase 5 schema bootstrap (create_all). At that point neither
execution_units nor effect_records exists yet, so the CREATE TABLE (which has a FK
to execution_units) would fail. The entire block is wrapped in a table-existence
guard on execution_units. If execution_units is absent, this migration stamps and
exits cleanly; the server's create_all then creates both tables — with all three
ORM-declared indexes — correctly. On existing deployments where execution_units
already exists, the full CREATE TABLE and index creation run normally.

downgrade() drops the table entirely with CASCADE.
"""

from alembic import op


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Gate on execution_units existing. On a blank database (fresh Docker
    # deployment) execution_units has not been created yet and the FK
    # reference would raise UndefinedTable. The server's create_all handles
    # table creation with dependency ordering when it runs at startup.
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename='execution_units' AND schemaname='public'
          ) THEN
            CREATE TABLE IF NOT EXISTS effect_records (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                action_id TEXT NOT NULL,
                action_type VARCHAR(256) NOT NULL,
                input_hash VARCHAR(64) NOT NULL,
                execution_id UUID REFERENCES execution_units(id) ON DELETE SET NULL,
                step_id VARCHAR(256),
                status VARCHAR(32) NOT NULL DEFAULT 'pending',
                result_payload JSONB,
                external_receipt JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                completed_at TIMESTAMPTZ
            );
          END IF;
        END $$
    """)

    op.execute("""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename='effect_records' AND schemaname='public'
          ) THEN
            CREATE UNIQUE INDEX IF NOT EXISTS uq_effect_records_action_id
            ON effect_records (action_id);
          END IF;
        END $$
    """)

    op.execute("""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename='effect_records' AND schemaname='public'
          ) THEN
            CREATE INDEX IF NOT EXISTS ix_effect_records_execution_id
            ON effect_records (execution_id);
          END IF;
        END $$
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS effect_records CASCADE")
