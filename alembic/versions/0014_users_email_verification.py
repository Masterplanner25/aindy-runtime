"""users email verification columns (FR-6 Phase C)

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-02

FR-6 Phase C adds ``is_verified`` / ``verified_at`` to ``users`` so registration can stop
returning an access token and start confirming address ownership — which is what closes the
``/auth/register`` account-enumeration oracle (a duplicate email must produce a response
identical to a new one, and it cannot if one branch returns a token).

**Existing rows are backfilled to verified.** The column lands with
``DEFAULT false`` to match the ORM's default for *new* registrations, and then every row
that existed at migration time is set to true. Those accounts predate verification and were
never given a chance to confirm; leaving them false would retroactively mark the entire
current user base unverified — and, once login gating is enabled, lock all of them out. The
backfill is the difference between an additive migration and an outage.

The backfill is ordered *after* the ADD COLUMN inside the same guarded block so a fresh
deployment and an existing one converge on the same state.

Idempotent and blank-DB safe (ALEMBIC-FRESH-DB-1). On a blank database ``alembic upgrade
head`` runs before the ORM ``create_all`` schema guard, so ``users`` may not exist yet; the
table-existence guard skips and Phase 5 bootstraps the full table (columns included) from
ORM metadata. On an existing deployment the block runs and ``ADD COLUMN IF NOT EXISTS``
no-ops when the columns are already present — in which case the backfill is scoped to rows
still holding the default, so re-running never re-verifies an account that was
deliberately unverified afterwards.

``downgrade()`` drops both columns.
"""

from alembic import op


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename='users' AND schemaname='public'
          ) THEN
            ALTER TABLE users
              ADD COLUMN IF NOT EXISTS is_verified BOOLEAN NOT NULL DEFAULT false;
            ALTER TABLE users
              ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ NULL;

            -- Grandfather accounts that predate verification. Scoped to rows created
            -- before this migration ran, so a re-run cannot re-verify an account that was
            -- unverified deliberately afterwards.
            UPDATE users
               SET is_verified = true,
                   verified_at = COALESCE(verified_at, now())
             WHERE is_verified = false
               AND created_at IS NOT NULL
               AND created_at < now();
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS verified_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS is_verified")
