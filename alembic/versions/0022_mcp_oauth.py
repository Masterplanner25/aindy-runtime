"""mcp_oauth_clients + mcp_oauth_grants: OAuth for the MCP endpoint (FR-66 phase 2, DEC-129)

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-09

Two tables for the MCP server's own authorization server: dynamic client registrations, and one
grant per user consent (the pending code and the refresh token stored only as SHA-256 hashes).
Nothing reads them until ``AINDY_MCP_OAUTH`` is on. Design:
``docs/design/MCP_REMOTE_TRANSPORT_DESIGN.md`` §6.

Fresh-database safety (ALEMBIC-FRESH-DB-1): ``mcp_oauth_grants`` references ``users``, which may
not exist yet on a blank deployment (alembic runs before create_all). The guard skips creation
then, and create_all builds both tables from the ORM models. ``IF NOT EXISTS`` keeps it idempotent.
"""

from alembic import op


revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename = 'users' AND schemaname = 'public'
          ) THEN
            CREATE TABLE IF NOT EXISTS mcp_oauth_clients (
                client_id VARCHAR(64) PRIMARY KEY,
                client_info TEXT NOT NULL,
                client_secret_ciphertext TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_used_at TIMESTAMPTZ
            );
            CREATE TABLE IF NOT EXISTS mcp_oauth_grants (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                client_id VARCHAR(64) NOT NULL
                    REFERENCES mcp_oauth_clients(client_id) ON DELETE CASCADE,
                scopes TEXT NOT NULL DEFAULT '',
                resource TEXT,
                token_version INTEGER NOT NULL DEFAULT 0,
                code_hash VARCHAR(64) UNIQUE,
                code_challenge VARCHAR(128),
                code_redirect_uri TEXT,
                code_redirect_explicit BOOLEAN NOT NULL DEFAULT TRUE,
                code_expires_at TIMESTAMPTZ,
                refresh_token_hash VARCHAR(64) UNIQUE,
                previous_refresh_token_hash VARCHAR(64),
                refresh_expires_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_used_at TIMESTAMPTZ,
                revoked_at TIMESTAMPTZ
            );
            CREATE INDEX IF NOT EXISTS ix_mcp_oauth_grants_user_id
                ON mcp_oauth_grants (user_id);
            CREATE INDEX IF NOT EXISTS ix_mcp_oauth_grants_client_id
                ON mcp_oauth_grants (client_id);
            CREATE INDEX IF NOT EXISTS ix_mcp_oauth_grants_previous_refresh_token_hash
                ON mcp_oauth_grants (previous_refresh_token_hash);
          END IF;
        END $$
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mcp_oauth_grants")
    op.execute("DROP TABLE IF EXISTS mcp_oauth_clients")
