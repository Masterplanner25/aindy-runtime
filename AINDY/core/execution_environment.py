"""
ExecutionEnvironmentSpec — the *requesting* half of the isolation contract (EXEC-ENV-BIND-1).

Design: ``docs/runtime/EXECUTION_ENVIRONMENT_SPEC_DESIGN.md``. Read it before changing anything
here; the shape was settled deliberately and the reasoning does not survive in the code.

What this is
------------
The runtime owns a provider abstraction (``SandboxRunner``, three implementations, a certification
ladder) and — until this module — no vocabulary in which an execution unit could *request*
anything from it. ``ExecutionUnit`` stores ``wall_time_ms`` / ``memory_bytes`` / ``syscall_count``,
but those are **measured actuals**. Nothing said what the execution was supposed to be allowed to
do, so *"was this the containment you asked for?"* had no answer for any individual run.

This module is the request record. It does **not** confine anything.

Phase 1 scope — declare, refuse, record
---------------------------------------
============  ==========================================================================
Declare       a caller supplies a spec
Refuse        the unit does not run if the host cannot meet ``min_assurance``
Record        required vs applied vs evidence class, on the ExecutionUnit row
Apply         **NOT HERE.** Each seam applies its own (tool transform, nodus kwargs,
              runner selection). Phase 1 changes no execution path.
============  ==========================================================================

**Do not read a phase-1 row as evidence that an environment was enforced.**
``env_evidence_class`` is what says whether it was.

Three orthogonal axes, not a trust level
----------------------------------------
Adopted from the Linux composition model — namespaces (visibility) + cgroups (resources) +
seccomp/creds (authority) are independent, which is why a caller can express a combination nobody
shipped a preset for. A flat bag of booleans has 2^N states, most meaningless, and cannot say
*"may see a lot, may do very little."* **The decades-tested part is the orthogonality, not the
axis names.**

★ The clamp is the load-bearing safety property
-----------------------------------------------
A caller-supplied spec is attacker-influenced in exactly the way ``AUTHORITY-VALUE-1`` describes.
So the effective spec is the **intersection** of the declared spec and a host floor: a caller may
ask for *more* confinement than the floor and **never for less**.

That asymmetry is what makes phase 1 safe to ship: narrowing-only means this module cannot reduce
confinement below what the host already applies, so the worst failure mode is an over-strict
refusal — which is **loud** — rather than an under-confined run, which is **silent**.

Unlike ``AUTHORITY-VALUE-1``'s clamp, this one is **not behind a flag**. That one shipped opt-in
because a real caller would have been denied by it; no caller supplies a spec today, so there is no
compatibility argument, and a security default that ships off is a pattern this repo has already
paid for repeatedly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Assurance ordering ────────────────────────────────────────────────────────
#
# Imported lazily in _host_assurance() so this module stays importable without dragging
# platform_layer (and its transitive imports) into every consumer. The names are duplicated as
# literals here ON PURPOSE and pinned by test: a rename upstream must fail loudly rather than
# silently reorder the ladder, which would turn a refusal into an acceptance.

ASSURANCE_INSECURE_DEV = "insecure-dev"
ASSURANCE_CONTAINER = "container-grade-sandbox"
ASSURANCE_STRONG = "strong-sandbox-tier"

#: Ordered weakest → strongest. Comparison is by index; an unknown class is treated as
#: weaker than everything known, so an unrecognised host NEVER satisfies a declared minimum.
ASSURANCE_ORDER: tuple[str, ...] = (
    ASSURANCE_INSECURE_DEV,
    ASSURANCE_CONTAINER,
    ASSURANCE_STRONG,
)


def assurance_rank(assurance_class: str | None) -> int:
    """Rank in :data:`ASSURANCE_ORDER`; ``-1`` for unknown/None (weaker than everything).

    ★ Unknown must rank LOW, not high. Ranking an unrecognised value high would make a
    typo satisfy every declared minimum — failing open on the one comparison that gates
    whether work runs at all.
    """
    try:
        return ASSURANCE_ORDER.index(str(assurance_class))
    except ValueError:
        return -1


# ── Axis vocabularies ─────────────────────────────────────────────────────────
#
# Each axis is ordered most-restrictive → least-restrictive, so "narrowing" is a min() over
# the shared order and needs no per-axis special casing.

FS_NONE, FS_READONLY, FS_SCOPED, FS_HOST = "none", "readonly", "scoped", "host"
FILESYSTEM_ORDER = (FS_NONE, FS_READONLY, FS_SCOPED, FS_HOST)

ENV_NONE, ENV_ALLOWLIST, ENV_INHERIT = "none", "allowlist", "inherit"
ENV_ORDER = (ENV_NONE, ENV_ALLOWLIST, ENV_INHERIT)

NET_NONE, NET_SCOPED, NET_OPEN = "none", "scoped", "open"
NETWORK_ORDER = (NET_NONE, NET_SCOPED, NET_OPEN)


class ExecutionEnvironmentError(Exception):
    """Base for environment-binding failures."""


class ExecutionEnvironmentInvalid(ExecutionEnvironmentError):
    """The declared spec is not well-formed. A caller bug, not a policy outcome."""


class ExecutionEnvironmentUnsatisfiable(ExecutionEnvironmentError):
    """The host cannot provide the declared environment, so the unit must not run.

    ★ This exception MUST propagate. ``require_execution_unit`` ends in a broad
    ``except Exception`` that returns ``None`` non-fatally, so an explicit re-raise guard is
    placed **before** it — the same shape ``SyscallContractViolation`` needed in
    ``SyscallDispatcher.dispatch()``.

    A refusal swallowed by a broad handler is **worse than no refusal**, because the recorded row
    says ``refused`` while the work ran.
    """

    def __init__(self, message: str, *, required: str, available: str) -> None:
        super().__init__(message)
        self.required = required
        self.available = available


# ── The spec ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Visibility:
    """What the execution may SEE."""

    filesystem: str = FS_HOST
    filesystem_roots: tuple[str, ...] = ()
    env: str = ENV_INHERIT
    env_allow: tuple[str, ...] = ()


@dataclass(frozen=True)
class Authority:
    """What the execution may DO."""

    network: str = NET_OPEN
    egress_scope: Optional[str] = None
    subprocess: bool = True


@dataclass(frozen=True)
class Resources:
    """How much the execution may USE. ``None`` means "no declared ceiling".

    These are DECLARED ceilings. ``ResourceManager`` owns enforcement and the ExecutionUnit row
    already carries the measured actuals, so declared and actual sit side by side.
    """

    wall_time_ms: Optional[int] = None
    memory_bytes: Optional[int] = None
    syscalls: Optional[int] = None


@dataclass(frozen=True)
class ExecutionEnvironmentSpec:
    """A declarative environment request attached to an ExecutionUnit."""

    visibility: Visibility = field(default_factory=Visibility)
    authority: Authority = field(default_factory=Authority)
    resources: Resources = field(default_factory=Resources)
    min_assurance: str = ASSURANCE_INSECURE_DEV

    # ── serialisation ────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "visibility": {
                "filesystem": self.visibility.filesystem,
                "filesystem_roots": list(self.visibility.filesystem_roots),
                "env": self.visibility.env,
                "env_allow": list(self.visibility.env_allow),
            },
            "authority": {
                "network": self.authority.network,
                "egress_scope": self.authority.egress_scope,
                "subprocess": self.authority.subprocess,
            },
            "resources": {
                "wall_time_ms": self.resources.wall_time_ms,
                "memory_bytes": self.resources.memory_bytes,
                "syscalls": self.resources.syscalls,
            },
            "min_assurance": self.min_assurance,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "ExecutionEnvironmentSpec":
        """Parse and validate. Raises :class:`ExecutionEnvironmentInvalid` on a malformed spec.

        Validation is strict rather than lenient: an unrecognised axis value is a caller bug, and
        coercing it to a default would silently grant a different environment than the one asked
        for — which is the failure this whole entry exists to make impossible.
        """
        if not isinstance(raw, dict):
            raise ExecutionEnvironmentInvalid(f"spec must be a dict, got {type(raw).__name__}")

        vis = raw.get("visibility") or {}
        auth = raw.get("authority") or {}
        res = raw.get("resources") or {}
        if not all(isinstance(section, dict) for section in (vis, auth, res)):
            raise ExecutionEnvironmentInvalid("visibility/authority/resources must be objects")

        def _enum(section: dict[str, Any], key: str, order: tuple[str, ...], default: str) -> str:
            value = section.get(key, default)
            if value not in order:
                raise ExecutionEnvironmentInvalid(
                    f"{key}={value!r} is not one of {', '.join(order)}"
                )
            return str(value)

        def _tuple(section: dict[str, Any], key: str) -> tuple[str, ...]:
            value = section.get(key) or []
            if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
                raise ExecutionEnvironmentInvalid(f"{key} must be a list of strings")
            return tuple(value)

        def _opt_int(section: dict[str, Any], key: str) -> Optional[int]:
            value = section.get(key)
            if value is None:
                return None
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ExecutionEnvironmentInvalid(f"{key} must be a non-negative int or null")
            return value

        min_assurance = raw.get("min_assurance", ASSURANCE_INSECURE_DEV)
        if min_assurance not in ASSURANCE_ORDER:
            raise ExecutionEnvironmentInvalid(
                f"min_assurance={min_assurance!r} is not one of {', '.join(ASSURANCE_ORDER)}"
            )

        egress = auth.get("egress_scope")
        if egress is not None and not isinstance(egress, str):
            raise ExecutionEnvironmentInvalid("egress_scope must be a string or null")

        subprocess_allowed = auth.get("subprocess", True)
        if not isinstance(subprocess_allowed, bool):
            raise ExecutionEnvironmentInvalid("subprocess must be a bool")

        return cls(
            visibility=Visibility(
                filesystem=_enum(vis, "filesystem", FILESYSTEM_ORDER, FS_HOST),
                filesystem_roots=_tuple(vis, "filesystem_roots"),
                env=_enum(vis, "env", ENV_ORDER, ENV_INHERIT),
                env_allow=_tuple(vis, "env_allow"),
            ),
            authority=Authority(
                network=_enum(auth, "network", NETWORK_ORDER, NET_OPEN),
                egress_scope=egress,
                subprocess=subprocess_allowed,
            ),
            resources=Resources(
                wall_time_ms=_opt_int(res, "wall_time_ms"),
                memory_bytes=_opt_int(res, "memory_bytes"),
                syscalls=_opt_int(res, "syscalls"),
            ),
            min_assurance=str(min_assurance),
        )


#: The host floor. Permissive by default — the host imposes no additional ceiling, which is the
#: honest position for a runtime whose default runner reports ``no-isolation-guarantee``.
#:
#: ★ A permissive floor is NOT the clamp being off. The clamp always runs; with this floor it is
#: an identity for well-behaved callers. A deployment that narrows the floor gets refusal-free
#: clamping of any caller that tries to exceed it, and a WARNING for every such attempt.
DEFAULT_HOST_FLOOR = ExecutionEnvironmentSpec()


def host_floor() -> ExecutionEnvironmentSpec:
    """The host-level ceiling a declared spec is intersected against.

    Deliberately a function, not a module constant read at import: module-import-time environment
    reads are invisible to behavioural tests, which this repo has been bitten by three times
    (``FR-10``, ``ResourceManager._get_backend``, the ``AINDY_REDIS_URL`` alias).
    """
    return DEFAULT_HOST_FLOOR


# ── Clamp ─────────────────────────────────────────────────────────────────────


def _narrower(order: tuple[str, ...], declared: str, floor: str) -> tuple[str, bool]:
    """Return (effective, was_widening) — the more restrictive of the two."""
    d, f = order.index(declared), order.index(floor)
    return (order[min(d, f)], d > f)


def _narrower_int(declared: Optional[int], floor: Optional[int]) -> tuple[Optional[int], bool]:
    """Lower ceiling wins. ``None`` means "no ceiling declared", so it never narrows."""
    if declared is None:
        return floor, False
    if floor is None:
        return declared, False
    return (min(declared, floor), declared > floor)


def clamp_to_floor(
    declared: ExecutionEnvironmentSpec,
    floor: Optional[ExecutionEnvironmentSpec] = None,
) -> tuple[ExecutionEnvironmentSpec, tuple[str, ...]]:
    """Intersect ``declared`` with the host floor. Returns (effective, widened_field_names).

    ★ A caller may ask for MORE confinement than the floor and never for less. Every field where
    the caller tried to widen is reported so the exposure is countable — silently clamping would
    leave the caller's stated requirement and the applied one differing with nothing recording the
    gap, which is the problem this module exists to fix.
    """
    floor = floor if floor is not None else host_floor()
    widened: list[str] = []

    fs, w = _narrower(FILESYSTEM_ORDER, declared.visibility.filesystem, floor.visibility.filesystem)
    if w:
        widened.append("visibility.filesystem")
    env_mode, w = _narrower(ENV_ORDER, declared.visibility.env, floor.visibility.env)
    if w:
        widened.append("visibility.env")
    net, w = _narrower(NETWORK_ORDER, declared.authority.network, floor.authority.network)
    if w:
        widened.append("authority.network")

    subprocess_allowed = declared.authority.subprocess and floor.authority.subprocess
    if declared.authority.subprocess and not floor.authority.subprocess:
        widened.append("authority.subprocess")

    wall, w = _narrower_int(declared.resources.wall_time_ms, floor.resources.wall_time_ms)
    if w:
        widened.append("resources.wall_time_ms")
    mem, w = _narrower_int(declared.resources.memory_bytes, floor.resources.memory_bytes)
    if w:
        widened.append("resources.memory_bytes")
    sysc, w = _narrower_int(declared.resources.syscalls, floor.resources.syscalls)
    if w:
        widened.append("resources.syscalls")

    # min_assurance narrows UPWARD: a higher floor demands more, so the effective minimum is the
    # stronger of the two. This is the one axis where "more restrictive" means a larger value.
    if assurance_rank(floor.min_assurance) > assurance_rank(declared.min_assurance):
        effective_assurance = floor.min_assurance
    else:
        effective_assurance = declared.min_assurance

    effective = replace(
        declared,
        visibility=replace(declared.visibility, filesystem=fs, env=env_mode),
        authority=replace(declared.authority, network=net, subprocess=subprocess_allowed),
        resources=Resources(wall_time_ms=wall, memory_bytes=mem, syscalls=sysc),
        min_assurance=effective_assurance,
    )
    return effective, tuple(widened)


# ── Host resolution ───────────────────────────────────────────────────────────


def _host_assurance() -> tuple[str, str]:
    """(assurance_class, evidence_class) for the runner this host would select.

    ★ Imports inside the function: ``AINDY/core`` must not pull ``platform_layer`` at import time,
    and ``runtime_only.py``'s CLI path depends on chains like this staying lazy.

    On any failure this reports the WEAKEST class, so a resolution error cannot make a strict
    ``min_assurance`` pass. Failing toward refusal is the only safe direction here.
    """
    try:
        from AINDY.platform_layer.sandbox_runner import (
            list_supported_sandbox_runners,
            resolve_sandbox_runner_type,
            sandbox_runner_assurance_posture,
        )

        runner_type = resolve_sandbox_runner_type()
        assurance_class = next(
            (
                str(item.get("assurance_class"))
                for item in list_supported_sandbox_runners()
                if item.get("runner_type") == runner_type
            ),
            ASSURANCE_INSECURE_DEV,
        )
        ceiling = str(sandbox_runner_assurance_posture(runner_type).get("assurance_ceiling", ""))
        return assurance_class, f"{assurance_class}/{ceiling}"
    except Exception as exc:  # pragma: no cover - defensive; exercised via monkeypatch in tests
        logger.warning(
            "[ExecEnv] host assurance resolution failed; reporting weakest class: %s", exc
        )
        return ASSURANCE_INSECURE_DEV, f"{ASSURANCE_INSECURE_DEV}/resolution-failed"


@dataclass(frozen=True)
class EnvironmentResolution:
    """The outcome of binding a declared spec to this host."""

    declared: ExecutionEnvironmentSpec
    effective: ExecutionEnvironmentSpec
    evidence_class: str
    host_assurance: str
    widened_fields: tuple[str, ...] = ()


def resolve_environment(
    declared: ExecutionEnvironmentSpec,
    *,
    floor: Optional[ExecutionEnvironmentSpec] = None,
) -> EnvironmentResolution:
    """Clamp, then check the host can meet the effective minimum.

    Raises :class:`ExecutionEnvironmentUnsatisfiable` if it cannot. **The unit must not run.**
    """
    effective, widened = clamp_to_floor(declared, floor)
    if widened:
        logger.warning(
            "[ExecEnv] declared spec attempted to widen beyond the host floor; clamped. "
            "fields=%s",
            ", ".join(widened),
        )

    host_class, evidence_class = _host_assurance()
    if assurance_rank(host_class) < assurance_rank(effective.min_assurance):
        raise ExecutionEnvironmentUnsatisfiable(
            f"host provides assurance {host_class!r}; execution requires at least "
            f"{effective.min_assurance!r}",
            required=effective.min_assurance,
            available=host_class,
        )

    return EnvironmentResolution(
        declared=declared,
        effective=effective,
        evidence_class=evidence_class,
        host_assurance=host_class,
        widened_fields=widened,
    )
