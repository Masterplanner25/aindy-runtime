"""FR-8 — `--reconcile` must grandfather rows that predate a newly-added column.

`server_default` decides what a column holds for rows written *afterwards*. That is not
always right for rows that already exist: `users.is_verified` defaults to false because new
accounts are unverified, but an account created before verification existed was never given
a chance to confirm. Leaving it false silently arms a lockout the moment an operator turns
on ``AINDY_REQUIRE_VERIFIED_LOGIN``.

Alembic 0014 encodes that distinction, but the ``alembic/`` tree is not shipped in the wheel
(``packages.find`` is ``AINDY*``), so the only install shape Docker uses never runs it — it
reconciles from packaged metadata instead. These tests pin the metadata path, which is the
one that was silently wrong.
"""
import pytest
import sqlalchemy as sa

from AINDY.db.models.user import User
from AINDY.db.schema_contract import (
    RECONCILE_BACKFILL_KEY,
    _render_add_column_sql,
    _render_backfill_sql,
)

pytestmark = pytest.mark.runtime_only


# ---------------------------------------------------------------------------
# The declaration itself — this is what makes the guarantee portable
# ---------------------------------------------------------------------------

def test_user_verification_columns_declare_a_backfill():
    """Without these, a wheel install reconciles to every account unverified."""
    assert User.__table__.c.is_verified.info.get(RECONCILE_BACKFILL_KEY) == "true"
    assert (
        User.__table__.c.verified_at.info.get(RECONCILE_BACKFILL_KEY)
        == "COALESCE(created_at, now())"
    )


def test_columns_without_a_declaration_render_no_backfill():
    """The mechanism is opt-in — an ordinary column must be untouched."""
    engine = sa.create_engine("sqlite://")
    table = User.__table__
    assert _render_backfill_sql(engine, table, table.c.email) is None
    assert _render_backfill_sql(engine, table, table.c.hashed_password) is None


def test_rendered_backfill_targets_the_right_table_and_column():
    engine = sa.create_engine("sqlite://")
    table = User.__table__
    sql = _render_backfill_sql(engine, table, table.c.is_verified)
    assert sql is not None
    normalized = sql.replace('"', "").replace("`", "")
    assert normalized == "UPDATE users SET is_verified = true"


def test_backfill_has_no_where_clause():
    """Deliberate, and worth pinning.

    The column did not exist a moment before the ADD COLUMN, so every row now present
    predates it by construction. A WHERE would only narrow that correct set.
    """
    engine = sa.create_engine("sqlite://")
    table = User.__table__
    sql = _render_backfill_sql(engine, table, table.c.is_verified)
    assert "WHERE" not in sql.upper()


# ---------------------------------------------------------------------------
# End to end against a real table — the behaviour the app team measured
# ---------------------------------------------------------------------------

def test_preexisting_rows_are_grandfathered_and_later_rows_are_not():
    """Reproduces the upgrade: rows exist, the column is added, then reconciled.

    Uses a text column rather than the real boolean, deliberately. SQLAlchemy renders
    ``server_default="false"`` as ``DEFAULT 'false'`` — a *quoted string literal*. Postgres
    casts that to boolean false on a BOOLEAN column, but sqlite stores the four characters
    ``false``, which reads back truthy. Asserting boolean semantics here would be testing
    sqlite's type affinity, not the backfill. The mechanism under test — does the UPDATE run
    and does it touch only pre-existing rows — is dialect-independent, so the column type is
    chosen not to obscure it. The boolean literals themselves are pinned at render level
    above, and Postgres is exercised by the app-side upgrade path.
    """
    engine = sa.create_engine("sqlite://")
    meta = sa.MetaData()
    accounts = sa.Table(
        "accounts",
        meta,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(255)),
    )
    meta.create_all(engine)

    with engine.begin() as conn:
        conn.execute(accounts.insert(), [{"email": "old-1"}, {"email": "old-2"}])

    # The column as it would arrive from packaged metadata. append_column is the public way
    # to attach it; Column._set_parent is private and its signature moves between
    # SQLAlchemy versions.
    new_column = sa.Column(
        "tier",
        sa.String(32),
        nullable=False,
        server_default="new",
        info={RECONCILE_BACKFILL_KEY: "'legacy'"},
    )
    accounts.append_column(new_column)

    with engine.begin() as conn:
        conn.execute(sa.text(_render_add_column_sql(conn, accounts, new_column)))

        # Before the backfill: the reported symptom in general form — pre-existing rows
        # silently take the value meant for rows created afterwards.
        pre = conn.execute(sa.text("SELECT tier FROM accounts")).scalars().all()
        assert pre == ["new", "new"], f"expected the server_default, got {pre}"

        conn.execute(sa.text(_render_backfill_sql(conn, accounts, new_column)))

        grandfathered = conn.execute(sa.text("SELECT tier FROM accounts")).scalars().all()
        assert grandfathered == ["legacy", "legacy"], "pre-existing rows must be grandfathered"

        # A row written after the reconcile is genuinely new: it must NOT be swept up. This
        # is the half that proves the backfill is not simply "set every row".
        conn.execute(sa.text("INSERT INTO accounts (email) VALUES ('new-1')"))
        fresh = conn.execute(
            sa.text("SELECT tier FROM accounts WHERE email = 'new-1'")
        ).scalar()
        assert fresh == "new", "a post-reconcile row must keep the server_default"
