"""Behavioural suite for the OS isolation layer — TenantContext + ResourceManager.

Closes part of the OS-isolation half of DOCS-COVERAGE-CLAIM-1: `OS_ISOLATION_LAYER.md`
cited `tests/unit/test_os_layer.py` — this path — which had never existed.

`OS_ISOLATION_LAYER.md` defines the layer as TenantContext (the isolation boundary),
ResourceManager (quota) and SchedulerEngine (priority + WAIT/RESUME). The first two are
covered here; the WAIT/RESUME propagation half lives in `test_event_bus.py`.

Two properties are asserted as they *currently are*, with the surprise named:

* `TenantContext`'s immutability used to be shallow — `frozen=True` blocks attribute
  rebinding but not in-place mutation of a `list` field (TENANT-FROZEN-SHALLOW-1, fixed
  2026-08-15; the field is now a tuple). See TestImmutabilityIsDeep.
* `ResourceManager.can_execute` returns `(True, None)` unconditionally under
  `settings.is_testing`, so quota enforcement is vacuous in the test environment and
  must be exercised with that flag patched off.

Marked `runtime_only` — without it CI collects nothing here (CI-MARKER-1).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from AINDY.kernel import resource_manager as rm_module
from AINDY.kernel.resource_manager import ResourceManager
from AINDY.kernel.tenant_context import (
    RESOURCE_LIMIT_EXCEEDED,
    TENANT_VIOLATION,
    TenantContext,
    build_tenant_context,
    tenant_context_from_syscall_context,
)

pytestmark = pytest.mark.runtime_only


# ── TenantContext: construction ───────────────────────────────────────────────


class TestBuildTenantContext:
    def test_tenant_id_defaults_to_user_id(self):
        ctx = build_tenant_context("user-123")
        assert ctx.tenant_id == "user-123"
        assert ctx.user_id == "user-123"

    def test_namespace_is_derived_from_tenant(self):
        assert build_tenant_context("user-123").namespace == "tenant:user-123"

    def test_explicit_tenant_overrides_user_id(self):
        ctx = build_tenant_context("user-123", tenant_id="org-9")
        assert ctx.tenant_id == "org-9"
        assert ctx.user_id == "user-123"
        assert ctx.namespace == "tenant:org-9"

    def test_capability_scope_defaults_to_empty(self):
        assert build_tenant_context("u1").capability_scope == ()

    def test_capability_scope_is_copied_not_aliased(self):
        caps = ["memory.read"]
        ctx = build_tenant_context("u1", caps)
        caps.append("memory.write")
        assert ctx.capability_scope == ("memory.read",)

    def test_any_iterable_is_accepted_and_stored_as_a_tuple(self):
        """Callers pass lists today; the field is a tuple (TENANT-FROZEN-SHALLOW-1)."""
        for supplied in (["a", "b"], ("a", "b"), iter(["a", "b"])):
            ctx = build_tenant_context("u1", supplied)
            assert ctx.capability_scope == ("a", "b")
            assert isinstance(ctx.capability_scope, tuple)

    def test_non_string_ids_are_coerced(self):
        ctx = build_tenant_context(12345)
        assert ctx.tenant_id == "12345"

    def test_empty_user_id_produces_a_degenerate_prefix(self):
        """No guard rejects an empty tenant; the prefix collapses to `/memory//`."""
        ctx = build_tenant_context("")
        assert ctx.tenant_id == ""
        assert ctx.memory_prefix() == "/memory//"
        assert ctx.validate_memory_path("/memory//anything") is True


class TestTenantContextFromSyscallContext:
    def test_derives_tenant_and_capabilities(self):
        class FakeSyscallCtx:
            user_id = "u1"
            capabilities = ["memory.read", "memory.write"]

        ctx = tenant_context_from_syscall_context(FakeSyscallCtx())
        assert ctx.tenant_id == "u1"
        assert ctx.capability_scope == ("memory.read", "memory.write")

    def test_missing_capabilities_attribute_is_tolerated(self):
        class Bare:
            user_id = "u1"

        assert tenant_context_from_syscall_context(Bare()).capability_scope == ()

    def test_none_capabilities_becomes_empty_scope(self):
        class NoneCaps:
            user_id = "u1"
            capabilities = None

        assert tenant_context_from_syscall_context(NoneCaps()).capability_scope == ()


# ── TenantContext: the isolation boundary ─────────────────────────────────────


class TestMemoryPathIsolation:
    @pytest.fixture
    def ctx(self):
        return build_tenant_context("t1", ["memory.read"])

    def test_own_namespace_is_allowed(self, ctx):
        assert ctx.validate_memory_path("/memory/t1/node-abc") is True
        ctx.assert_memory_path("/memory/t1/node-abc")

    def test_other_tenant_is_refused(self, ctx):
        assert ctx.validate_memory_path("/memory/t2/node-xyz") is False
        with pytest.raises(PermissionError, match=TENANT_VIOLATION):
            ctx.assert_memory_path("/memory/t2/node-xyz")

    def test_sibling_tenant_sharing_a_prefix_is_refused(self, ctx):
        """`t1` must not authorize `t12` — the prefix's trailing slash is load-bearing."""
        assert ctx.validate_memory_path("/memory/t12/node") is False

    def test_bare_root_is_refused(self, ctx):
        assert ctx.validate_memory_path("/memory/") is False

    def test_exact_tenant_root_without_trailing_slash_is_refused(self, ctx):
        """Worth knowing: this guard and MAS's `validate_tenant_path` disagree here.

        `TenantContext.validate_memory_path` requires the trailing slash, so the exact
        tenant root fails; `memory_address_space.validate_tenant_path` accepts the
        exact form. Two tenant guards, two answers for the same string.
        """
        assert ctx.validate_memory_path("/memory/t1") is False

        from AINDY.memory.memory_address_space import validate_tenant_path

        validate_tenant_path("/memory/t1", "t1")  # the other guard allows it

    def test_a_path_in_another_root_is_refused(self, ctx):
        assert ctx.validate_memory_path("/other/t1/node") is False

    def test_error_message_names_both_path_and_namespace(self, ctx):
        with pytest.raises(PermissionError) as excinfo:
            ctx.assert_memory_path("/memory/t2/x")
        message = str(excinfo.value)
        assert "/memory/t2/x" in message and "/memory/t1/" in message


class TestCrossTenantGuard:
    def test_same_tenant_passes(self):
        build_tenant_context("t1").assert_same_tenant("t1")

    def test_different_tenant_raises(self):
        with pytest.raises(PermissionError, match=TENANT_VIOLATION):
            build_tenant_context("t1").assert_same_tenant("t2")

    def test_comparison_is_string_based_so_types_do_not_matter(self):
        build_tenant_context("123").assert_same_tenant(123)


class TestCapabilityGuard:
    @pytest.fixture
    def ctx(self):
        return build_tenant_context("t1", ["memory.read"])

    def test_granted_capability_passes(self, ctx):
        assert ctx.has_capability("memory.read") is True
        ctx.assert_capability("memory.read")

    def test_ungranted_capability_raises(self, ctx):
        assert ctx.has_capability("memory.write") is False
        with pytest.raises(PermissionError, match=TENANT_VIOLATION):
            ctx.assert_capability("memory.write")

    def test_matching_is_exact_not_prefix(self, ctx):
        assert ctx.has_capability("memory") is False
        assert ctx.has_capability("memory.read.extra") is False

    def test_empty_scope_grants_nothing(self):
        with pytest.raises(PermissionError):
            build_tenant_context("t1").assert_capability("memory.read")


class TestImmutabilityIsDeep:
    """TENANT-FROZEN-SHALLOW-1 — FIXED 2026-08-15.

    `frozen=True` blocks attribute *rebinding*; it does not deep-freeze. While
    `capability_scope` was a `list`, a capability could be appended to a live
    security context that the type documents as "Immutable" — and
    `assert_capability` would then pass for it. The field is now a tuple.
    """

    def test_attribute_rebinding_is_blocked(self):
        ctx = build_tenant_context("t1", ["memory.read"])
        with pytest.raises(Exception) as excinfo:  # dataclasses.FrozenInstanceError
            ctx.tenant_id = "t2"
        assert "FrozenInstance" in type(excinfo.value).__name__

    def test_capability_scope_cannot_be_mutated_in_place(self):
        """The regression itself. Was: append succeeded and the guard then passed."""
        ctx = build_tenant_context("t1", ["memory.read"])
        with pytest.raises(AttributeError):
            ctx.capability_scope.append("admin.everything")
        assert ctx.has_capability("admin.everything") is False
        with pytest.raises(PermissionError):
            ctx.assert_capability("admin.everything")

    def test_the_field_is_a_tuple_not_a_list(self):
        assert isinstance(build_tenant_context("t1", ["a"]).capability_scope, tuple)

    def test_direct_construction_defaults_to_an_empty_tuple(self):
        """Covers the dataclass default itself (`field(default_factory=tuple)`), not
        just the builder — a context built without the helper must be frozen too."""
        ctx = TenantContext(tenant_id="t1", user_id="t1", namespace="tenant:t1")
        assert ctx.capability_scope == ()
        with pytest.raises(AttributeError):
            ctx.capability_scope.append("admin.everything")

    def test_reading_the_scope_is_unchanged(self):
        """`in`, `len` and iteration must still work — only mutation differs."""
        ctx = build_tenant_context("t1", ["memory.read", "memory.write"])
        assert "memory.read" in ctx.capability_scope
        assert len(ctx.capability_scope) == 2
        assert list(ctx.capability_scope) == ["memory.read", "memory.write"]
        assert "caps=2" in repr(ctx)


class TestKernelPackageExportsOneClass:
    """`AINDY/kernel/__init__.py` was a byte-identical copy of `tenant_context.py`
    (171 lines, present since the initial extraction), so the package root defined a
    *second* `TenantContext`. `isinstance` across the two silently failed, and a fix
    applied to one would not reach the other — which is exactly how it surfaced,
    fixing TENANT-FROZEN-SHALLOW-1.
    """

    def test_package_root_and_submodule_yield_the_same_class(self):
        from AINDY.kernel import TenantContext as FromPackage
        from AINDY.kernel.tenant_context import TenantContext as FromModule

        assert FromPackage is FromModule

    def test_an_instance_is_recognised_through_either_import_path(self):
        from AINDY.kernel import TenantContext as FromPackage
        from AINDY.kernel.tenant_context import TenantContext as FromModule

        ctx = build_tenant_context("t1")
        assert isinstance(ctx, FromPackage)
        assert isinstance(ctx, FromModule)

    def test_the_package_root_no_longer_redefines_the_class(self):
        """A re-export has no `class` statement of its own."""
        import inspect

        import AINDY.kernel as pkg

        assert "class TenantContext" not in inspect.getsource(pkg)

    def test_submodule_access_is_not_shadowed_by_the_re_export(self):
        """The `AINDY.routes` hazard in CLAUDE.md: exporting a name that collides with
        a submodule makes `from pkg import submodule` return the wrong object. None of
        the exported names collide, so this must still be the module."""
        from AINDY.kernel import tenant_context as maybe_module

        assert inspect_is_module(maybe_module)


def inspect_is_module(obj) -> bool:
    import types

    return isinstance(obj, types.ModuleType)


def test_error_codes_are_distinct_constants():
    assert TENANT_VIOLATION == "TENANT_VIOLATION"
    assert RESOURCE_LIMIT_EXCEEDED == "RESOURCE_LIMIT_EXCEEDED"
    assert TENANT_VIOLATION != RESOURCE_LIMIT_EXCEEDED


# ── ResourceManager ───────────────────────────────────────────────────────────


@pytest.fixture
def manager():
    """A ResourceManager with no Redis backend and no cross-test residue."""
    with patch.object(rm_module, "_get_backend", return_value=None):
        mgr = ResourceManager()
    mgr._redis = None
    mgr._get_redis = lambda: None
    yield mgr
    mgr.reset()


@pytest.fixture
def enforcing(manager):
    """`can_execute` short-circuits to allow under `settings.is_testing`; patch it off
    so the quota path is actually reachable.

    `is_testing` is a pydantic *property*, so it has to be patched on the class —
    `patch.object(settings, "is_testing", False)` raises AttributeError.
    """
    with patch.object(
        type(rm_module.settings), "is_testing", property(lambda _self: False)
    ):
        yield manager


class TestQuotaIsVacuousInTests:
    def test_can_execute_always_allows_while_is_testing(self, manager):
        """Recorded because it silently defeats any naive quota test: the check
        returns before reading a single counter."""
        for _ in range(manager.MAX_CONCURRENT_PER_TENANT * 5):
            manager.mark_started("t1")
        assert manager.can_execute("t1") == (True, None)


class TestConcurrencyAccounting:
    def test_starts_at_zero(self, manager):
        assert manager.get_tenant_active("t1") == 0

    def test_mark_started_increments(self, manager):
        manager.mark_started("t1", "eu-1")
        assert manager.get_tenant_active("t1") == 1

    def test_mark_completed_decrements(self, manager):
        manager.mark_started("t1", "eu-1")
        manager.mark_completed("t1", "eu-1")
        assert manager.get_tenant_active("t1") == 0

    def test_counters_never_go_negative(self, manager):
        manager.mark_completed("t1", "eu-unknown")
        assert manager.get_tenant_active("t1") >= 0

    def test_tenants_are_counted_independently(self, manager):
        manager.mark_started("t1", "eu-1")
        manager.mark_started("t1", "eu-2")
        manager.mark_started("t2", "eu-3")
        assert manager.get_tenant_active("t1") == 2
        assert manager.get_tenant_active("t2") == 1

    def test_reset_tenant_quota_clears_only_that_tenant(self, manager):
        manager.mark_started("t1", "eu-1")
        manager.mark_started("t2", "eu-2")
        manager.reset_tenant_quota("t1")
        assert manager.get_tenant_active("t1") == 0
        assert manager.get_tenant_active("t2") == 1

    def test_anonymous_executions_still_count_toward_the_tenant(self, manager):
        manager.mark_started("t1", None)
        assert manager.get_tenant_active("t1") == 1


class TestConcurrencyLimit:
    def test_allows_up_to_the_ceiling(self, enforcing):
        for i in range(enforcing.MAX_CONCURRENT_PER_TENANT):
            allowed, reason = enforcing.can_execute("t1", f"eu-{i}")
            assert allowed is True, reason
            enforcing.mark_started("t1", f"eu-{i}")

    def test_refuses_past_the_ceiling_with_a_reason(self, enforcing):
        for i in range(enforcing.MAX_CONCURRENT_PER_TENANT):
            enforcing.mark_started("t1", f"eu-{i}")
        allowed, reason = enforcing.can_execute("t1", "eu-over")
        assert allowed is False
        assert reason and isinstance(reason, str)

    def test_one_tenant_hitting_the_ceiling_does_not_block_another(self, enforcing):
        for i in range(enforcing.MAX_CONCURRENT_PER_TENANT):
            enforcing.mark_started("t1", f"eu-{i}")
        assert enforcing.can_execute("t1", "eu-x")[0] is False
        assert enforcing.can_execute("t2", "eu-y")[0] is True

    def test_completing_an_execution_frees_a_slot(self, enforcing):
        for i in range(enforcing.MAX_CONCURRENT_PER_TENANT):
            enforcing.mark_started("t1", f"eu-{i}")
        assert enforcing.can_execute("t1", "eu-x")[0] is False
        enforcing.mark_completed("t1", "eu-0")
        assert enforcing.can_execute("t1", "eu-x")[0] is True


class TestUsageTracking:
    def test_usage_is_isolated_per_execution_unit(self, manager):
        manager.mark_started("t1", "eu-1")
        manager.mark_started("t1", "eu-2")
        manager.record_syscall("eu-1")
        manager.record_syscall("eu-1")
        manager.record_syscall("eu-2")
        assert manager.get_usage("eu-1")["syscall_count"] == 2
        assert manager.get_usage("eu-2")["syscall_count"] == 1

    def test_record_cpu_accumulates_into_wall_time_ms(self, manager):
        """`record_cpu` writes `wall_time_ms`, not a `cpu_ms` field — the CPU naming
        survives only in the `AINDY_QUOTA_CPU_MS` env var, kept for operator
        compatibility. There is no separate CPU counter."""
        manager.mark_started("t1", "eu-1")
        manager.record_cpu("eu-1", 100)
        manager.record_cpu("eu-1", 50)
        usage = manager.get_usage("eu-1")
        assert usage["wall_time_ms"] == 150
        assert "cpu_ms" not in usage

    def test_memory_records_the_high_water_mark_not_the_last_value(self, manager):
        manager.mark_started("t1", "eu-1")
        manager.record_memory("eu-1", 5000)
        manager.record_memory("eu-1", 1000)
        assert manager.get_usage("eu-1")["memory_bytes"] == 5000

    def test_usage_for_an_unknown_eu_is_empty_not_an_error(self, manager):
        assert isinstance(manager.get_usage("never-seen"), dict)


class TestBackendSelectionCaching:
    """`_get_backend()` latches on `_RESOURCE_BACKEND_INITIALIZED`, so the
    Redis-vs-in-process choice is made once per process and a later `REDIS_URL`
    has no effect. This is the exact shape CLAUDE.md flags under
    "module-import-time env reads are invisible to behavioural tests" — only a
    reload-based or global-resetting test can see it."""

    def test_backend_choice_is_cached_after_the_first_call(self, monkeypatch):
        monkeypatch.setattr(rm_module, "_RESOURCE_BACKEND", None)
        monkeypatch.setattr(rm_module, "_RESOURCE_BACKEND_INITIALIZED", False)
        monkeypatch.delenv("REDIS_URL", raising=False)

        assert rm_module._get_backend() is None
        assert rm_module._RESOURCE_BACKEND_INITIALIZED is True

        # setting REDIS_URL afterwards must not change the decision
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        assert rm_module._get_backend() is None

    def test_test_mode_forces_the_in_process_backend(self, monkeypatch):
        monkeypatch.setattr(rm_module, "_RESOURCE_BACKEND", None)
        monkeypatch.setattr(rm_module, "_RESOURCE_BACKEND_INITIALIZED", False)
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setenv("TEST_MODE", "1")

        assert rm_module._get_backend() is None
