"""
Kernel hardening — SystemEvent type registry and model schema contract.

Two drift guards:
  1. Event type hash — SHA-256 of the sorted SystemEventTypes string constants.
     Any addition, removal, or rename fails this test, forcing the change
     to be intentional. Update the baseline via the helper at the bottom.
  2. Model column set — the ORM field names of SystemEvent are stable.
     Schema changes go through Alembic migrations; silent ORM drift is caught here.
"""
from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

pytestmark = pytest.mark.runtime_only

_BASELINE_FILE = pathlib.Path(__file__).parent.parent / "baselines" / "system_event_contract.json"

_EXPECTED_MODEL_COLUMNS = frozenset({
    "id", "type", "user_id", "agent_id", "trace_id",
    "parent_event_id", "source", "payload", "timestamp",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collect_event_types() -> list[str]:
    from AINDY.core.system_event_types import SystemEventTypes
    return sorted(
        v for v in vars(SystemEventTypes).values()
        if isinstance(v, str) and not v.startswith("_")
    )


def _hash_event_types(types: list[str]) -> str:
    return hashlib.sha256("\n".join(types).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 1. Event type registry hash
# ---------------------------------------------------------------------------

def test_system_event_type_registry_is_stable():
    """
    Fails when SystemEventTypes gains, loses, or renames any event type string.
    To update the baseline after an intentional change:
        python -c "
        from tests.unit.test_system_event_contract import _collect_event_types, _hash_event_types
        import json, pathlib
        t = _collect_event_types(); h = _hash_event_types(t)
        pathlib.Path('tests/baselines/system_event_contract.json').write_text(
            json.dumps({'hash': h, 'types': t}, indent=2)
        )
        print('Baseline updated:', h)
        "
    """
    current_types = _collect_event_types()
    current_hash = _hash_event_types(current_types)

    if not _BASELINE_FILE.exists():
        _BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _BASELINE_FILE.write_text(
            json.dumps({"hash": current_hash, "types": current_types}, indent=2)
        )
        pytest.skip(f"Baseline created ({current_hash}) — commit tests/baselines/system_event_contract.json")

    baseline = json.loads(_BASELINE_FILE.read_text())
    baseline_hash = baseline["hash"]
    baseline_types = set(baseline["types"])

    added = set(current_types) - baseline_types
    removed = baseline_types - set(current_types)

    assert current_hash == baseline_hash, (
        f"SystemEventTypes registry drifted.\n"
        f"  Added:   {sorted(added) or '(none)'}\n"
        f"  Removed: {sorted(removed) or '(none)'}\n"
        f"  New hash: {current_hash}\n"
        "Update the baseline (see docstring) if the change is intentional."
    )


def test_all_event_type_constants_are_non_empty_strings():
    from AINDY.core.system_event_types import SystemEventTypes
    for name, value in vars(SystemEventTypes).items():
        if name.startswith("_") or callable(value):
            continue
        assert isinstance(value, str), f"{name} must be a str, got {type(value)}"
        assert value.strip(), f"{name} must not be empty or whitespace-only"


# ---------------------------------------------------------------------------
# 2. SystemEvent ORM model column set
# ---------------------------------------------------------------------------

def test_system_event_model_columns_are_stable():
    from AINDY.db.models.system_event import SystemEvent
    from sqlalchemy import inspect as sa_inspect

    mapper = sa_inspect(SystemEvent)
    actual_columns = frozenset(c.key for c in mapper.mapper.column_attrs)

    added = actual_columns - _EXPECTED_MODEL_COLUMNS
    removed = _EXPECTED_MODEL_COLUMNS - actual_columns

    assert not added and not removed, (
        f"SystemEvent ORM columns changed.\n"
        f"  Added:   {sorted(added) or '(none)'}\n"
        f"  Removed: {sorted(removed) or '(none)'}\n"
        "If intentional, update _EXPECTED_MODEL_COLUMNS in this file "
        "and create an Alembic migration."
    )
