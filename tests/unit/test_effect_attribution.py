"""MEB-3b — effect-record tenant/session attribution.

resolve_effect_record persists tenant_id/session_id onto a newly-inserted EffectRecord,
taking explicit kwargs first and falling back per-field to the ambient attribution
contextvar. These use a MagicMock db (so no real Postgres is needed) and inspect the real
EffectRecord object handed to db.add(). The end-to-end persistence on a live schema is
covered by the integration test (tests/integration/test_idempotency_gate_e2e.py).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.runtime_only


def _mock_db_no_existing():
    """A MagicMock db whose first insert path is taken (no existing record, clean commit)."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.commit.return_value = None
    return db


def _added_record(db):
    """The EffectRecord object handed to db.add() (real object; MagicMock only records it)."""
    assert db.add.call_count == 1, "expected exactly one EffectRecord insert"
    return db.add.call_args[0][0]


def test_explicit_attribution_persisted_on_new_record():
    from AINDY.kernel.effect_ledger import resolve_effect_record

    db = _mock_db_no_existing()
    done, cached = resolve_effect_record(
        db, "action-attr-1", "sys.v1.test", {"k": "v"},
        tenant_id="tenant-42", session_id="mcp:99",
    )

    assert (done, cached) == (False, None)
    rec = _added_record(db)
    assert rec.tenant_id == "tenant-42"
    assert rec.session_id == "mcp:99"


def test_no_attribution_defaults_to_none():
    from AINDY.kernel.effect_ledger import resolve_effect_record

    db = _mock_db_no_existing()
    resolve_effect_record(db, "action-attr-2", "sys.v1.test", {})

    rec = _added_record(db)
    assert rec.tenant_id is None
    assert rec.session_id is None


def test_contextvar_fallback_used_when_kwargs_absent():
    from AINDY.kernel import effect_ledger

    db = _mock_db_no_existing()
    token = effect_ledger.set_effect_attribution(tenant_id="ctx-tenant", session_id="ctx-session")
    try:
        effect_ledger.resolve_effect_record(db, "action-attr-3", "sys.v1.test", {})
    finally:
        effect_ledger.reset_effect_attribution(token)

    rec = _added_record(db)
    assert rec.tenant_id == "ctx-tenant"
    assert rec.session_id == "ctx-session"


def test_explicit_kwargs_override_contextvar_per_field():
    from AINDY.kernel import effect_ledger

    db = _mock_db_no_existing()
    token = effect_ledger.set_effect_attribution(tenant_id="ctx-tenant", session_id="ctx-session")
    try:
        # Explicit tenant overrides; session falls back to the contextvar.
        effect_ledger.resolve_effect_record(
            db, "action-attr-4", "sys.v1.test", {}, tenant_id="explicit-tenant",
        )
    finally:
        effect_ledger.reset_effect_attribution(token)

    rec = _added_record(db)
    assert rec.tenant_id == "explicit-tenant"
    assert rec.session_id == "ctx-session"


def test_attribution_is_not_folded_into_input_hash():
    """Attribution must not change the dedup key — two calls that differ ONLY by
    tenant/session produce the same input_hash on the inserted row."""
    from AINDY.kernel.effect_ledger import resolve_effect_record

    db_a = _mock_db_no_existing()
    resolve_effect_record(db_a, "aid", "sys.v1.test", {"x": 1}, tenant_id="A", session_id="s1")
    db_b = _mock_db_no_existing()
    resolve_effect_record(db_b, "aid", "sys.v1.test", {"x": 1}, tenant_id="B", session_id="s2")

    assert _added_record(db_a).input_hash == _added_record(db_b).input_hash


def test_contextvar_helpers_roundtrip():
    from AINDY.kernel import effect_ledger

    assert effect_ledger.current_effect_attribution() == (None, None)
    token = effect_ledger.set_effect_attribution(tenant_id="t", session_id="s")
    assert effect_ledger.current_effect_attribution() == ("t", "s")
    effect_ledger.reset_effect_attribution(token)
    assert effect_ledger.current_effect_attribution() == (None, None)
