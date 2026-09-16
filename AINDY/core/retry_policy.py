"""
Retry Policy - central definition of retry semantics for all execution types.

This module defines how many attempts each execution type makes and under what
conditions. It also centralizes the shared backoff calculation so retry delays
stay consistent across execution paths.

Current system defaults (preserved exactly):
  Flow nodes    -> max_attempts=3  (global POLICY["max_retries"] in flow_engine.py)
  Agent low/med -> max_attempts=3  (MAX_STEP_RETRIES in nodus_adapter.py)
  Agent high    -> max_attempts=1  (immediate fail; no retry)
  AsyncJob      -> max_attempts=1  (default in async_job_service.py)
  Nodus sched.  -> max_attempts=3  (NodusScheduledJob.max_retries default)
"""
from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Optional


_MAX_BACKOFF_SECONDS = 10.0
_MAX_JITTER_MS = 50


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RetryPolicy:
    """Immutable retry policy for one execution unit."""

    max_attempts: int
    """Total attempts allowed (1 = no retry)."""

    backoff_ms: int = 0
    """Base delay between attempts in milliseconds."""

    exponential_backoff: bool = False
    """If True, multiply backoff_ms by 2^attempt between retries."""

    high_risk_immediate_fail: bool = False
    """If True, any failure on the first attempt is terminal regardless of
    max_attempts. Matches nodus_adapter high-risk no-retry rule."""

    execution_guarantee: str = "AT_LEAST_ONCE"
    """Idempotency contract: 'AT_LEAST_ONCE' or 'EXACTLY_ONCE'."""


# ---------------------------------------------------------------------------
# Well-known policies (named constants for documentation and future adoption)
# ---------------------------------------------------------------------------

# Mirrors flow_engine.POLICY["max_retries"] = 3 with exponential backoff
FLOW_NODE_DEFAULT = RetryPolicy(max_attempts=3, backoff_ms=200, exponential_backoff=True)

# Mirrors nodus_adapter.MAX_STEP_RETRIES = 3 for low/medium risk
AGENT_LOW_MEDIUM = RetryPolicy(max_attempts=3, backoff_ms=200, exponential_backoff=True)

# Mirrors nodus_adapter high-risk rule: 1 attempt, immediate fail on error
AGENT_HIGH_RISK = RetryPolicy(
    max_attempts=1,
    backoff_ms=0,
    exponential_backoff=False,
    high_risk_immediate_fail=True,
    execution_guarantee="EXACTLY_ONCE",
)

# Mirrors async_job_service default max_attempts=1
ASYNC_JOB_DEFAULT = RetryPolicy(max_attempts=1, backoff_ms=500, exponential_backoff=True)

# Mirrors NodusScheduledJob.max_retries default = 3 via nodus_schedule_service
NODUS_SCHEDULED_DEFAULT = RetryPolicy(max_attempts=3, backoff_ms=300, exponential_backoff=True)

# Used when a node explicitly opts out of retry (e.g. task_orchestrate RETRY->FAILURE)
NO_RETRY = RetryPolicy(max_attempts=1, backoff_ms=0, exponential_backoff=False)


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

_RISK_TO_AGENT_POLICY: dict[str, RetryPolicy] = {
    "low": AGENT_LOW_MEDIUM,
    "medium": AGENT_LOW_MEDIUM,
    "high": AGENT_HIGH_RISK,
}

# The non-retryable substring table now lives beside `classify_failure` below as
# `_SUBSTRING_CLASSES` — the same nine needles, each mapped to a failure class.


def resolve_retry_policy(
    *,
    execution_type: str,
    risk_level: Optional[str] = None,
    node_max_retries: Optional[int] = None,
    job_max_retries: Optional[int] = None,
) -> RetryPolicy:
    """
    Return the RetryPolicy that applies to one execution unit.

    Parameters
    ----------
    execution_type:
        One of ``"flow"``, ``"agent"``, ``"job"``, ``"nodus"``.
        Unknown types fall back to NO_RETRY (fail-safe).

    risk_level:
        Agent step risk level: ``"low"``, ``"medium"``, or ``"high"``.
        Only meaningful when execution_type == ``"agent"``.

    node_max_retries:
        Per-node override from node config (e.g. a flow node that declares
        its own ``max_retries`` value). When present this overrides the
        flow default. Has no effect for non-flow types.

    job_max_retries:
        Per-job override from ``NodusScheduledJob.max_retries``. Only
        meaningful when execution_type == ``"nodus"``.

    Returns
    -------
    RetryPolicy
        Frozen dataclass; never raises.
    """
    etype = (execution_type or "").lower().strip()

    if etype == "flow":
        if node_max_retries is not None:
            return RetryPolicy(
                max_attempts=max(1, node_max_retries),
                backoff_ms=FLOW_NODE_DEFAULT.backoff_ms,
                exponential_backoff=FLOW_NODE_DEFAULT.exponential_backoff,
            )
        return FLOW_NODE_DEFAULT

    if etype == "agent":
        risk = (risk_level or "high").lower().strip()
        return _RISK_TO_AGENT_POLICY.get(risk, AGENT_HIGH_RISK)

    if etype == "job":
        return ASYNC_JOB_DEFAULT

    if etype == "nodus":
        # Nodus scripts run inside a flow wrapper; inherit from there.
        # If a scheduled job supplies its own max_retries, honour that.
        if job_max_retries is not None:
            return RetryPolicy(
                max_attempts=max(1, job_max_retries),
                backoff_ms=NODUS_SCHEDULED_DEFAULT.backoff_ms,
                exponential_backoff=NODUS_SCHEDULED_DEFAULT.exponential_backoff,
            )
        return NODUS_SCHEDULED_DEFAULT

    # Unknown type -> safest default: no retry
    return NO_RETRY


# ---------------------------------------------------------------------------
# Retry execution helpers
# ---------------------------------------------------------------------------

def _retry_delay_seconds(policy: RetryPolicy, attempt_number: int) -> float:
    """
    Return the delay before the next retry attempt.

    ``attempt_number`` is 1-based for retries only: after the first failed
    attempt pass ``1``, so the first execution attempt is never delayed.
    """
    if attempt_number <= 0 or policy.backoff_ms <= 0:
        return 0.0

    multiplier = 2 ** attempt_number if policy.exponential_backoff else 1
    delay_ms = (policy.backoff_ms * multiplier) + random.randint(0, _MAX_JITTER_MS)
    return min(delay_ms / 1000.0, _MAX_BACKOFF_SECONDS)


def _sleep_before_retry(policy: RetryPolicy, attempt_number: int) -> None:
    delay_seconds = _retry_delay_seconds(policy, attempt_number)
    if delay_seconds > 0:
        time.sleep(delay_seconds)


async def _sleep_before_retry_async(policy: RetryPolicy, attempt_number: int) -> None:
    delay_seconds = _retry_delay_seconds(policy, attempt_number)
    if delay_seconds > 0:
        await asyncio.sleep(delay_seconds)


# ---------------------------------------------------------------------------
# Failure classification (RETRY-CLASSIFY-1)
# ---------------------------------------------------------------------------
#
# Whether a failed attempt is retried used to be decided by `is_retryable_error(str)`: lowercase
# the message, match nine substrings. Run over `execute_tool`'s OWN refusal strings that read
# RETRY for a cancelled run, a missing capability token and a crashed enforcement check — three
# conditions that cannot change between attempts (design §3). The class is therefore set at the
# RAISING SITE (`failure_class` on the result dict, beside `error`), and the substring table
# survives only as a fallback for un-classed strings — one that says so when it fires.
#
# ★ The class is a STRING on the result dict, not an exception type, because the three loops
# that consume it (flow node, tool step, compiled plan in the guest) all consume DICTS, and the
# guest boundary swallows host exceptions into `ok: False` — a type cannot cross it, a string can.
# Design: docs/design/RETRY_CLASSIFICATION_AND_CONTEXT_DESIGN.md.

FAILURE_CLASSES: frozenset[str] = frozenset({
    "transient",     # retry may succeed: timeout, worker crash, 5xx, connection reset
    "cancelled",     # the run was cancelled — never retry, never re-plan
    "permission",    # capability / scope / policy refusal
    "not_found",     # tool, syscall, route, resource absent
    "invalid",       # caller-side: bad args, schema violation
    "fatal",         # the raising site knows it is terminal
})

#: The one class a retry may follow. Everything else stops the loop.
RETRYABLE_CLASSES: frozenset[str] = frozenset({"transient"})

FAILURE_CLASS_KEY = "failure_class"

# The fallback table, kept verbatim from the string-only classifier so the default flip changes
# nothing for a string the table already stopped. Order matters only for the class it maps to.
_SUBSTRING_CLASSES: tuple[tuple[str, str], ...] = (
    ("permission", "permission"),
    ("unauthorized", "permission"),
    ("forbidden", "permission"),
    ("blocked by policy", "permission"),
    ("not found", "not_found"),
    ("404", "not_found"),
    ("401", "permission"),
    ("403", "permission"),
    ("invalid", "invalid"),
)


@dataclass(frozen=True)
class FailureRecord:
    """One failed attempt, classified. The payload both retry entries share (design §4)."""

    error: str
    """The message, unchanged — what callers read today."""

    failure_class: str
    """One of ``FAILURE_CLASSES``."""

    classified_by: str
    """``"site"`` (the raising site declared it), ``"substring"`` (the fallback table fired),
    or ``"default"`` (neither decided; treated as transient — the pre-existing behaviour)."""

    attempt: int = 1
    """1-based attempt that produced it."""

    site: str = "unknown"
    """``"flow_node"`` | ``"tool_step"`` | ``"compiled_plan"`` | ``"syscall"``."""

    @property
    def retryable(self) -> bool:
        return self.failure_class in RETRYABLE_CLASSES

    def as_dict(self) -> dict[str, Any]:
        return {
            "failure_class": self.failure_class,
            "classified_by": self.classified_by,
            "attempt": self.attempt,
            "site": self.site,
        }


def _substring_class(message: str) -> Optional[str]:
    lower = message.lower()
    for needle, klass in _SUBSTRING_CLASSES:
        if needle in lower:
            return klass
    return None


def classify_failure(
    result_or_error: Any,
    *,
    site: str = "unknown",
    attempt: int = 1,
) -> FailureRecord:
    """Classify one failed attempt.

    Accepts the result dict a loop already holds (``{"success": False, "error": ..,
    "failure_class": ..}``), a bare error string, or ``None``. A class the site declared wins;
    otherwise the substring table; otherwise ``transient`` — and the record says which.
    """
    declared: Any = None
    message: Any = result_or_error
    if isinstance(result_or_error, Mapping):
        declared = result_or_error.get(FAILURE_CLASS_KEY)
        message = result_or_error.get("error")
    text = "" if message is None else str(message)

    if isinstance(declared, str) and declared in FAILURE_CLASSES:
        return FailureRecord(text, declared, "site", attempt, site)
    matched = _substring_class(text) if text else None
    if matched is not None:
        return FailureRecord(text, matched, "substring", attempt, site)
    return FailureRecord(text, "transient", "default", attempt, site)


def is_retryable_error(error: Any) -> bool:
    """Return False when a failure must not be retried, whatever the policy allows.

    Takes the result DICT where the caller has one (so a site-declared ``failure_class`` is
    honoured) or the bare error string (the legacy form, decided by the fallback table). Wired
    at the flow-node retry gate, the agent tool-step loop, the Nodus host function of the same
    name, and into every compiled agent plan.
    """
    return classify_failure(error).retryable


def record_retry_classification(record: FailureRecord, *, decision: str) -> None:
    """Count one classification where a loop decided (``decision``: ``"retry"`` | ``"stop"``).

    The operator signal for this class of failure: a mis-classification used to be
    indistinguishable from a hard failure. ``classified_by="substring"`` is the residue the
    table still owns. Never raises — a metrics failure must not change a retry decision.
    """
    try:
        from AINDY.platform_layer.metrics import retry_classifications_total

        retry_classifications_total.labels(
            site=record.site,
            failure_class=record.failure_class,
            classified_by=record.classified_by,
            decision=decision,
        ).inc()
    except Exception:  # noqa: BLE001 — observability never decides
        pass


def decide_retry(
    result_or_error: Any,
    *,
    site: str,
    attempt: int,
    attempts_allowed: bool,
) -> tuple[bool, FailureRecord]:
    """Classify, decide, and COUNT in one step — the shape every loop should call.

    ``attempts_allowed`` is the policy's answer (attempts remain); the class can only veto it.
    Returns ``(retry, record)``.
    """
    record = classify_failure(result_or_error, site=site, attempt=attempt)
    retry = bool(attempts_allowed and record.retryable)
    record_retry_classification(record, decision="retry" if retry else "stop")
    return retry, record
