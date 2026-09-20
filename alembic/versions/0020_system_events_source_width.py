"""system_events.source widens from 32 to 128 characters (FR-41)

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-20

One column type change, no new column:

* ``system_events.source``  VARCHAR(32) → VARCHAR(128) — matches ``trace_id``.

The pipeline writes every request's route name here. At 32, a 33+ character route name raised
``StringDataRightTruncation`` inside the REQUIRED ``execution.started`` emit on every request;
the pipeline caught it, logged a WARNING, and the request proceeded with no execution record at
all — the quiet kind of failure. The app that found it had 8 of 230 route names over the width,
unrecorded since 2026-09-10. Companion runtime change: an over-width source is refused at
pipeline entry as a contract violation, and never truncated.

**Data safety — widening only.** Every existing value fits; nothing is rewritten. ``downgrade()``
narrows back with ``USING left(source, 32)``, which IS lossy for any row written with a longer
name after this migration — an operator downgrading past this revision accepts that, and the
runtime at 0019 could not have written such a row anyway.

**★ Operators: this release DOES change the schema.** ``FR-14``: a runtime schema change makes a
bare ``aindy-runtime bootstrap-schema`` exit **3** (additive-reconcile-required). Existing
deployments run ``bootstrap-schema --reconcile``, or branch on exit code 3.

Idempotent and blank-DB safe (ALEMBIC-FRESH-DB-1): the table-existence guard skips the block on
a blank database and the ORM ``create_all`` then creates the table at 128. ``ALTER COLUMN TYPE``
to the same type is a no-op on a re-run.
"""

from alembic import op


revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename='system_events' AND schemaname='public'
          ) THEN
            ALTER TABLE system_events ALTER COLUMN source TYPE VARCHAR(128);
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
            WHERE tablename='system_events' AND schemaname='public'
          ) THEN
            ALTER TABLE system_events ALTER COLUMN source TYPE VARCHAR(32) USING left(source, 32);
          END IF;
        END $$
        """
    )
