"""flow_runs gains a graph signature (FLOW-GRAPH-SIGNATURE-1)

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-02

One additive, nullable column on ``flow_runs``:

* ``graph_signature``  VARCHAR(64)  — sha256 of the flow's SHAPE when the run started

A suspended run was restored against whatever definition ``register_flows()`` produced *this*
boot. Nothing recorded what it was planned against, so a node renamed or an edge rerouted between
suspend and resume executed against a definition the run was never planned for — silently, and
reported as success. This column is the record that makes the mismatch detectable.

**Data safety — purely additive, nothing to backfill, and NULL is meaningful.** ``NULL`` means
"this run predates the column" and is defined to behave exactly as the code did before: the
comparison cannot be made, so the resume proceeds. That is deliberate. Treating NULL as a
mismatch would quarantine every in-flight run the moment this deployed, which is how a guard like
this gets switched off within a week. No row legal before is illegal after.

**★ Operators: this release DOES change the schema.** ``FR-14``: an additive runtime column makes
a bare ``aindy-runtime bootstrap-schema`` exit **3** (additive-reconcile-required). Under
``set -e`` with ``restart: unless-stopped`` that is a crash loop, not a warning. Existing
deployments must run ``bootstrap-schema --reconcile``, or branch on exit code 3.

Idempotent and blank-DB safe (ALEMBIC-FRESH-DB-1). In Docker compose ``alembic upgrade head``
runs *before* the ORM ``create_all`` schema guard, so ``flow_runs`` may not exist yet. The
table-existence guard skips the block on a blank database and Phase 5 then bootstraps the table —
this column included — from ORM metadata. ``ADD COLUMN IF NOT EXISTS`` alone would NOT be
sufficient: against a missing table it still raises ``UndefinedTable``.

``downgrade()`` drops the column.
"""

from alembic import op


revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename='flow_runs' AND schemaname='public'
          ) THEN
            ALTER TABLE flow_runs ADD COLUMN IF NOT EXISTS graph_signature VARCHAR(64);
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
            WHERE tablename='flow_runs' AND schemaname='public'
          ) THEN
            ALTER TABLE flow_runs DROP COLUMN IF EXISTS graph_signature;
          END IF;
        END $$
        """
    )
