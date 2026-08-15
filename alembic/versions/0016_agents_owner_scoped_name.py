"""agents.name unique per owner, not globally (APP-FR-* FR-12 remainder)

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-15

``agents.name`` carried a global ``UNIQUE`` constraint, inherited from a table that
in practice held seven platform-owned rows. The table is shaped for a general
registry — it has ``owner_user_id`` — and a global unique name contradicts that
shape: the first user to register "Research Bot" would block every other user from
that name forever, and the 409 telling them so is a cross-tenant existence oracle.

Replaced by two **partial** unique indexes:

* ``uq_agents_name_shared``  — ``UNIQUE (name) WHERE owner_user_id IS NULL``
* ``uq_agents_owner_name``   — ``UNIQUE (owner_user_id, name) WHERE owner_user_id IS NOT NULL``

A single ``UNIQUE (owner_user_id, name)`` would not be equivalent. SQL treats NULLs
as distinct inside a unique constraint, so every shared row (system agents,
app-registered identities — all ``owner_user_id IS NULL``) would escape the
constraint entirely and two rows named "Runtime" would both be accepted. The
partial pair keeps the old global guarantee exactly where it still applies and
scopes it per user where it does not.

``memory_namespace`` is deliberately untouched and stays globally unique: it is the
tag written onto every memory node (``MemoryNodeModel.source_agent``), so one
namespace must mean one agent process-wide.

**Data safety.** This only ever *widens* what is accepted — every row legal before
is legal after — so there is nothing to backfill and no ``reconcile_backfill``
marker. The constraint drop is the only destructive-looking step, and it is
replaced in the same statement block.

**Constraint name.** ``agents_name_key`` is PostgreSQL's default for an inline
column ``UNIQUE``, which is how the table is created both by Alembic history and by
the ORM ``create_all`` schema guard. ``IF EXISTS`` covers a deployment where it was
named differently or already dropped.

Idempotent and blank-DB safe (ALEMBIC-FRESH-DB-1): in Docker compose
``alembic upgrade head`` runs before the ORM ``create_all`` guard, so ``agents`` may
not exist yet. The table-existence guard skips, and Phase 5 then bootstraps the
whole table — these indexes included — from ORM metadata.

``downgrade()`` drops both indexes and restores the global unique constraint. Note
it can legitimately fail on a database that has since accepted two same-named rows
for different owners; that is the correct outcome, since the old constraint cannot
represent that data.
"""

from alembic import op


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename='agents' AND schemaname='public'
          ) THEN
            ALTER TABLE agents DROP CONSTRAINT IF EXISTS agents_name_key;

            CREATE UNIQUE INDEX IF NOT EXISTS uq_agents_name_shared
              ON agents (name)
              WHERE owner_user_id IS NULL;

            CREATE UNIQUE INDEX IF NOT EXISTS uq_agents_owner_name
              ON agents (owner_user_id, name)
              WHERE owner_user_id IS NOT NULL;
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename='agents' AND schemaname='public'
          ) THEN
            DROP INDEX IF EXISTS uq_agents_owner_name;
            DROP INDEX IF EXISTS uq_agents_name_shared;

            IF NOT EXISTS (
              SELECT 1 FROM pg_catalog.pg_constraint WHERE conname='agents_name_key'
            ) THEN
              ALTER TABLE agents ADD CONSTRAINT agents_name_key UNIQUE (name);
            END IF;
          END IF;
        END $$
        """
    )
