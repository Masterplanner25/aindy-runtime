"""
Tests for Tier 2 auth wiring fixes:

  V1 — auth/__init__.py is now a re-export shim (no more verbatim duplicate).
  V6a — require_platform_admin_access enforces platform.admin scope for API keys.
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


def test_auth_package_exports_require_scope():
    from AINDY.auth import require_scope
    assert callable(require_scope)


def test_auth_package_exports_auth_principal():
    from AINDY.auth import AuthPrincipal
    assert AuthPrincipal is not None


def test_auth_package_exports_get_authenticated_principal():
    from AINDY.auth import get_authenticated_principal
    assert callable(get_authenticated_principal)


def test_auth_init_and_api_key_auth_share_same_scopes():
    """Symbols from both import paths must be the same objects — no divergence."""
    from AINDY.auth import Scopes as Scopes_init
    from AINDY.auth.api_key_auth import Scopes as Scopes_direct
    assert Scopes_init is Scopes_direct


# ---------------------------------------------------------------------------
# V6a — require_platform_admin_access scope enforcement
# ---------------------------------------------------------------------------

def _make_api_key_user(scopes):
    return {"auth_type": "api_key", "api_key_scopes": scopes, "sub": "key-123"}


def _make_jwt_user(is_admin):
    return {"auth_type": "jwt", "is_admin": is_admin, "sub": "user-456"}


def _call_guard(user_dict):
    """Call require_platform_admin_access with a pre-resolved current_user."""
    from AINDY.services.auth_service import require_platform_admin_access
    mock_dep = MagicMock(return_value=user_dict)
    with patch("AINDY.services.auth_service.get_current_user", mock_dep):
        return require_platform_admin_access(current_user=user_dict)


def test_api_key_with_platform_admin_scope_passes():
    user = _make_api_key_user(["platform.admin", "flow.read"])
    result = _call_guard(user)
    assert result["auth_type"] == "api_key"


def test_api_key_without_platform_admin_scope_is_rejected():
    user = _make_api_key_user(["flow.read", "memory.read"])
    with pytest.raises(HTTPException) as exc_info:
        _call_guard(user)
    assert exc_info.value.status_code == 403


def test_api_key_with_empty_scopes_is_rejected():
    user = _make_api_key_user([])
    with pytest.raises(HTTPException) as exc_info:
        _call_guard(user)
    assert exc_info.value.status_code == 403


def test_api_key_with_no_scopes_field_is_rejected():
    user = {"auth_type": "api_key", "sub": "key-789"}
    with pytest.raises(HTTPException) as exc_info:
        _call_guard(user)
    assert exc_info.value.status_code == 403


def test_jwt_admin_passes():
    user = _make_jwt_user(is_admin=True)
    result = _call_guard(user)
    assert result["is_admin"] is True


def test_jwt_non_admin_is_rejected():
    user = _make_jwt_user(is_admin=False)
    with pytest.raises(HTTPException) as exc_info:
        _call_guard(user)
    assert exc_info.value.status_code == 403
