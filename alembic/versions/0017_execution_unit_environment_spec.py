"""execution_units gains the environment descriptor (EXEC-ENV-BIND-1, phase 1)

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-19

Three additive, nullable columns on ``execution_units``:

* ``env_spec``            JSONB   — what the caller DECLARED
* ``env_applied``         JSONB   — the EFFECTIVE spec after clamping to the host floor
* ``env_evidence_class``  VARCHAR — ``"<assurance_class>/<assurance_ceiling>"`` of the resolved runner

Design: ``docs/runtime/EXECUTION_ENVIRONMENT_SPEC_DESIGN.md``.

**Why three columns and not one.** They answer three different questions — what was asked for,
what was actually in force, and how well verified the boundary was. Collapsing them into one
JSONB blob is the ``register_syscall`` / ``FR-14`` shape: a surface that loses a distinction the
record already had. ``env_evidence_class`` in particular is the only field that says whether the
environment was *enforced*; in phase 1 nothing applies a spec, so a populated ``env_applied``
must not be read as evidence of confinement.

**Data safety — this is purely additive and there is nothing to backfill.** ``NULL`` means
"declared nothing" and is defined to behave exactly as the code did before these columns existed.
Every pre-existing row is ``NULL``, and the descriptor is opt-in per execution. No row legal
before is illegal after.

**★ Operators: this release DOES change the schema, and that has a deployment consequence.**
``FR-14``: an additive runtime column makes a bare ``aindy-runtime bootstrap-schema`` exit **3**
(additive-reconcile-required). Under ``set -e`` with ``restart: unless-stopped`` that is a crash
loop, not a warning. Existing deployments must run ``bootstrap-schema --reconcile``, or branch on
exit code 3. The app handoff for this release states this explicitly — it is the first release
since the exit-code work landed where the condition actually fires.

Idempotent and blank-DB safe (ALEMBIC-FRESH-DB-1). In Docker compose ``alembic upgrade head``
runs *before* the ORM ``create_all`` schema guard, so ``execution_units`` may not exist yet. The
table-existence guard skips the whole block on a blank database and Phase 5 then bootstraps the
table — these columns included — from ORM metadata. ``ADD COLUMN IF NOT EXISTS`` alone would NOT
be sufficient: against a missing table it still raises ``UndefinedTable``.

``downgrade()`` drops all three columns.
"""

from alembic import op


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_tables
            WHERE tablename='execution_units' AND schemaname='public'
          ) THEN
            ALTER TABLE execution_units ADD COLUMN IF NOT EXISTS env_spec JSONB;
            ALTER TABLE execution_units ADD COLUMN IF NOT EXISTS env_applied JSONB;
            ALTER TABLE execution_units ADD COLUMN IF NOT EXISTS env_evidence_class VARCHAR(96);
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
            WHERE tablename='execution_units' AND schemaname='public'
          ) THEN
            ALTER TABLE execution_units DROP COLUMN IF EXISTS env_evidence_class;
            ALTER TABLE execution_units DROP COLUMN IF EXISTS env_applied;
            ALTER TABLE execution_units DROP COLUMN IF EXISTS env_spec;
          END IF;
        END $$
        """
    )
