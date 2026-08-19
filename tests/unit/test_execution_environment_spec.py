"""EXEC-ENV-BIND-1 phase 1 — declare, refuse, record.

Design: ``docs/runtime/EXECUTION_ENVIRONMENT_SPEC_DESIGN.md``.

★ **Two of these tests assert an ABSENCE and are vacuous without a liveness control.**
"an undeclared unit is not refused" and "a satisfiable spec is not refused" both pass when the
whole mechanism is unwired — that is `EVENTBUS-COVERAGE-1`'s variant 6, where a first-draft suite
scored 4/7 because an absence-assertion passed with the wire broken. So
``test_liveness_refusal_actually_fires`` runs first and proves refusal happens at all; if it goes
green while the mechanism is dead, everything below it is worthless.

★ **The refusal tests call the caller, not the resolver** (`ROUTE-GUARD-1`). Reading
``resolve_environment`` proves the check was written, not that ``require_execution_unit``'s
documented non-fatal contract lets the answer out — which is the entire risk, since that function
ends in a broad ``except Exception`` that returns ``None``.
"""

from __future__ import annotations

import pytest

from AINDY.core.execution_environment import (
    ASSURANCE_CONTAINER,
    ASSURANCE_INSECURE_DEV,
    ASSURANCE_ORDER,
    ASSURANCE_STRONG,
    Authority,
    ExecutionEnvironmentInvalid,
    ExecutionEnvironmentSpec,
    ExecutionEnvironmentUnsatisfiable,
    Resources,
    Visibility,
    assurance_rank,
    clamp_to_floor,
    resolve_environment,
)

pytestmark = pytest.mark.runtime_only


# ── Liveness control — must run before every absence assertion below ──────────


def test_liveness_refusal_actually_fires(monkeypatch):
    """If this fails, every 'is not refused' assertion in this file is vacuous."""
    monkeypatch.setattr(
        "AINDY.core.execution_environment._host_assurance",
        lambda: (ASSURANCE_INSECURE_DEV, "insecure-dev/no-isolation-guarantee"),
    )
    spec = ExecutionEnvironmentSpec(min_assurance=ASSURANCE_STRONG)

    with pytest.raises(ExecutionEnvironmentUnsatisfiable) as exc:
        resolve_environment(spec)

    assert exc.value.required == ASSURANCE_STRONG
    assert exc.value.available == ASSURANCE_INSECURE_DEV


# ── Assurance ladder ─────────────────────────────────────────────────────────


def test_assurance_order_is_weakest_to_strongest():
    assert ASSURANCE_ORDER == (ASSURANCE_INSECURE_DEV, ASSURANCE_CONTAINER, ASSURANCE_STRONG)
    assert assurance_rank(ASSURANCE_INSECURE_DEV) < assurance_rank(ASSURANCE_CONTAINER)
    assert assurance_rank(ASSURANCE_CONTAINER) < assurance_rank(ASSURANCE_STRONG)


def test_unknown_assurance_ranks_below_everything_known():
    """★ Failing open here would make a typo satisfy every declared minimum."""
    assert assurance_rank("typo-tier") == -1
    assert assurance_rank(None) == -1
    for known in ASSURANCE_ORDER:
        assert assurance_rank("typo-tier") < assurance_rank(known)


def test_an_unrecognised_host_class_never_satisfies_a_minimum(monkeypatch):
    monkeypatch.setattr(
        "AINDY.core.execution_environment._host_assurance", lambda: ("renamed-upstream", "x/y")
    )
    with pytest.raises(ExecutionEnvironmentUnsatisfiable):
        resolve_environment(ExecutionEnvironmentSpec(min_assurance=ASSURANCE_INSECURE_DEV))


def test_assurance_class_names_match_the_provider_side():
    """The literals here are duplicated from platform_layer on purpose; pin them.

    A rename upstream must fail loudly rather than silently reorder the ladder — a reordering
    turns a refusal into an acceptance, which is the one direction that must never happen quietly.
    """
    from AINDY.platform_layer import sandbox_runner as sr

    assert sr.ASSURANCE_CLASS_INSECURE_DEV == ASSURANCE_INSECURE_DEV
    assert sr.ASSURANCE_CLASS_CONTAINER == ASSURANCE_CONTAINER
    assert sr.ASSURANCE_CLASS_STRONG == ASSURANCE_STRONG


# ── Parsing ──────────────────────────────────────────────────────────────────


def test_roundtrip_through_dict_is_lossless():
    spec = ExecutionEnvironmentSpec(
        visibility=Visibility(filesystem="scoped", filesystem_roots=("/srv",), env="allowlist",
                              env_allow=("PATH",)),
        authority=Authority(network="scoped", egress_scope="api.example", subprocess=False),
        resources=Resources(wall_time_ms=1000, memory_bytes=2048, syscalls=10),
        min_assurance=ASSURANCE_CONTAINER,
    )
    assert ExecutionEnvironmentSpec.from_dict(spec.to_dict()) == spec


@pytest.mark.parametrize(
    "payload",
    [
        {"visibility": {"filesystem": "everything"}},
        {"authority": {"network": "wide-open"}},
        {"visibility": {"env": "all"}},
        {"min_assurance": "super-strong"},
        {"resources": {"wall_time_ms": -1}},
        {"resources": {"wall_time_ms": "1000"}},
        {"authority": {"subprocess": "yes"}},
        {"visibility": {"filesystem_roots": "/srv"}},
        {"authority": {"egress_scope": 7}},
        "not-a-dict",
    ],
)
def test_malformed_specs_are_rejected_not_coerced(payload):
    """★ Coercing an unrecognised value to a default silently grants a DIFFERENT environment
    than the one asked for — the exact failure this entry exists to make impossible."""
    with pytest.raises(ExecutionEnvironmentInvalid):
        ExecutionEnvironmentSpec.from_dict(payload)


def test_a_bool_is_not_an_acceptable_int_ceiling():
    """`isinstance(True, int)` is True in Python; the guard must exclude it explicitly."""
    with pytest.raises(ExecutionEnvironmentInvalid):
        ExecutionEnvironmentSpec.from_dict({"resources": {"memory_bytes": True}})


# ── The clamp: narrow-only ───────────────────────────────────────────────────

_RESTRICTIVE_FLOOR = ExecutionEnvironmentSpec(
    visibility=Visibility(filesystem="readonly", env="none"),
    authority=Authority(network="none", subprocess=False),
    resources=Resources(wall_time_ms=5_000, memory_bytes=1_024, syscalls=5),
    min_assurance=ASSURANCE_CONTAINER,
)


def test_a_caller_cannot_widen_past_the_floor():
    """★ The load-bearing safety property. A spec may only ever NARROW."""
    greedy = ExecutionEnvironmentSpec(
        visibility=Visibility(filesystem="host", env="inherit"),
        authority=Authority(network="open", subprocess=True),
        resources=Resources(wall_time_ms=999_999, memory_bytes=999_999, syscalls=999),
        min_assurance=ASSURANCE_INSECURE_DEV,
    )
    effective, widened = clamp_to_floor(greedy, _RESTRICTIVE_FLOOR)

    assert effective.visibility.filesystem == "readonly"
    assert effective.visibility.env == "none"
    assert effective.authority.network == "none"
    assert effective.authority.subprocess is False
    assert effective.resources == Resources(wall_time_ms=5_000, memory_bytes=1_024, syscalls=5)
    # min_assurance narrows UPWARD — a higher floor demands more.
    assert effective.min_assurance == ASSURANCE_CONTAINER

    assert set(widened) == {
        "visibility.filesystem",
        "visibility.env",
        "authority.network",
        "authority.subprocess",
        "resources.wall_time_ms",
        "resources.memory_bytes",
        "resources.syscalls",
    }


def test_a_caller_may_ask_for_more_confinement_than_the_floor():
    strict = ExecutionEnvironmentSpec(
        visibility=Visibility(filesystem="none", env="none"),
        authority=Authority(network="none", subprocess=False),
        resources=Resources(wall_time_ms=10, memory_bytes=10, syscalls=1),
        min_assurance=ASSURANCE_STRONG,
    )
    effective, widened = clamp_to_floor(strict, _RESTRICTIVE_FLOOR)

    assert widened == ()
    assert effective.visibility.filesystem == "none"
    assert effective.resources.wall_time_ms == 10
    assert effective.min_assurance == ASSURANCE_STRONG


def test_every_widening_is_reported_so_the_exposure_is_countable():
    """★ Silently clamping leaves declared and applied differing with nothing recording the gap."""
    spec = ExecutionEnvironmentSpec(authority=Authority(network="open"))
    _, widened = clamp_to_floor(spec, _RESTRICTIVE_FLOOR)
    assert "authority.network" in widened


def test_the_default_floor_is_an_identity_for_well_behaved_callers():
    """A permissive floor is not the clamp being off — it always runs."""
    spec = ExecutionEnvironmentSpec(
        visibility=Visibility(filesystem="scoped"),
        authority=Authority(network="scoped", subprocess=False),
        resources=Resources(wall_time_ms=42),
    )
    effective, widened = clamp_to_floor(spec)
    assert widened == ()
    assert effective == spec


def test_a_null_resource_ceiling_never_narrows_the_floor():
    spec = ExecutionEnvironmentSpec(resources=Resources(wall_time_ms=None))
    effective, widened = clamp_to_floor(spec, _RESTRICTIVE_FLOOR)
    assert effective.resources.wall_time_ms == 5_000
    assert "resources.wall_time_ms" not in widened


# ── Resolution ───────────────────────────────────────────────────────────────


def test_a_satisfiable_spec_resolves_and_records_evidence(monkeypatch):
    monkeypatch.setattr(
        "AINDY.core.execution_environment._host_assurance",
        lambda: (ASSURANCE_STRONG, "strong-sandbox-tier/kernel-observable-verified"),
    )
    resolution = resolve_environment(ExecutionEnvironmentSpec(min_assurance=ASSURANCE_CONTAINER))

    assert resolution.host_assurance == ASSURANCE_STRONG
    assert resolution.evidence_class == "strong-sandbox-tier/kernel-observable-verified"
    assert resolution.declared.min_assurance == ASSURANCE_CONTAINER


def test_host_resolution_failure_reports_the_weakest_class(monkeypatch):
    """★ Failing toward refusal is the only safe direction: a resolution error must not let a
    strict minimum pass."""
    import AINDY.core.execution_environment as mod

    def _boom(*_a, **_k):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(mod, "resolve_sandbox_runner_type", _boom, raising=False)
    monkeypatch.setattr(
        "AINDY.platform_layer.sandbox_runner.resolve_sandbox_runner_type", _boom
    )

    assert mod._host_assurance()[0] == ASSURANCE_INSECURE_DEV
    with pytest.raises(ExecutionEnvironmentUnsatisfiable):
        resolve_environment(ExecutionEnvironmentSpec(min_assurance=ASSURANCE_STRONG))
