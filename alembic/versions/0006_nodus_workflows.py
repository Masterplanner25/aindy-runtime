"""nodus_workflows: registered Nodus workflow source store (RTR-1)

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-29

Adds the nodus_workflows table backing register_nodus_workflow — stores the .nd
SOURCE (durable, versioned artifact) for every registered Nodus workflow so
registrations survive a restart. See docs/runtime/NODUS_WORKFLOW_CONTRACT.md.

Fresh-database safety: on a blank Docker deployment, alembic runs before
create_all. CREATE TABLE IF NOT EXISTS is idempotent; on an existing deployment
the table is created, on a fresh one the server's create_all bootstraps the same
table from ORM metadata. Additive-only; no data migration.
"""

from alembic import op


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS nodus_workflows (
            id UUID PRIMARY KEY,
            name VARCHAR(256) NOT NULL,
            source TEXT NOT NULL,
            kind VARCHAR(16) NOT NULL DEFAULT 'flow-graph',
            version VARCHAR(128),
            content_hash VARCHAR(64) NOT NULL,
            capabilities JSON,
            owner_class VARCHAR(64) NOT NULL DEFAULT 'external-third-party',
            provenance JSON,
            created_by VARCHAR(256),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            is_active BOOLEAN NOT NULL DEFAULT true
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_nodus_workflows_name "
        "ON nodus_workflows (name)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_nodus_workflows_content_hash "
        "ON nodus_workflows (content_hash)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_nodus_workflows_content_hash")
    op.execute("DROP INDEX IF EXISTS uq_nodus_workflows_name")
    op.execute("DROP TABLE IF EXISTS nodus_workflows")
