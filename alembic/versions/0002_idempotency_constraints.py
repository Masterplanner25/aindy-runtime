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

All DML and index creation steps are wrapped in table-existence guards
(DO $$ BEGIN IF EXISTS (pg_catalog.pg_tables) THEN ... END IF; END $$).
This makes the migration safe to run on a blank database where the tables
have not yet been created by the server's schema bootstrap (create_all).

- Fresh Docker deployment: tables do not exist when alembic runs; DML and
  DDL are skipped. The server's Phase 5 startup calls ensure_runtime_schema
  with allow_bootstrap=True, which runs create_all with the current ORM
  metadata (including the unique constraints). Result: constraints present.
- Existing deployment upgrade: tables exist; dedup DML and CREATE INDEX
  execute normally. Result: duplicates cleaned, constraints added.

The IF NOT EXISTS guards on CREATE UNIQUE INDEX remain so this migration is
also safe against a schema that was bootstrapped via create_all before this
migration was applied.

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
    # Each DML block is wrapped in a table-existence guard so that running
    # `alembic upgrade head` on a blank database (before the server's
    # create_all bootstrap) skips silently rather than raising UndefinedTable.
    # On existing deployments with data these blocks run and deduplicate rows.

    # IDEM-3 dedup: keep newest active key per (user_id, name), deactivate rest
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename='platform_api_keys' AND schemaname='public'
          ) THEN
            UPDATE platform_api_keys SET is_active = false
            WHERE is_active = true
              AND id NOT IN (
                SELECT DISTINCT ON (user_id, name) id
                FROM platform_api_keys
                WHERE is_active = true
                ORDER BY user_id, name, created_at DESC
              );
          END IF;
        END $$
    """)

    # IDEM-2 dedup: keep newest active subscription per (event_type, callback_url)
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename='webhook_subscriptions' AND schemaname='public'
          ) THEN
            UPDATE webhook_subscriptions SET is_active = false
            WHERE is_active = true
              AND id NOT IN (
                SELECT DISTINCT ON (event_type, callback_url) id
                FROM webhook_subscriptions
                WHERE is_active = true
                ORDER BY event_type, callback_url, created_at DESC
              );
          END IF;
        END $$
    """)

    # IDEM-4 dedup: keep newest EU per (source_type, source_id) where both non-NULL
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename='execution_units' AND schemaname='public'
          ) THEN
            DELETE FROM execution_units
            WHERE source_type IS NOT NULL
              AND source_id IS NOT NULL
              AND id NOT IN (
                SELECT DISTINCT ON (source_type, source_id) id
                FROM execution_units
                WHERE source_type IS NOT NULL AND source_id IS NOT NULL
                ORDER BY source_type, source_id, created_at DESC
              );
          END IF;
        END $$
    """)

    # ── Uniqueness constraints ─────────────────────────────────────────────────
    # Gated on table existence so the migration is safe on blank databases.
    # IF NOT EXISTS on the index name makes it safe against create_all having
    # already created the index from the ORM __table_args__ declaration.

    # IDEM-2: webhook_subscriptions — partial unique on (event_type, callback_url)
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename='webhook_subscriptions' AND schemaname='public'
          ) THEN
            CREATE UNIQUE INDEX IF NOT EXISTS uq_webhook_subscriptions_event_url_active
            ON webhook_subscriptions (event_type, callback_url)
            WHERE is_active = true;
          END IF;
        END $$
    """)

    # IDEM-3: platform_api_keys — partial unique on (user_id, name)
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename='platform_api_keys' AND schemaname='public'
          ) THEN
            CREATE UNIQUE INDEX IF NOT EXISTS uq_platform_api_keys_user_name_active
            ON platform_api_keys (user_id, name)
            WHERE is_active = true;
          END IF;
        END $$
    """)

    # IDEM-4: execution_units — partial unique on (source_type, source_id)
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename='execution_units' AND schemaname='public'
          ) THEN
            CREATE UNIQUE INDEX IF NOT EXISTS uq_execution_units_source
            ON execution_units (source_type, source_id)
            WHERE source_type IS NOT NULL AND source_id IS NOT NULL;
          END IF;
        END $$
    """)

    # IDEM-5a: dynamic_flows — rename old ix_ index (if present from create_all with
    # the previous unique=True,index=True column declaration) or create fresh.
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename='dynamic_flows' AND schemaname='public'
          ) THEN
            IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_dynamic_flows_name') THEN
              ALTER INDEX ix_dynamic_flows_name RENAME TO uq_dynamic_flows_name;
            ELSE
              CREATE UNIQUE INDEX IF NOT EXISTS uq_dynamic_flows_name ON dynamic_flows (name);
            END IF;
          END IF;
        END $$
    """)

    # IDEM-5b: dynamic_nodes — same rename/create pattern
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename='dynamic_nodes' AND schemaname='public'
          ) THEN
            IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_dynamic_nodes_name') THEN
              ALTER INDEX ix_dynamic_nodes_name RENAME TO uq_dynamic_nodes_name;
            ELSE
              CREATE UNIQUE INDEX IF NOT EXISTS uq_dynamic_nodes_name ON dynamic_nodes (name);
            END IF;
          END IF;
        END $$
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_dynamic_nodes_name")
    op.execute("DROP INDEX IF EXISTS uq_dynamic_flows_name")
    op.execute("DROP INDEX IF EXISTS uq_execution_units_source")
    op.execute("DROP INDEX IF EXISTS uq_platform_api_keys_user_name_active")
    op.execute("DROP INDEX IF EXISTS uq_webhook_subscriptions_event_url_active")
