"""INFINITY-COMPLETION-HOOK-BOUNDARY-1 — completion hooks must be usable.

The extension-boundary sanitizer strips `db` (blocked root key) and redacts the
`run` ORM object, and completion hooks were also subprocess-isolated — so a
first-party `handle_agent_run_completed` got `db=None` + a redacted run and
no-op'd, silently killing post-completion enforcement (e.g. the Infinity loop).

Fix (boundary-preserving): the context now carries `run_id` (a string, which
survives the sanitizer) and the surface runs in-process, so a hook can re-fetch
the run with its own session. The runtime still never leaks a db/session/ORM
handle across the boundary.
"""
from __future__ import annotations

import pytest

from AINDY.platform_layer import registry
from AINDY.platform_layer.extension_boundary import sanitize_extension_context

pytestmark = pytest.mark.runtime_only


class _FakeRun:
    """Looks like an ORM object to the sanitizer (has _sa_instance_state)."""

    __module__ = "AINDY.db.models.agent_run"

    def __init__(self):
        self._sa_instance_state = object()
        self.id = "run-abc"
        self.result = {"steps": []}


def test_run_id_survives_sanitizer_but_db_and_run_do_not():
    ctx = {
        "run": _FakeRun(),
        "db": object(),
        "run_id": "run-abc",
        "user_id": "u1",
        "run_type": "default",
        "trace_id": "t1",
    }
    clean = sanitize_extension_context(ctx)

    # run_id / user_id / strings survive — a hook can re-fetch by run_id.
    assert clean["run_id"] == "run-abc"
    assert clean["user_id"] == "u1"
    # db is dropped (blocked root key); run is redacted (no id, no .result).
    assert "db" not in clean
    assert clean["run"] == {"_redacted_type": "_FakeRun"}


def test_completion_hook_surface_runs_in_process():
    """agent_completion_hook must be in the stateful in-process set so a
    first-party hook isn't subprocess-isolated (can open a session / reach state)."""
    assert "agent_completion_hook" in registry._STATEFUL_IN_PROCESS_CALLBACK_SURFACES
    spec = registry._runtime_callback_spec(
        surface="agent_completion_hook",
        identifier="default",
        handler=lambda payload: None,
        expects_argument=True,
    )
    assert spec is None  # None => runs in-process, not wrapped into a subprocess


def test_hook_invoked_with_run_id_through_full_path():
    """End-to-end through run_agent_completion_hooks (the sanitized path) — the
    hook receives run_id and can act, and its return propagates back."""
    captured: dict = {}

    def _hook(payload):
        captured.update(payload)
        return {"decision": payload.get("run_id")}

    registry._agent_completion_hooks["default"].append(_hook)
    try:
        results = registry.run_agent_completion_hooks(
            "default",
            {"run": _FakeRun(), "db": object(), "run_id": "run-xyz", "user_id": "u9",
             "run_type": "default", "trace_id": "t"},
        )
    finally:
        registry._agent_completion_hooks["default"].remove(_hook)

    assert captured["run_id"] == "run-xyz"  # hook can re-fetch by this
    assert "db" not in captured  # boundary intact
    assert {"decision": "run-xyz"} in results  # return propagates (Gap-4 NextAction)
