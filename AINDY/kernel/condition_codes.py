from __future__ import annotations

from enum import Enum


class ConditionClassification(str, Enum):
    """Severity tier for a runtime condition. Inherits str so values compare equal to raw strings."""

    SAFE_DEGRADED = "safe_degraded"
    UNSAFE_DEGRADED = "unsafe_degraded"
    STARTUP_FATAL = "startup_fatal"


class RuntimeConditionCode(str, Enum):
    """
    Stable operator-facing condition codes emitted by set_api_runtime_condition().

    These codes appear in /ready (required_failures list) and /health responses.
    Each code is associated with a ConditionClassification that determines whether it
    blocks readiness.

    Stable surface — removing or renaming a value requires a MAJOR version bump.
    See docs/runtime/CONDITION_CODES.md for descriptions and classification.
    """

    # Extension override
    EXTERNAL_PYTHON_OVERRIDE_ENABLED = "external_python_override_enabled"

    # Redis / event bus
    REDIS_SINGLE_INSTANCE_MODE = "redis_single_instance_mode"
    EVENT_BUS_LOCAL_ONLY = "event_bus_local_only"
    EVENT_BUS_REHYDRATION_DRAIN_FAILED = "event_bus_rehydration_drain_failed"
    EVENT_BUS_SUBSCRIBER_UNAVAILABLE = "event_bus_subscriber_unavailable"
    DISTRIBUTED_WORKER_UNAVAILABLE = "distributed_worker_unavailable"
    QUEUE_BACKEND_FALLBACK = "queue_backend_fallback"

    # MongoDB
    MONGO_REQUIRED_UNAVAILABLE = "mongo_required_unavailable"
    MONGO_OPTIONAL_UNAVAILABLE = "mongo_optional_unavailable"

    # Registry / rehydration
    DYNAMIC_REGISTRY_RESTORE_INCOMPLETE = "dynamic_registry_restore_incomplete"
    DYNAMIC_REGISTRY_RESTORE_FAILED = "dynamic_registry_restore_failed"
    WAIT_EUS_REHYDRATION_FAILED = "wait_eus_rehydration_failed"
    FLOW_RUN_REHYDRATION_FAILED = "flow_run_rehydration_failed"
    AGENT_RUN_REHYDRATION_FAILED = "agent_run_rehydration_failed"


class ReadinessBlockerCode(str, Enum):
    """
    Stable codes that appear in the required_failures list of /ready responses.

    A non-empty required_failures list means status="not_ready" (HTTP 503).
    The codes are additive — multiple can be present simultaneously.

    Stable surface — see docs/runtime/CONDITION_CODES.md.
    """

    STARTUP_INCOMPLETE = "startup_incomplete"
    POSTGRES = "postgres"
    SCHEMA = "schema"
    REDIS = "redis"
    QUEUE = "queue"
    EVENT_BUS = "event_bus"
    WORKER = "worker"
    SCHEDULER = "scheduler"
    PLUGIN_HOSTS = "plugin_hosts"
    PLUGIN_SANDBOX_ATTESTATION = "plugin_sandbox_attestation"


class SyscallResponseStatus(str, Enum):
    """Top-level status field in every SyscallDispatcher response envelope."""

    SUCCESS = "success"
    ERROR = "error"


class FlowRunStatus(str, Enum):
    """
    Lifecycle states for a FlowRun entity.

    Stable surface — returned in flow execution API responses.

    Active states: RUNNING (freshly started), EXECUTING (claimed off a WAIT for a
    resume, or actively stepping). Suspendable state: WAITING (WAIT node entered;
    resumes on matching event). Terminal states: SUCCESS, FAILED.

    RTR-3 (2026-07-08): this enum now mirrors the values the flow engine actually
    writes. Terminal success is ``SUCCESS`` (``runner_completion``); ``EXECUTING``
    is written by the resume claim (``runner_steps`` / ``flow_run_rehydration``).
    ``COMPLETED`` is a legacy alias the runtime no longer writes — retained so the
    stable enum surface is not narrowed. Use ``FLOW_TERMINAL_STATUSES`` /
    ``is_flow_terminal`` rather than comparing against a single literal.
    """

    RUNNING = "running"
    EXECUTING = "executing"
    WAITING = "waiting"
    SUCCESS = "success"
    FAILED = "failed"
    # Legacy alias — never written by the runtime; terminal success is SUCCESS.
    COMPLETED = "completed"


class AgentRunStatus(str, Enum):
    """
    Lifecycle states for an AgentRun entity.

    Stable surface — returned in agent execution API responses.
    Terminal states: COMPLETED, FAILED, CANCELLED, VERIFY_FAILED.
    Intermediate: DELEGATED (sub-agent dispatched; not yet terminal),
    WAITING (parked mid-plan on a WAIT step; resumes on the matching event).

    RTR-3 (2026-07-08): WAITING added to mirror the value the VM-backed path
    actually writes when parking a run (``AgentRun.wait_state`` is set alongside
    it) and that ``agent_run_rehydration`` queries on restart.

    CANCELLED (AGENT-HARDEN-1): operator-driven terminal state set by
    ``sys.v1.agent.cancel``. A non-terminal run is flipped to CANCELLED via an
    atomic CAS; the VM-backed segment chain observes it at the next segment
    boundary and halts before the next tool call.

    VERIFY_FAILED (AGENT-HARDEN-6): a run whose plan ran to completion but whose
    declared post-conditions did not hold. The Verifier stage marks it terminal
    and rolls back its reversible effects (AGENT-HARDEN-3 compensators).
    """

    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    WAITING = "waiting"
    DELEGATED = "delegated"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    VERIFY_FAILED = "verify_failed"


# ── Run-status classification (RTR-3) ─────────────────────────────────────────
#
# Single source of truth for "is this run finished?" and for mapping the two
# lifecycles onto each other. An AgentRun mirrors a FlowRun (RTR-3: the FlowRun is
# the de-facto execution authority; the AgentRun is a projection linked by a
# nullable ``flow_run_id``). Recovery/reconciliation code must classify status via
# these helpers instead of hardcoding a single literal — the historical
# ``status != "executing"`` guard silently no-op'd recovery for any run parked in
# ``delegated`` / ``waiting`` / ``approved``, stranding it forever.

AGENT_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        AgentRunStatus.COMPLETED.value,
        AgentRunStatus.FAILED.value,
        AgentRunStatus.CANCELLED.value,
        AgentRunStatus.VERIFY_FAILED.value,
    }
)
"""AgentRun states that are final — recovery leaves these untouched."""

FLOW_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        FlowRunStatus.SUCCESS.value,
        FlowRunStatus.FAILED.value,
        # Legacy terminal literal — never written by the runtime, honored for reads.
        FlowRunStatus.COMPLETED.value,
    }
)
"""FlowRun states that are final — recovery leaves these untouched."""

AGENT_ACTIVE_STATUSES: frozenset[str] = frozenset(
    {
        AgentRunStatus.PENDING_APPROVAL.value,
        AgentRunStatus.APPROVED.value,
        AgentRunStatus.EXECUTING.value,
        AgentRunStatus.DELEGATED.value,
        AgentRunStatus.WAITING.value,
    }
)
"""AgentRun states that are not terminal — a stuck one of these is recoverable."""

FLOW_ACTIVE_STATUSES: frozenset[str] = frozenset(
    {
        FlowRunStatus.RUNNING.value,
        FlowRunStatus.EXECUTING.value,
        FlowRunStatus.WAITING.value,
    }
)
"""FlowRun states that are not terminal."""

# Deterministic terminal correspondence between the two lifecycles. Used by
# reconcilers that must reflect one side's terminal state onto the other.
_FLOW_TO_AGENT_STATUS: dict[str, str] = {
    FlowRunStatus.RUNNING.value: AgentRunStatus.EXECUTING.value,
    FlowRunStatus.EXECUTING.value: AgentRunStatus.EXECUTING.value,
    FlowRunStatus.WAITING.value: AgentRunStatus.WAITING.value,
    FlowRunStatus.SUCCESS.value: AgentRunStatus.COMPLETED.value,
    FlowRunStatus.COMPLETED.value: AgentRunStatus.COMPLETED.value,
    FlowRunStatus.FAILED.value: AgentRunStatus.FAILED.value,
}

_AGENT_TO_FLOW_STATUS: dict[str, str] = {
    AgentRunStatus.PENDING_APPROVAL.value: FlowRunStatus.RUNNING.value,
    AgentRunStatus.APPROVED.value: FlowRunStatus.RUNNING.value,
    AgentRunStatus.EXECUTING.value: FlowRunStatus.EXECUTING.value,
    AgentRunStatus.DELEGATED.value: FlowRunStatus.EXECUTING.value,
    AgentRunStatus.WAITING.value: FlowRunStatus.WAITING.value,
    AgentRunStatus.COMPLETED.value: FlowRunStatus.SUCCESS.value,
    AgentRunStatus.FAILED.value: FlowRunStatus.FAILED.value,
    AgentRunStatus.CANCELLED.value: FlowRunStatus.FAILED.value,
    AgentRunStatus.VERIFY_FAILED.value: FlowRunStatus.FAILED.value,
}


def is_agent_terminal(status: str | None) -> bool:
    """True when an AgentRun status is final (safe to leave to recovery)."""
    return status in AGENT_TERMINAL_STATUSES


def is_flow_terminal(status: str | None) -> bool:
    """True when a FlowRun status is final."""
    return status in FLOW_TERMINAL_STATUSES


def flow_status_to_agent(flow_status: str | None) -> str:
    """Map a FlowRun status onto its AgentRun equivalent (defaults to failed)."""
    if flow_status is None:
        return AgentRunStatus.FAILED.value
    return _FLOW_TO_AGENT_STATUS.get(flow_status, AgentRunStatus.FAILED.value)


def agent_status_to_flow(agent_status: str | None) -> str:
    """Map an AgentRun status onto its FlowRun equivalent (defaults to failed)."""
    if agent_status is None:
        return FlowRunStatus.FAILED.value
    return _AGENT_TO_FLOW_STATUS.get(agent_status, FlowRunStatus.FAILED.value)


class DependencyStatus(str, Enum):
    """
    Per-component health status returned in /health/deep dependency checks.

    OK means the component is reachable and operational.
    DEGRADED means operational with non-critical failures.
    UNAVAILABLE means the component cannot be reached.
    NOT_CONFIGURED means the optional dependency is not wired up.
    NOT_RUNNING means initialized but not actively running (e.g. scheduler follower).
    NOT_APPLICABLE means the check is not relevant for the active deployment profile.
    """

    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    NOT_CONFIGURED = "not_configured"
    NOT_RUNNING = "not_running"
    NOT_APPLICABLE = "not_applicable"


class PublicHealthStatus(str, Enum):
    """
    Top-level health status returned in /health responses.

    Derived from the internal health tier: all critical deps ok → OK;
    non-critical failures → DEGRADED; any critical failure → UNHEALTHY.
    """

    OK = "ok"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class AutonomyDecision(str, Enum):
    """
    Decision codes returned by the trigger evaluator for autonomous agent runs.

    Returned in agent autonomous execution responses as status: EXECUTE / DEFERRED / IGNORED.
    EXECUTE: trigger fires, agent execution proceeds immediately.
    DEFER: trigger deferred; re-evaluation scheduled after defer_seconds.
    IGNORE: trigger dismissed; no further re-evaluation.
    """

    EXECUTE = "execute"
    DEFER = "defer"
    IGNORE = "ignore"
