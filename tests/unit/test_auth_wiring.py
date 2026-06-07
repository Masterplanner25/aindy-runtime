"""
Tests for Tier 2 auth wiring fixes:

  V1 — auth/__init__.py is now a re-export shim (no more verbatim duplicate).
  V6 — Two distinct guards with precise semantics:
       require_platform_admin_access: platform router — passes all API keys,
         requires is_admin for JWT (API key scope enforcement is per-endpoint).
       require_admin_principal: true admin operations — requires platform.admin
         scope for API keys AND is_admin for JWT.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from unittest.mock import MagicMock, patch


pytestmark = pytest.mark.runtime_only


# ---------------------------------------------------------------------------
# V1 — auth/__init__.py re-exports
# ---------------------------------------------------------------------------

def test_auth_package_exports_scopes():
    from AINDY.auth import Scopes
    assert hasattr(Scopes, "PLATFORM_ADMIN")
    assert "platform.admin" in Scopes.ALL



def test_auth_init_and_api_key_auth_share_same_scopes():
    """Symbols from both import paths must be the same objects — no divergence."""
    from AINDY.auth import Scopes as Scopes_init
    from AINDY.auth.api_key_auth import Scopes as Scopes_direct
    assert Scopes_init is Scopes_direct


# ---------------------------------------------------------------------------
# V6 — require_platform_admin_access (platform router guard)
# ---------------------------------------------------------------------------

def _make_api_key_user(scopes):
    return {"auth_type": "api_key", "api_key_scopes": scopes, "sub": "key-123"}


def _make_jwt_user(is_admin):
    return {"auth_type": "jwt", "is_admin": is_admin, "sub": "user-456"}


def _call_platform_guard(user_dict):
    """Call require_platform_admin_access with a pre-resolved current_user."""
    from AINDY.services.auth_service import require_platform_admin_access
    return require_platform_admin_access(current_user=user_dict)


def _call_admin_guard(user_dict):
    """Call require_admin_principal with a pre-resolved current_user."""
    from AINDY.services.auth_service import require_admin_principal
    return require_admin_principal(current_user=user_dict)


# require_platform_admin_access: any API key passes (scope enforced per-endpoint)
def test_platform_guard_passes_any_api_key():
    user = _make_api_key_user(["memory.write", "memory.read"])
    result = _call_platform_guard(user)
    assert result["auth_type"] == "api_key"


def test_platform_guard_passes_api_key_with_no_scopes():
    user = _make_api_key_user([])
    result = _call_platform_guard(user)
    assert result["auth_type"] == "api_key"


def test_platform_guard_passes_jwt_admin():
    user = _make_jwt_user(is_admin=True)
    result = _call_platform_guard(user)
    assert result["is_admin"] is True


def test_platform_guard_rejects_jwt_non_admin():
    user = _make_jwt_user(is_admin=False)
    with pytest.raises(HTTPException) as exc_info:
        _call_platform_guard(user)
    assert exc_info.value.status_code == 403


# require_admin_principal: strict — both auth types must prove admin
def test_admin_guard_passes_api_key_with_platform_admin_scope():
    user = _make_api_key_user(["platform.admin", "flow.read"])
    result = _call_admin_guard(user)
    assert result["auth_type"] == "api_key"


def test_admin_guard_rejects_api_key_without_platform_admin_scope():
    user = _make_api_key_user(["memory.write", "memory.read"])
    with pytest.raises(HTTPException) as exc_info:
        _call_admin_guard(user)
    assert exc_info.value.status_code == 403


def test_admin_guard_rejects_api_key_with_empty_scopes():
    user = _make_api_key_user([])
    with pytest.raises(HTTPException) as exc_info:
        _call_admin_guard(user)
    assert exc_info.value.status_code == 403


def test_admin_guard_passes_jwt_admin():
    user = _make_jwt_user(is_admin=True)
    result = _call_admin_guard(user)
    assert result["is_admin"] is True


def test_admin_guard_rejects_jwt_non_admin():
    user = _make_jwt_user(is_admin=False)
    with pytest.raises(HTTPException) as exc_info:
        _call_admin_guard(user)
    assert exc_info.value.status_code == 403
