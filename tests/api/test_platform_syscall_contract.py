from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from AINDY.services.auth_service import get_current_user, require_platform_admin_access


pytestmark = pytest.mark.runtime_only


def _admin_user():
    return {
        "sub": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "is_admin": True,
        "auth_type": "jwt",
    }


def test_platform_syscall_catalog_exposes_stable_and_experimental_markers(runtime_only_app):
    runtime_only_app.dependency_overrides[get_current_user] = _admin_user
    runtime_only_app.dependency_overrides[require_platform_admin_access] = _admin_user

    with TestClient(runtime_only_app, raise_server_exceptions=False) as client:
        response = client.get("/platform/syscalls")

    assert response.status_code == 200
    payload = response.json()
    assert "v1" in payload["versions"]
    assert payload["syscalls"]["v1"]["memory.read"]["stable"] is True
    assert payload["syscalls"]["v1"]["memory.list"]["stable"] is False
    assert payload["syscalls"]["v1"]["agent.count_runs"]["stable"] is False
