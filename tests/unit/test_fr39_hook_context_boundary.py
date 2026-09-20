"""FR-39 — every tenant-bearing hook context the runtime builds survives the extension boundary.

#708 (FR-36) fixed the completion-hook builder and tested THAT builder. The planner-context and
tools-for-run contexts in `shared.py` had the same bug — `"user_id": _db_user_id(user_id)` is a
`uuid.UUID`, which `sanitize_extension_context` redacts to `{"_redacted_type": "UUID"}` — and the
app's planner-context provider had returned the bare base prompt on every plan since 2026-05-20.

Two guards, so the third instance fails here and not in an app log:
  1. the boundary test is parametrised over EVERY runtime-built hook context that carries a
     tenant, with production-shaped values (a real `uuid.UUID`, never the string "u1" that let
     FR-36's original test pass — variant 13);
  2. an AST census: no hook call site in `agents/agent_runtime/` hands the registry an inline
     dict — a context must come from a builder, and every builder is in (1).
"""
from __future__ import annotations

import ast
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.runtime_only

_AGENT_RUNTIME = Path(__file__).resolve().parents[2] / "AINDY" / "agents" / "agent_runtime"
# The registry entry points that receive a runtime-built context and sanitize it.
_HOOK_ENTRY_POINTS = {"get_planner_context", "get_tools_for_run", "run_agent_completion_hooks"}


class _OrmLikeRun:
    _sa_instance_state = object()

    def __init__(self):
        self.id = uuid.uuid4()
        self.agent_type = "default"
        self.trace_id = "trace-fr39"


class _SessionLike:
    def execute(self, *a, **k):
        return None

    def commit(self):
        return None


def _planner_context(tenant, db):
    from AINDY.agents.agent_runtime.shared import (
        PROVIDER_HOOK_PRIMITIVE_KEYS,
        build_provider_hook_context,
    )

    return build_provider_hook_context("default", user_id=tenant, db=db), PROVIDER_HOOK_PRIMITIVE_KEYS


def _tools_for_run_context(tenant, db):
    # Same builder as the planner context by construction; kept as its own census row so a
    # future split of the two contexts has to add a row here.
    return _planner_context(tenant, db)


def _completion_hook_context(tenant, db):
    from AINDY.agents.agent_runtime.execution import (
        COMPLETION_HOOK_PRIMITIVE_KEYS,
        build_completion_hook_context,
    )

    return build_completion_hook_context(_OrmLikeRun(), db=db, user_db_id=tenant), COMPLETION_HOOK_PRIMITIVE_KEYS


_BUILDERS = {
    "planner_context": _planner_context,
    "tools_for_run": _tools_for_run_context,
    "completion_hook": _completion_hook_context,
}


@pytest.mark.parametrize("surface", sorted(_BUILDERS))
def test_documented_primitives_survive_the_boundary(surface):
    from AINDY.platform_layer.extension_boundary import sanitize_extension_context

    tenant = uuid.uuid4()
    ctx, primitive_keys = _BUILDERS[surface](tenant, _SessionLike())
    clean = sanitize_extension_context(ctx)

    assert clean["user_id"] == str(tenant), (surface, clean.get("user_id"))
    for key in primitive_keys:
        value = clean.get(key)
        assert not (isinstance(value, dict) and "_redacted_type" in value), (
            f"{surface}: documented key {key!r} reached the hook REDACTED: {value}"
        )
        assert value is None or isinstance(value, (str, int, float, bool)), (surface, key, value)
    assert "db" not in clean, f"{surface}: db crossed the boundary"


@pytest.mark.parametrize("surface", sorted(_BUILDERS))
def test_a_missing_tenant_is_none_not_the_string_none(surface):
    ctx, _ = _BUILDERS[surface](None, None)
    assert ctx["user_id"] is None


def test_the_documented_key_set_is_what_the_provider_builder_produces():
    """Liveness for the census: a key added to the context without being declared primitive
    fails HERE."""
    from AINDY.agents.agent_runtime.shared import (
        PROVIDER_HOOK_PRIMITIVE_KEYS,
        build_provider_hook_context,
    )

    produced = set(build_provider_hook_context("default", user_id=uuid.uuid4(), db=None))
    assert produced == PROVIDER_HOOK_PRIMITIVE_KEYS | {"db"}


def test_a_string_tenant_passes_through_unchanged():
    """The pre-UUID shape (a non-UUID string id) is not altered — `str()` is idempotent on it."""
    from AINDY.agents.agent_runtime.shared import build_provider_hook_context

    assert build_provider_hook_context("t", user_id="legacy-user", db=None)["user_id"] == "legacy-user"


def test_no_hook_call_site_hands_the_registry_an_inline_dict():
    """AST census over agents/agent_runtime/: every call to a sanitizing hook entry point must
    pass a context built by a builder (which the boundary test above covers), never a dict
    literal — that is how both FR-36 and FR-39 got in. Derived and asserted non-empty."""
    call_sites: list[tuple[str, int, str]] = []
    offenders: list[tuple[str, int, str]] = []
    for path in sorted(_AGENT_RUNTIME.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else None
            if name not in _HOOK_ENTRY_POINTS:
                continue
            call_sites.append((path.name, node.lineno, name))
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                if isinstance(arg, ast.Dict):
                    offenders.append((path.name, node.lineno, name))
    assert call_sites, "no hook call sites found — did the entry points move? the census is vacuous"
    assert not offenders, f"hook context built inline at the call site: {offenders}"
