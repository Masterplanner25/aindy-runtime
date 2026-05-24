from __future__ import annotations

import pytest

pytestmark = pytest.mark.runtime_only


def test_effect_record_model_imports():
    from AINDY.db.models.effect_record import EffectRecord

    assert EffectRecord.__tablename__ == "effect_records"


def test_effect_record_in_models_init():
    from AINDY.db.models import EffectRecord

    assert EffectRecord is not None


def test_effect_record_has_required_columns():
    from AINDY.db.models.effect_record import EffectRecord

    col_names = {c.name for c in EffectRecord.__table__.columns}
    for required in (
        "id",
        "action_id",
        "action_type",
        "input_hash",
        "status",
        "created_at",
        "execution_id",
        "step_id",
    ):
        assert required in col_names, f"Column {required!r} missing from effect_records"
