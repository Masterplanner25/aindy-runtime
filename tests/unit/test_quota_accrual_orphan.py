"""QUOTA-ACCRUAL-ORPHAN-1 — a syscall's usage must accrue on a unit somebody reaps.

The entry was filed as "id-less callers share a bucket named ``""`` and lock out after 100
calls". Run through the real entry point, neither half was true: ``make_syscall_ctx_from_tool``
and ``_resolve_trace_context`` both mint a fresh UUID per call, so an id-less caller got a NEW
unit every time — no budget at all — and the ``UsageSnapshot`` for each one was created by the
dispatcher's step-4 accrual and cleared by nothing. 120 calls → 120 snapshots, tenant ``""``,
forever. And "route handlers are fine" was wrong too: the pipeline claims and reaps a unit but
never told the dispatcher which, so ``POST /platform/syscall`` orphaned one snapshot per call
*inside* the pipeline while the request's own unit read ``syscall_count: 0``.

Three mechanisms, each pinned here against the instrument an operator reads:

* a ROOT dispatch that minted its own unit reaps it on return and counts itself
  (``aindy_syscall_unowned_unit_total{syscall}``) — a named or inherited unit is left to its
  owner, which is the liveness control on the purge;
* the pipeline binds its unit into the dispatcher's ContextVars, so a route's dispatches nest
  under the request unit that ``mark_completed`` reaps;
* the in-memory store evicts unreaped snapshots after the TTL the Redis backend already
  applies, counted and logged, never silently.

Plus the off-by-one the bridge would have made live: ``check_quota`` re-decided ADMISSION with
the running unit in the tenant's count, refusing every syscall of the last unit admitted at the
concurrency limit. It never fired because no dispatch snapshot carried a tenant.
"""
from __future__ import annotations

import uuid
from unittest.mock import PropertyMock, patch

import pytest

from AINDY.kernel import resource_manager as rm_mod
from AINDY.kernel import syscall_dispatcher as sd
from AINDY.kernel import syscall_registry
from AINDY.kernel.resource_manager import EU_KEY_TTL_SECONDS, ResourceLimitError, ResourceManager

pytestmark = pytest.mark.runtime_only

_PROBE = "sys.v1.orphanprobe.noop"


def _noop(payload, ctx):
    return {"ok": True}


@pytest.fixture(autouse=True)
def _probe_syscall():
    syscall_registry.SYSCALL_REGISTRY[_PROBE] = syscall_registry.SyscallEntry(
        handler=_noop, capability="orphanprobe.noop", description="probe",
    )
    yield
    syscall_registry.SYSCALL_REGISTRY.pop(_PROBE, None)


@pytest.fixture
def rm(monkeypatch):
    """A fresh ResourceManager wired in as the dispatcher's, so snapshots are observable."""
    fresh = ResourceManager()
    monkeypatch.setattr(sd, "_get_rm", lambda: fresh)
    return fresh


def _unowned_count(syscall: str) -> float:
    from AINDY.platform_layer.metrics import REGISTRY

    value = REGISTRY.get_sample_value("aindy_syscall_unowned_unit_total", {"syscall": syscall})
    return float(value or 0.0)


def _evicted_count() -> float:
    from AINDY.platform_layer.metrics import REGISTRY

    value = REGISTRY.get_sample_value("aindy_resource_usage_evicted_total")
    return float(value or 0.0)


# ── Dispatcher: a minted root unit is reaped by the dispatch that minted it ───────────────


def test_idless_root_dispatch_reaps_its_own_unit(rm):
    """The MCP-server shape: ``dispatch_syscall(name, args, user_id=...)`` and nothing else."""
    before = _unowned_count(_PROBE)
    units = set()
    for _ in range(25):
        result = sd.dispatch_syscall(_PROBE, {}, user_id="u-1")
        assert result["status"] == "success", result
        units.add(result["execution_unit_id"])

    assert len(units) == 25, "each id-less call is its own unit — that is the vacuous-budget half"
    assert rm._usage == {}, f"minted units must not outlive the dispatch: {list(rm._usage)[:3]}"
    assert _unowned_count(_PROBE) == before + 25, (
        "every minted root unit is counted; a zero here with 25 dispatches means the operator "
        "cannot see which callers dispatch without owning a unit"
    )


def test_empty_context_id_at_root_is_also_minted_and_reaped(rm):
    """The memory_router shape: a SyscallContext built with execution_unit_id=""."""
    ctx = syscall_registry.SyscallContext(
        execution_unit_id="", user_id="u-1", capabilities=["orphanprobe.noop"], trace_id="",
    )
    result = sd.get_dispatcher().dispatch(_PROBE, {}, ctx)
    assert result["status"] == "success"
    assert result["execution_unit_id"], "the dispatcher still guarantees an id on the envelope"
    assert rm._usage == {}


def test_named_unit_is_left_to_its_owner(rm):
    """Liveness control for the purge: a unit the CALLER named is not the dispatcher's to reap."""
    run_id = str(uuid.uuid4())
    result = sd.dispatch_syscall(_PROBE, {}, user_id="u-1", execution_unit_id=run_id)
    assert result["status"] == "success"
    assert result["execution_unit_id"] == run_id
    assert run_id in rm._usage, "a named unit's snapshot must survive for its owner to reap"
    assert rm._usage[run_id].syscall_count == 1


def test_nested_dispatch_accrues_on_the_bound_unit_and_leaves_it(rm):
    """What the pipeline bridge relies on: an active unit is inherited, accrued on, not reaped."""
    bound = str(uuid.uuid4())
    tok_t = sd._TRACE_ID_CTX.set(bound)
    tok_e = sd._EU_ID_CTX.set(bound)
    try:
        for _ in range(3):
            # Both id-less shapes nest under the bound unit.
            r1 = sd.dispatch_syscall(_PROBE, {}, user_id="u-1")
            ctx = syscall_registry.SyscallContext(
                execution_unit_id="", user_id="u-1", capabilities=["orphanprobe.noop"], trace_id="",
            )
            r2 = sd.get_dispatcher().dispatch(_PROBE, {}, ctx)
            assert r1["execution_unit_id"] == bound and r2["execution_unit_id"] == bound
            assert r1["trace_id"] == bound and r2["trace_id"] == bound
    finally:
        sd._EU_ID_CTX.reset(tok_e)
        sd._TRACE_ID_CTX.reset(tok_t)

    assert set(rm._usage) == {bound}, "nested dispatches must not mint units of their own"
    assert rm._usage[bound].syscall_count == 6


# ── Pipeline: the request's unit is the one the dispatcher accrues on ──────────────────────


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


def test_route_dispatch_accrues_on_the_request_unit_and_is_reaped(runtime_only_app):
    """Through the booted app (ROUTE-GUARD-1): five calls, zero orphans, one unit per request."""
    from fastapi.testclient import TestClient

    from AINDY.kernel.resource_manager import get_resource_manager
    from AINDY.services.auth_service import get_current_user

    rm = get_resource_manager()
    runtime_only_app.dependency_overrides[get_current_user] = _admin_session
    with TestClient(runtime_only_app, raise_server_exceptions=False) as client:
        before = set(rm._usage)
        for _ in range(5):
            response = client.post(
                "/platform/syscall",
                json={"name": "sys.v1.event.emit", "payload": {"event_type": "orphan.probe", "payload": {}}},
            )
            assert response.status_code == 200, response.text
            envelope = response.json()
            assert envelope["status"] == "success", envelope
            unit = envelope["execution_unit_id"]
            # The syscall ran under the REQUEST's trace — not one of its own (FR-26 extended).
            assert envelope["trace_id"] == response.headers["x-trace-id"]
            # ...and under the request's UNIT: the accrual landed on a snapshot that carries the
            # tenant only mark_started sets, and that the pipeline's mark_completed has already
            # reaped — nothing but the pipeline calls either for a route.
            usage = rm.get_usage(unit)
            assert usage["syscall_count"] == 1, usage
            assert usage["tenant_id"], f"a minted unit has no tenant; this one must: {usage}"
            assert unit in rm._pending_purge, "the request unit is reaped when the request ends"

        # The pipeline reaped every request unit; the sweep drops them; nothing else was made.
        rm.can_execute("sweep-trigger")
        orphans = {k: v.to_dict() for k, v in rm._usage.items() if k not in before}
        assert orphans == {}, f"route dispatches orphaned snapshots: {orphans}"


def test_pipeline_binds_only_when_it_has_both_ids():
    """Binding a trace without a unit would make nested dispatches inherit "" as their unit —
    the bucket-named-"" this entry was filed about — so the bridge refuses half a binding."""
    from types import SimpleNamespace

    from AINDY.core.execution_pipeline.pipeline import ExecutionPipeline

    pipeline = ExecutionPipeline()
    no_unit = SimpleNamespace(metadata={"trace_id": "t-1"})
    assert pipeline._safe_bind_syscall_unit(no_unit) is None
    assert sd._EU_ID_CTX.get() == "" and sd._TRACE_ID_CTX.get() == ""

    both = SimpleNamespace(metadata={"trace_id": "t-1", "eu_id": "eu-1"})
    tokens = pipeline._safe_bind_syscall_unit(both)
    try:
        assert sd._EU_ID_CTX.get() == "eu-1" and sd._TRACE_ID_CTX.get() == "t-1"
    finally:
        pipeline._safe_unbind_syscall_unit(tokens)
    assert sd._EU_ID_CTX.get() == "" and sd._TRACE_ID_CTX.get() == ""


# ── ResourceManager: admission is decided once; unreaped snapshots expire ─────────────────


@pytest.fixture
def enforcing():
    """``is_testing`` is a pydantic PROPERTY — patch it on the class or every quota check
    short-circuits to (True, None) and the test proves nothing (DOCS-* gotcha)."""
    from AINDY.config import Settings

    with patch.object(Settings, "is_testing", new_callable=PropertyMock, return_value=False):
        yield


def test_check_quota_does_not_redecide_admission_for_an_admitted_unit(enforcing):
    manager = ResourceManager()
    limit = manager.MAX_CONCURRENT_PER_TENANT
    for i in range(limit):
        ok, reason = manager.can_execute("tenant-a", f"eu-{i}")
        assert ok, reason
        manager.mark_started("tenant-a", f"eu-{i}")
    # The tenant is now AT its limit, and the last unit admitted is in that count.
    ok, reason = manager.can_execute("tenant-a", "eu-next")
    assert not ok, "control: admission of a further unit is refused at the limit"

    last = f"eu-{limit - 1}"
    manager.record_syscall(last, 1)
    ok, reason = manager.check_quota(last)
    assert ok, f"an admitted unit within its own budget was refused mid-execution: {reason}"

    # Liveness: the same call still enforces the per-unit budget it exists for.
    manager.record_syscall(last, rm_mod.MAX_SYSCALLS_PER_EXECUTION)
    ok, reason = manager.check_quota(last)
    assert not ok and "syscall_count" in (reason or "")


def test_unreaped_snapshots_are_evicted_after_the_redis_ttl(enforcing, caplog):
    manager = ResourceManager()
    manager.record_syscall("old-1", 1)
    manager.record_syscall("old-2", 1)
    manager.record_syscall("fresh", 1)
    # Age two of them past the TTL, and put the sweep clock in the past so it runs now.
    stale_at = manager._usage["fresh"].created_at - EU_KEY_TTL_SECONDS - 1
    manager._usage["old-1"].created_at = stale_at
    manager._usage["old-2"].created_at = stale_at
    manager._last_eviction_sweep = stale_at

    before = _evicted_count()
    with caplog.at_level("WARNING", logger="AINDY.kernel.resource_manager"):
        manager.can_execute("anyone")

    assert set(manager._usage) == {"fresh"}, "only snapshots past the TTL are evicted"
    assert _evicted_count() == before + 2, "an eviction is counted, never silent"
    assert any("evicted 2 usage snapshot" in rec.getMessage() for rec in caplog.records)


def test_eviction_sweep_is_rate_limited(enforcing):
    manager = ResourceManager()
    manager.record_syscall("old", 1)
    manager._usage["old"].created_at -= EU_KEY_TTL_SECONDS + 1
    # The sweep ran at construction; a second one within the interval must not scan.
    manager.can_execute("anyone")
    assert "old" in manager._usage, "a full scan on every admission check would be O(n) per call"


def test_reaped_units_are_still_purged_on_the_next_admission_check(enforcing):
    """The pending-purge half of the sweep is unchanged: mark_completed defers, can_execute drops."""
    manager = ResourceManager()
    manager.mark_started("t", "eu-1")
    manager.record_syscall("eu-1", 3)
    manager.mark_completed("t", "eu-1")
    assert manager.get_usage("eu-1")["syscall_count"] == 3, "final usage readable after completion"
    manager.can_execute("t")
    assert "eu-1" not in manager._usage


def test_owned_execution_admits_starts_and_reaps(enforcing):
    manager = ResourceManager()
    with manager.owned_execution("tenant-b") as eu_id:
        assert eu_id in manager._usage and manager._usage[eu_id].tenant_id == "tenant-b"
        assert manager.get_tenant_active("tenant-b") == 1
        manager.record_syscall(eu_id, 1)
    assert manager.get_tenant_active("tenant-b") == 0
    assert eu_id in manager._pending_purge, "reaped on exit — dropped by the next sweep"
    manager.can_execute("tenant-b")
    assert eu_id not in manager._usage


def test_owned_execution_refuses_at_the_concurrency_limit_and_reaps_on_error(enforcing):
    manager = ResourceManager()
    for i in range(manager.MAX_CONCURRENT_PER_TENANT):
        manager.mark_started("tenant-c", f"held-{i}")
    with pytest.raises(ResourceLimitError, match="concurrent limit"):
        with manager.owned_execution("tenant-c"):
            pytest.fail("the block must not run when admission is refused")

    manager.reset()
    with pytest.raises(RuntimeError, match="inside"):
        with manager.owned_execution("tenant-d") as eu_id:
            raise RuntimeError("inside")
    assert manager.get_tenant_active("tenant-d") == 0
    assert eu_id in manager._pending_purge


# ── MCP server: a transport with no pipeline owns a unit per call ─────────────────────────


def test_mcp_handler_owns_a_unit_per_call(monkeypatch):
    pytest.importorskip("nodus_mcp_aindy")
    from AINDY.platform_layer import mcp_server

    fresh = ResourceManager()
    monkeypatch.setattr(rm_mod, "_RESOURCE_MANAGER", fresh)
    registry = mcp_server.build_registry("user-mcp", ["sys.v1.memory.read"])
    tool = registry.get(registry.names()[0])

    seen: dict = {}

    def _fake_dispatch(name, args, **kwargs):
        seen.update(kwargs)
        # Mid-call the unit is started under the identity — the subject the quota will read.
        seen["active_mid_call"] = fresh.get_tenant_active("user-mcp")
        seen["tenant_mid_call"] = fresh._usage[kwargs["execution_unit_id"]].tenant_id
        return {"status": "success", "data": {}}

    with patch("AINDY.kernel.syscall_dispatcher.dispatch_syscall", side_effect=_fake_dispatch):
        assert tool.handler({"path": "/x/**"})["status"] == "success"

    assert seen["user_id"] == "user-mcp"
    assert seen["execution_unit_id"], "the call must name the unit it owns"
    assert seen["active_mid_call"] == 1 and seen["tenant_mid_call"] == "user-mcp"
    assert fresh.get_tenant_active("user-mcp") == 0, "reaped when the call returned"
    assert seen["execution_unit_id"] in fresh._pending_purge


def test_mcp_handler_refuses_in_the_dispatcher_envelope_shape(monkeypatch):
    pytest.importorskip("nodus_mcp_aindy")
    from AINDY.platform_layer import mcp_server

    fresh = ResourceManager()
    monkeypatch.setattr(rm_mod, "_RESOURCE_MANAGER", fresh)
    monkeypatch.setattr(
        fresh, "can_execute", lambda tenant_id, eu_id=None: (False, "RESOURCE_LIMIT_EXCEEDED: test"),
    )
    registry = mcp_server.build_registry("user-mcp", ["sys.v1.memory.read"])
    tool = registry.get(registry.names()[0])

    with patch("AINDY.kernel.syscall_dispatcher.dispatch_syscall") as dispatch:
        result = tool.handler({"path": "/x/**"})

    dispatch.assert_not_called()
    assert result["status"] == "error" and "RESOURCE_LIMIT_EXCEEDED" in result["error"]
    assert result["syscall"] == "sys.v1.memory.read" and result["version"] == "v1"
    assert fresh.get_tenant_active("user-mcp") == 0, "a refused call starts nothing"
