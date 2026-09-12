"""FR-23 — `/observability/system` reported 0 syscalls and 0 tools while ~90 and 16 were live.

Two wrong dicts behind one confident number on an operator surface:

* `syscall_count` walked `platform_layer.registry._syscalls`, which the dispatcher **never
  reads** — every app registers into `kernel.syscall_registry.SYSCALL_REGISTRY`.
* `tool_count` walked the static `register_agent_tool` model, which no app uses — tools arrive
  through `register_run_tool_provider` and execute from `agents.tool_registry.TOOL_REGISTRY`.

★ The contract test calls the route through the booted runtime app (`ROUTE-GUARD-1`) and
asserts the numbers against the SAME sources dispatch and `execute_tool` resolve against, so
the metric cannot drift back to a dict nothing else reads. The last test pins the half of the
finding that is not fixed here: `platform_layer.register_syscall` still routes nowhere, and it
must at least say so.
"""
from __future__ import annotations

import logging
import uuid

import pytest
from fastapi.testclient import TestClient

from AINDY.services.auth_service import get_current_user

pytestmark = pytest.mark.runtime_only


def _admin_session() -> dict:
    from AINDY.auth.api_key_auth import derive_session_scopes

    uid = str(uuid.uuid4())
    return {
        "sub": uid,
        "user_id": uid,
        "auth_type": "jwt",
        "is_admin": True,
        "session_scopes": derive_session_scopes(is_admin=True),
    }


@pytest.fixture
def admin_client(runtime_only_app):
    runtime_only_app.dependency_overrides[get_current_user] = _admin_session
    with TestClient(runtime_only_app, raise_server_exceptions=False) as client:
        yield client


def _registry(admin_client) -> dict:
    response = admin_client.get("/platform/observability/system")
    assert response.status_code == 200, response.text
    body = response.json()
    payload = body.get("data", body)
    return payload["registry"]


def test_syscall_count_is_what_the_dispatcher_can_reach(admin_client):
    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY, SYSCALL_REGISTRY_MIN_COUNT

    registry = _registry(admin_client)

    assert registry["syscall_count"] == len(SYSCALL_REGISTRY)
    assert registry["syscall_count"] >= SYSCALL_REGISTRY_MIN_COUNT, (
        "liveness: the booted runtime registers its own syscalls, so a correct count cannot "
        "be zero — a zero here is the FR-23 defect, not an empty deployment"
    )


def test_tool_count_is_what_execute_tool_can_reach(admin_client):
    from AINDY.agents.tool_registry import TOOL_REGISTRY

    registry = _registry(admin_client)

    assert registry["tool_count"] == len(TOOL_REGISTRY)
    assert registry["tool_count"] > 0, (
        "liveness: the runtime agent defaults register memory tools in every process, so a "
        "correct count cannot be zero"
    )


def test_a_syscall_registered_the_way_apps_register_is_counted(admin_client):
    """The defect, driven directly: register through the kernel path and watch the number."""
    from unittest.mock import patch

    from AINDY.kernel.syscall_registry import SyscallEntry

    before = _registry(admin_client)["syscall_count"]
    entry = SyscallEntry(lambda payload, ctx: {}, "memory.read", "fr23 probe")
    with patch.dict(
        "AINDY.kernel.syscall_registry.SYSCALL_REGISTRY", {"sys.v1.fr23.probe": entry}, clear=False
    ):
        after = _registry(admin_client)["syscall_count"]

    assert after == before + 1


def test_the_provider_model_is_visible(admin_client):
    """The tool model apps actually use is a callable per run type, not a static dict. The
    surface now names the run types that have one, so 'tool_count' is not the only word."""
    registry = _registry(admin_client)

    assert "run_tool_provider_run_types" in registry
    assert "default" in registry["run_tool_provider_run_types"], (
        "the runtime agent defaults register a 'default' provider; its absence means the "
        "list is being read from somewhere the defaults do not land"
    )


def test_the_legacy_platform_layer_dicts_are_no_longer_the_source(admin_client):
    """Mutation control: putting entries into the dicts the OLD code counted must not move
    the numbers. If it does, the metric has drifted back to a source nothing dispatches from."""
    from unittest.mock import patch

    before = _registry(admin_client)
    with patch.dict(
        "AINDY.platform_layer.registry._syscalls", {"ghost.one": lambda *a: None}, clear=False
    ), patch.dict(
        "AINDY.platform_layer.registry._agent_tools", {"ghost.tool": object()}, clear=False
    ):
        during = _registry(admin_client)

    assert during["syscall_count"] == before["syscall_count"]
    assert during["tool_count"] == before["tool_count"]


def test_platform_layer_register_syscall_says_it_routes_nowhere(caplog):
    """The open half of FR-23, made at least audible. A validating function that accepts a
    handler and routes it nowhere is worse than an absent one — it accepts work silently.
    Removing it is an ABI decision (it is a capability-gated in-process entry); until that is
    taken, it must warn."""
    from unittest.mock import patch

    from AINDY.platform_layer import registry

    caplog.set_level(logging.WARNING, logger="AINDY.platform_layer.registry")

    # ★ Note the signature: this seam validates a SINGLE-parameter handler, while the kernel
    # registry the dispatcher reads takes `(payload, ctx)`. They were never the same contract.
    def handler(payload):
        return {}

    with patch.dict("AINDY.platform_layer.registry._syscalls", {}, clear=False), patch.object(
        registry, "_require_in_process_extension_capability", lambda *_a, **_k: None
    ):
        registry.register_syscall("sys.v1.fr23.unreachable", handler)
        assert registry.get_syscall("sys.v1.fr23.unreachable") is handler

    from AINDY.kernel.syscall_registry import SYSCALL_REGISTRY

    assert "sys.v1.fr23.unreachable" not in SYSCALL_REGISTRY, (
        "if this ever passes, the seam got wired into dispatch — close FR-23's open half and "
        "delete the warning, do not keep both"
    )
    lines = [r.getMessage() for r in caplog.records if r.name == "AINDY.platform_layer.registry"]
    assert any("NOT read by the dispatcher" in line and "sys.v1.fr23.unreachable" in line for line in lines), lines
