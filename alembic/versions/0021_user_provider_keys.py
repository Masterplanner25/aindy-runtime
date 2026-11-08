"""user_provider_keys: a user's own provider keys, encrypted at rest (FR-59, DEC-121)

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-08

Adds ``user_provider_keys``, one row per (user, provider), holding AES-GCM ciphertext and the
key's last four characters. Nothing reads it until ``AINDY_PROVIDER_KEYS=per_user``. Design:
``docs/design/PROVIDER_KEYS_PER_USER_DESIGN.md``.

Fresh-database safety (ALEMBIC-FRESH-DB-1): on a blank deployment ``users`` may not exist yet
when this runs (alembic runs before create_all). The guard skips creation then, and create_all
builds the table from the ORM model. ``IF NOT EXISTS`` keeps it idempotent.
"""

from alembic import op


revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename = 'users' AND schemaname = 'public'
          ) THEN
            CREATE TABLE IF NOT EXISTS user_provider_keys (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                provider VARCHAR(32) NOT NULL,
                ciphertext TEXT NOT NULL,
                key_hint VARCHAR(8) NOT NULL,
                key_version VARCHAR(16) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_user_provider_keys_user_provider UNIQUE (user_id, provider)
            );
            CREATE INDEX IF NOT EXISTS ix_user_provider_keys_user_id
                ON user_provider_keys (user_id);
          END IF;
        END $$
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_provider_keys")
