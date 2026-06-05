from __future__ import annotations

import pytest

from AINDY.watcher.constants import VALID_ACTIVITY_TYPES, VALID_SIGNAL_TYPES, parse_timestamp

pytestmark = pytest.mark.runtime_only


def test_valid_signal_types_contains_lifecycle_signals():
    assert "session_started" in VALID_SIGNAL_TYPES
    assert "session_ended" in VALID_SIGNAL_TYPES
    assert "distraction_detected" in VALID_SIGNAL_TYPES
    assert "focus_achieved" in VALID_SIGNAL_TYPES
    assert "heartbeat" in VALID_SIGNAL_TYPES


def test_valid_activity_types_contains_all_categories():
    assert "work" in VALID_ACTIVITY_TYPES
    assert "communication" in VALID_ACTIVITY_TYPES
    assert "distraction" in VALID_ACTIVITY_TYPES
    assert "idle" in VALID_ACTIVITY_TYPES
    assert "unknown" in VALID_ACTIVITY_TYPES


def test_parse_timestamp_parses_utc_z_suffix():
    dt = parse_timestamp("2025-01-01T12:00:00Z")
    assert dt.tzinfo is not None
    assert dt.year == 2025
    assert dt.month == 1


def test_parse_timestamp_raises_on_invalid_input():
    with pytest.raises(ValueError):
        parse_timestamp("not-a-date")
