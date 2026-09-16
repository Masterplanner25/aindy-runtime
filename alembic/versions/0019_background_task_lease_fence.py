"""background_task_leases gains a fencing token (LEASE-FENCE-1)

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-16

One additive column on ``background_task_leases``:

* ``fence``  BIGINT NOT NULL DEFAULT 0 — monotonic; 1 on the first claim, unchanged on renew,
  incremented on every TAKEOVER of an expired lease.

Expiry bounds how LONG two background leaders coexist (a GC pause, a disk stall) and does nothing
about what the stale one WRITES in that window — it learns it lost the lease at its next tick,
and its jobs already running keep running as leader. With the fence, a leader-only job reads the
row ``FOR SHARE`` inside its own transaction and refuses to commit if the fence moved: a takeover
must contend for the row lock, so a job that passed the check commits before anyone can become
leader, and a takeover that already committed leaves a higher fence behind. The stale leader is
refused, not asked to notice. Design: ``docs/design/LEASE_FENCE_DESIGN.md``.

**Data safety — purely additive, nothing to backfill.** An existing lease row reads ``fence = 0``;
the next takeover makes it 1, the next renew leaves it. A leader whose in-memory hold predates the
column has ``fence = None`` and the check is skipped for it (there is nothing to compare), which is
exactly the pre-fence behaviour for exactly one lease generation.

**★ Operators: this release DOES change the schema.** ``FR-14``: an additive runtime column makes a
bare ``aindy-runtime bootstrap-schema`` exit **3** (additive-reconcile-required). Existing
deployments must run ``bootstrap-schema --reconcile``, or branch on exit code 3.

Idempotent and blank-DB safe (ALEMBIC-FRESH-DB-1): the table-existence guard skips the block on a
blank database and the ORM ``create_all`` guard then bootstraps the table, column included.
``ADD COLUMN IF NOT EXISTS`` alone would raise ``UndefinedTable`` against a missing table.

``downgrade()`` drops the column.
"""

from alembic import op


revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename='background_task_leases' AND schemaname='public'
          ) THEN
            ALTER TABLE background_task_leases
              ADD COLUMN IF NOT EXISTS fence BIGINT NOT NULL DEFAULT 0;
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
            WHERE tablename='background_task_leases' AND schemaname='public'
          ) THEN
            ALTER TABLE background_task_leases DROP COLUMN IF EXISTS fence;
          END IF;
        END $$
        """
    )
