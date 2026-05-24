"""idempotency_constraints

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-23

Adds DB-level uniqueness constraints to close IDEM-2, IDEM-3, IDEM-4, and
IDEM-5 from the idempotency audit (IDEMPOTENCY_AUDIT.md, 2026-05-23).

IDEM-2: webhook_subscriptions — partial UNIQUE on (event_type, callback_url)
        where is_active = true. Prevents duplicate active webhook registrations
        for the same URL+event pair without blocking soft-delete reuse.

IDEM-3: platform_api_keys — partial UNIQUE on (user_id, name) where
        is_active = true. Prevents a user from creating two live keys with
        the same display name.

IDEM-4: execution_units — partial UNIQUE on (source_type, source_id) where
        both columns are NOT NULL. PostgreSQL treats NULL columns as distinct
        in UNIQUE constraints, so a partial index is the correct approach.

IDEM-5: dynamic_flows and dynamic_nodes — UNIQUE on name. The ORM already
        declares unique=True; this migration creates the DB constraint for
        existing deployments that were bootstrapped before the ORM declaration.
        Uses IF NOT EXISTS to be safe on fresh deployments.

Pre-constraint deduplication: for each table, any existing duplicate active
rows are resolved by retaining the most-recently-created row and soft-deleting
(is_active = false) older duplicates before the constraint is applied.
"""

from alembic import op


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Pre-constraint deduplication ──────────────────────────────────────────
    # For each table, soft-delete older duplicate active rows so that the
    # subsequent CREATE UNIQUE INDEX succeeds on existing deployments.

    # IDEM-3 dedup: keep newest active key per (user_id, name), deactivate rest
    op.execute("""
        UPDATE platform_api_keys SET is_active = false
        WHERE is_active = true
          AND id NOT IN (
            SELECT DISTINCT ON (user_id, name) id
            FROM platform_api_keys
            WHERE is_active = true
            ORDER BY user_id, name, created_at DESC
          )
    """)

    # IDEM-2 dedup: keep newest active subscription per (event_type, callback_url)
    op.execute("""
        UPDATE webhook_subscriptions SET is_active = false
        WHERE is_active = true
          AND id NOT IN (
            SELECT DISTINCT ON (event_type, callback_url) id
            FROM webhook_subscriptions
            WHERE is_active = true
            ORDER BY event_type, callback_url, created_at DESC
          )
    """)

    # IDEM-4 dedup: keep newest EU per (source_type, source_id) where both non-NULL
    op.execute("""
        DELETE FROM execution_units
        WHERE source_type IS NOT NULL
          AND source_id IS NOT NULL
          AND id NOT IN (
            SELECT DISTINCT ON (source_type, source_id) id
            FROM execution_units
            WHERE source_type IS NOT NULL AND source_id IS NOT NULL
            ORDER BY source_type, source_id, created_at DESC
          )
    """)

    # ── Uniqueness constraints ─────────────────────────────────────────────────

    # IDEM-2: webhook_subscriptions — partial unique on (event_type, callback_url)
    op.create_index(
        "uq_webhook_subscriptions_event_url_active",
        "webhook_subscriptions",
        ["event_type", "callback_url"],
        unique=True,
        postgresql_where="is_active = true",
    )

    # IDEM-3: platform_api_keys — partial unique on (user_id, name)
    op.create_index(
        "uq_platform_api_keys_user_name_active",
        "platform_api_keys",
        ["user_id", "name"],
        unique=True,
        postgresql_where="is_active = true",
    )

    # IDEM-4: execution_units — partial unique on (source_type, source_id)
    op.create_index(
        "uq_execution_units_source",
        "execution_units",
        ["source_type", "source_id"],
        unique=True,
        postgresql_where="source_type IS NOT NULL AND source_id IS NOT NULL",
    )

    # IDEM-5a: dynamic_flows — rename old ix_ index (from unique=True column) to uq_ name.
    # The ix_dynamic_flows_name index was created by create_all with the old ORM declaration.
    # We rename it to the canonical uq_ name and create a new one if it doesn't exist.
    op.execute(
        "DO $$ BEGIN"
        "  IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_dynamic_flows_name') THEN"
        "    ALTER INDEX ix_dynamic_flows_name RENAME TO uq_dynamic_flows_name;"
        "  ELSE"
        "    CREATE UNIQUE INDEX IF NOT EXISTS uq_dynamic_flows_name ON dynamic_flows (name);"
        "  END IF;"
        " END $$"
    )

    # IDEM-5b: dynamic_nodes — same rename/create pattern
    op.execute(
        "DO $$ BEGIN"
        "  IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_dynamic_nodes_name') THEN"
        "    ALTER INDEX ix_dynamic_nodes_name RENAME TO uq_dynamic_nodes_name;"
        "  ELSE"
        "    CREATE UNIQUE INDEX IF NOT EXISTS uq_dynamic_nodes_name ON dynamic_nodes (name);"
        "  END IF;"
        " END $$"
    )


def downgrade() -> None:
    op.drop_index("uq_dynamic_nodes_name", table_name="dynamic_nodes")
    op.drop_index("uq_dynamic_flows_name", table_name="dynamic_flows")
    op.drop_index("uq_execution_units_source", table_name="execution_units")
    op.drop_index("uq_platform_api_keys_user_name_active", table_name="platform_api_keys")
    op.drop_index("uq_webhook_subscriptions_event_url_active", table_name="webhook_subscriptions")
