"""
Tests for AINDY_ENV_FILE override and _resolve_env_file() resolution logic.

The env_file path is resolved at module-load time via _ENV_FILE / _resolve_env_file(),
not from a Settings instance — SettingsConfigDict(env_file=...) is evaluated at
class-definition time. The resolution helper is extracted for testability without
requiring importlib.reload() gymnastics, consistent with resolve_event_bus_redis_url().
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.runtime_only


# ---------------------------------------------------------------------------
# _resolve_env_file — resolution logic
# ---------------------------------------------------------------------------

def test_default_resolves_to_package_relative_env(monkeypatch):
    """Default path is AINDY/.env relative to config.py's parent directory."""
    monkeypatch.delenv("AINDY_ENV_FILE", raising=False)

    from AINDY.config import _resolve_env_file
    result = Path(_resolve_env_file())

    # Must be an absolute path ending in AINDY/.env
    assert result.is_absolute()
    assert result.name == ".env"
    assert result.parent.name == "AINDY"


def test_override_returns_custom_path(monkeypatch, tmp_path):
    """AINDY_ENV_FILE env var overrides the default resolution."""
    custom = tmp_path / "custom.env"
    custom.write_text("LOG_LEVEL=DEBUG\n")
    monkeypatch.setenv("AINDY_ENV_FILE", str(custom))

    from AINDY.config import _resolve_env_file
    assert _resolve_env_file() == str(custom)


def test_override_with_stable_container_path(monkeypatch):
    """
    Simulates the containerised deployment pattern:
    AINDY_ENV_FILE=/etc/aindy/.env so the bind-mount target is stable
    across package version upgrades (AINDY/.env path embeds no version).
    """
    monkeypatch.setenv("AINDY_ENV_FILE", "/etc/aindy/.env")

    from AINDY.config import _resolve_env_file
    assert _resolve_env_file() == "/etc/aindy/.env"


def test_empty_aindy_env_file_falls_through_to_default(monkeypatch):
    """
    Empty AINDY_ENV_FILE is treated as unset — os.getenv returns the empty
    string, which is falsy, so the default path is used instead.
    Consistent with the empty-string fall-through pattern in
    resolve_event_bus_redis_url().
    """
    monkeypatch.setenv("AINDY_ENV_FILE", "")

    from AINDY.config import _resolve_env_file
    result = Path(_resolve_env_file())
    # Empty string is falsy — falls through to default
    assert result.name == ".env"
    assert result.parent.name == "AINDY"
