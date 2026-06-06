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
    Terminal states: COMPLETED, FAILED.
    Suspendable state: WAITING (WAIT node entered; resumes on matching event).
    """

    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentRunStatus(str, Enum):
    """
    Lifecycle states for an AgentRun entity.

    Stable surface — returned in agent execution API responses.
    Terminal states: COMPLETED, FAILED.
    Intermediate: DELEGATED (sub-agent dispatched; not yet terminal).
    """

    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    DELEGATED = "delegated"
    COMPLETED = "completed"
    FAILED = "failed"


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
