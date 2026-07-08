"""drop nodus_trace_events: retire the dead per-function trace path (RTR-1 close)

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-07

Drops the ``nodus_trace_events`` table. The per-host-function trace path was
speced (Phase 3, "wire or drop") but never wired: ``_flush_nodus_traces()`` had
no call sites and the worker produced no trace records, so no row was ever
written. Execution-level observability is already covered by ``SystemEvent``
(``source="nodus"``) + the ``EventEdge`` causal graph (RTR-7), which the per-fn
table only duplicated at finer grain. RTR-1 chose "drop"; the model, reader
service, ``GET /platform/nodus/trace/{trace_id}`` route, and CLI surface are all
removed alongside this migration.

The table was only ever created by the ORM ``create_all`` schema guard (it never
had a create migration), so the drop is idempotent and blank-DB safe: ``DROP
TABLE IF EXISTS`` no-ops when absent. ``downgrade()`` recreates the table +
indexes from the retired model's schema so the revision is reversible.
"""

from alembic import op


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS nodus_trace_events")


def downgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS nodus_trace_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            execution_unit_id VARCHAR(128) NOT NULL,
            trace_id VARCHAR(128) NOT NULL,
            sequence INTEGER NOT NULL DEFAULT 0,
            fn_name VARCHAR(64) NOT NULL,
            args_summary JSON,
            result_summary JSON,
            duration_ms INTEGER,
            status VARCHAR(16) NOT NULL DEFAULT 'ok',
            error TEXT,
            user_id UUID,
            timestamp TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS ix_nodus_trace_events_execution_unit_id
            ON nodus_trace_events (execution_unit_id);
        CREATE INDEX IF NOT EXISTS ix_nodus_trace_events_trace_id
            ON nodus_trace_events (trace_id);
        CREATE INDEX IF NOT EXISTS ix_nodus_trace_events_user_id
            ON nodus_trace_events (user_id);
        CREATE INDEX IF NOT EXISTS ix_nodus_trace_events_timestamp
            ON nodus_trace_events (timestamp);
    """)
