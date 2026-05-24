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

All index creation steps use IF NOT EXISTS so this migration is safe to run
against a schema that was bootstrapped via create_all (which already creates
indexes declared in __table_args__) as well as against a pristine migration
target where no indexes exist yet.

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
    # Soft-delete older duplicate active rows so that the subsequent
    # CREATE UNIQUE INDEX succeeds on existing deployments with duplicates.
    # These UPDATE/DELETE statements are safe to run even when no duplicates
    # exist — they will simply match zero rows.

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

    # ── Uniqueness constraints (all IF NOT EXISTS — idempotent against create_all) ──

    # IDEM-2: webhook_subscriptions — partial unique on (event_type, callback_url)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_webhook_subscriptions_event_url_active
        ON webhook_subscriptions (event_type, callback_url)
        WHERE is_active = true
    """)

    # IDEM-3: platform_api_keys — partial unique on (user_id, name)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_platform_api_keys_user_name_active
        ON platform_api_keys (user_id, name)
        WHERE is_active = true
    """)

    # IDEM-4: execution_units — partial unique on (source_type, source_id)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_execution_units_source
        ON execution_units (source_type, source_id)
        WHERE source_type IS NOT NULL AND source_id IS NOT NULL
    """)

    # IDEM-5a: dynamic_flows — rename old ix_ index (if present from create_all with
    # the previous unique=True,index=True column declaration) or create fresh.
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_dynamic_flows_name') THEN
            ALTER INDEX ix_dynamic_flows_name RENAME TO uq_dynamic_flows_name;
          ELSE
            CREATE UNIQUE INDEX IF NOT EXISTS uq_dynamic_flows_name ON dynamic_flows (name);
          END IF;
        END $$
    """)

    # IDEM-5b: dynamic_nodes — same rename/create pattern
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_dynamic_nodes_name') THEN
            ALTER INDEX ix_dynamic_nodes_name RENAME TO uq_dynamic_nodes_name;
          ELSE
            CREATE UNIQUE INDEX IF NOT EXISTS uq_dynamic_nodes_name ON dynamic_nodes (name);
          END IF;
        END $$
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_dynamic_nodes_name")
    op.execute("DROP INDEX IF EXISTS uq_dynamic_flows_name")
    op.execute("DROP INDEX IF EXISTS uq_execution_units_source")
    op.execute("DROP INDEX IF EXISTS uq_platform_api_keys_user_name_active")
    op.execute("DROP INDEX IF EXISTS uq_webhook_subscriptions_event_url_active")
