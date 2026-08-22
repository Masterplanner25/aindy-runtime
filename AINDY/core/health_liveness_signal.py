"""FR-18 — a liveness probe must not persist a full health snapshot.

Every successful ``/health`` call wrote a ``health.liveness.completed`` SystemEvent whose
payload was the **entire health response** — 26 top-level keys including
``trusted_python_execution`` (~52 kB uncompressed), the deployment contract, the sandbox
attestation and the full plugin inventory.

**Who probes it, precisely** — the app team's report said "the recommended compose shape,
every 15s"; ours is the *image's* ``HEALTHCHECK``, ``curl --fail /health`` every **30s**
(2,880 rows/day). Our compose's ``api`` healthcheck uses ``/ready``, which emits nothing;
their compose adds a 15s ``/health`` probe on top. Either way the write rate is set by a
timer and not by traffic, which is the property that matters.

Measured by the app team on a dev stack with four accounts and no real traffic:
**120,444 rows, 3,317 MB — 99.6% of the database** over 34 days (~3,500 rows/day, ~98
MB/day), with ``n_dead_tup = 0``, i.e. live intended data rather than bloat. A plain
``pg_dump`` would not finish; excluding this one table's data produced 17 MB.

The content is near-constant by construction. Sandbox posture, deployment contract and
plugin inventory cannot change between two probes seconds apart, so the same ~28 kB was
rewritten thousands of times a day.

**What this module does instead.** It keeps the event — nothing else records that the
liveness path ran — and drops both the size and the rate:

1. **A digest, never the snapshot.** Status, degraded domains, warnings and a
   fingerprint of the posture blobs: a few hundred bytes. The full snapshot is available
   on demand from ``/health/detail``, which is where it belonged all along.
2. **Emit on change, plus a slow heartbeat.** A row lands when the fingerprint moves
   (that is the event worth having), on the first probe after boot, and otherwise at
   most once per ``AINDY_HEALTH_LIVENESS_EVENT_INTERVAL_SECONDS`` (default 1h) so
   silence is never ambiguous.

**Why both, rather than the cheaper one.** They fail differently, and that is the point.
Change-detection depends on the fingerprint excluding every volatile field; if a new
health key sneaks a counter or a timestamp past ``_VOLATILE_LEAF_KEYS``, every probe
looks *changed* and the rate control is defeated. The digest is not defeated by that — it
bounds a worst-case probe to a few hundred bytes instead of 28 kB, so the failure mode of
the rate control is *a smaller improvement*, not a return to 98 MB/day. Watch
``aindy_health_liveness_events_total{outcome="suppressed"}``: if it stays at zero while
probes flow, change-detection is not working and the fingerprint needs a field.

**A cold process writes more than one row, and that is correct.** Several posture
providers populate lazily — the plugin-host probe, the sandbox attestation, the runtime
conditions — so the first probes of a fresh container can each register a genuine change
before the picture settles. ``changed_keys`` on the row names exactly which key moved, so
"the process is warming up" and "a volatile field is leaking into the fingerprint" are
distinguishable rather than both reading as `reason: changed`. It is also why the route
test warms once before measuring: asserting one row from a cold start measures cache
population, not rate control.

**Nothing consumes this event** — checked across both repos: the only references are the
emit site, this module and documentation. So the payload shape is free to change.

Best-effort throughout: a liveness check must stay a liveness check, so no failure here
may reach the caller.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

#: Off switch — a liveness probe becomes a pure read.
_ENV_ENABLED = "AINDY_HEALTH_LIVENESS_EVENTS"
#: ``digest`` (default) | ``full``. ``full`` restores the pre-fix whole-snapshot payload
#: for anyone who was, against expectation, mining the snapshots.
_ENV_PAYLOAD_MODE = "AINDY_HEALTH_LIVENESS_EVENT_PAYLOAD"
#: Heartbeat floor for an *unchanged* posture. ``0`` disables the heartbeat entirely, so
#: only changes are recorded.
_ENV_INTERVAL = "AINDY_HEALTH_LIVENESS_EVENT_INTERVAL_SECONDS"

DEFAULT_INTERVAL_SECONDS = 3600

EVENT_TYPE = "health.liveness.completed"

#: The keys whose *values* describe posture. Selected explicitly rather than by excluding
#: volatile ones: this is a fingerprint of what an operator would call a state change, and
#: an allowlist cannot silently acquire a counter when a new health key is added — it just
#: does not see it (the module docstring says what that costs).
_POSTURE_KEYS: tuple[str, ...] = (
    "status",
    # `tier` and `deployment_contract` appear only on the production payload
    # (`HealthStatus.to_dict()`); the in-test payload is a different, smaller shape. Both
    # shapes are fingerprinted by the same allowlist, and a key that is absent is simply
    # not seen — which is why this list covers the union rather than either one.
    "tier",
    "deployment_contract",
    "version",
    "degraded_domains",
    "degraded_apps",
    "warnings",
    "platform",
    "domains",
    "dependencies",
    "runtime_conditions",
    "trusted_python_execution",
    "extension_execution_posture",
    "extension_provenance",
    "plugin_hosts",
    "plugin_sandbox_attestation",
    "plugin_sandbox_posture",
    "plugin_sandbox_platform",
    "sandbox_verification_posture",
    "async_jobs",
    "cache",
    "wait_resume",
    "stuck_run",
    "checks",
)

#: Leaf keys that move on their own. They appear *inside* posture blobs — a domain's
#: ``last_checked``, a worker's ``last_beat``, a breaker's ``failure_count`` — so
#: excluding the volatile top-level keys is not enough.
_VOLATILE_LEAF_KEYS = frozenset(
    {
        "timestamp",
        "last_checked",
        "last_check",
        "checked_at",
        "last_beat",
        "last_seen",
        "updated_at",
        "generated_at",
        "opened_at",
        "uptime_seconds",
        "duration_ms",
        "elapsed_ms",
        "latency_ms",
        "queue_depth",
        "depth",
        "in_flight",
        "delayed",
        "checkedout",
        "checked_out",
        "overflow",
        "pressure_ratio",
        "failure_count",
        "detail",
    }
)

_STATE_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "fingerprint": None,
    "key_hashes": {},
    "last_emit": None,  # datetime | None
    "probes_since_emit": 0,
}


# ── configuration (resolved per call, never cached at import — CLAUDE.md standing rule)


def liveness_events_enabled() -> bool:
    """True unless explicitly disabled."""
    return os.getenv(_ENV_ENABLED, "true").strip().lower() not in {"0", "false", "no"}


def liveness_event_payload_mode() -> str:
    """``digest`` (default) or ``full`` (the pre-fix whole-snapshot behaviour)."""
    value = os.getenv(_ENV_PAYLOAD_MODE, "digest").strip().lower()
    return "full" if value in {"full", "snapshot"} else "digest"


def heartbeat_interval_seconds() -> int:
    """Seconds between heartbeat rows for an unchanged posture. ``0`` = change-only."""
    raw = os.getenv(_ENV_INTERVAL, "").strip()
    if not raw:
        return DEFAULT_INTERVAL_SECONDS
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        logger.warning(
            "[HealthLiveness] %s=%r is not a number; using %ds",
            _ENV_INTERVAL,
            raw,
            DEFAULT_INTERVAL_SECONDS,
        )
        return DEFAULT_INTERVAL_SECONDS
    return max(0, value)


# ── fingerprinting


def _stable(value: Any) -> Any:
    """Strip self-moving leaves so an unchanged posture fingerprints identically."""
    if isinstance(value, dict):
        return {
            key: _stable(item)
            for key, item in sorted(value.items())
            if key not in _VOLATILE_LEAF_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_stable(item) for item in value]
    return value


def _hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def posture_key_hashes(payload: dict[str, Any]) -> dict[str, str]:
    """Per-key hashes of the posture-bearing half of a health payload.

    Per-key rather than one hash over the lot, so that when the fingerprint moves the
    row can say **which** key moved. Without that, a posture change and a leaked
    volatile field produce the same evidence — a `reason: changed` row and no way to
    tell a real transition from broken change-detection.
    """
    return {key: _hash(_stable(payload.get(key))) for key in _POSTURE_KEYS if key in payload}


def posture_fingerprint(payload: dict[str, Any]) -> str:
    """A stable hash of the posture-bearing half of a health payload."""
    return "sha256:" + _hash(posture_key_hashes(payload))


def _payload_bytes(payload: dict[str, Any]) -> int:
    try:
        return len(json.dumps(payload, default=str, separators=(",", ":")).encode("utf-8"))
    except Exception:  # pragma: no cover - sizing must never raise
        return -1


def build_digest(
    payload: dict[str, Any],
    *,
    fingerprint: str,
    reason: str,
    probes_since_last_event: int,
    changed_keys: list[str] | None = None,
) -> dict[str, Any]:
    """The row that replaces the snapshot. Small, and it says what it left out."""
    warnings = payload.get("warnings") or []
    if not isinstance(warnings, (list, tuple)):
        warnings = [warnings]
    return {
        "status": payload.get("status"),
        "version": payload.get("version"),
        "degraded_domains": list(payload.get("degraded_domains") or []),
        "warnings": list(warnings),
        "posture_fingerprint": fingerprint,
        # Why this row exists: a posture change, the first probe of this process, or the
        # heartbeat. Without it a reader cannot tell a transition from a keepalive.
        "reason": reason,
        # How many probes this row stands for — also the write saving, in rows.
        "probes_since_last_event": int(probes_since_last_event),
        # Which posture keys moved. Empty for boot/heartbeat rows. A row that keeps
        # naming the same key every probe is the signature of a volatile field leaking
        # into the fingerprint, not of a system changing 5,000 times a day.
        "changed_keys": list(changed_keys or []),
        "snapshot_bytes": _payload_bytes(payload),
        "snapshot_endpoint": "/health/detail",
    }


# ── decision


def _decide(
    fingerprint: str,
    *,
    now: datetime,
    interval: int,
    key_hashes: dict[str, str] | None = None,
) -> tuple[bool, str, int, list[str]]:
    """Return ``(persist, reason, probes_since_last_event, changed_keys)``.

    Updates the state. ``changed_keys`` is non-empty only for ``reason == "changed"``.
    """
    key_hashes = dict(key_hashes or {})
    with _STATE_LOCK:
        previous = _STATE["fingerprint"]
        previous_keys: dict[str, str] = _STATE["key_hashes"] or {}
        last_emit: datetime | None = _STATE["last_emit"]
        probes = int(_STATE["probes_since_emit"]) + 1
        changed: list[str] = []

        if previous is None:
            reason = "boot"
        elif fingerprint != previous:
            reason = "changed"
            changed = sorted(
                key
                for key in set(previous_keys) | set(key_hashes)
                if previous_keys.get(key) != key_hashes.get(key)
            )
        elif interval and last_emit is not None and (now - last_emit).total_seconds() >= interval:
            reason = "interval"
        else:
            _STATE["probes_since_emit"] = probes
            return False, "suppressed", probes, []

        _STATE["fingerprint"] = fingerprint
        _STATE["key_hashes"] = key_hashes
        _STATE["last_emit"] = now
        _STATE["probes_since_emit"] = 0
        return True, reason, probes, changed


def reset_state() -> None:
    """Forget what this process has emitted. For tests, and for a deliberate re-baseline."""
    with _STATE_LOCK:
        _STATE["fingerprint"] = None
        _STATE["key_hashes"] = {}
        _STATE["last_emit"] = None
        _STATE["probes_since_emit"] = 0


def _count(outcome: str) -> None:
    try:
        from AINDY.platform_layer.metrics import health_liveness_events_total

        health_liveness_events_total.labels(outcome=outcome).inc()
    except Exception:  # pragma: no cover - metrics must never break a health check
        logger.debug("[HealthLiveness] metric skipped for outcome=%s", outcome, exc_info=True)


def record_liveness_probe(payload: dict[str, Any]) -> str:
    """Record that a liveness probe completed. Never raises.

    Returns the outcome — ``persisted:<reason>`` | ``suppressed`` | ``disabled`` |
    ``failed`` — for callers that want to assert on it. The health route ignores it.
    """
    if not liveness_events_enabled():
        _count("disabled")
        return "disabled"

    from AINDY.kernel.clock import utcnow

    payload = payload or {}
    key_hashes = posture_key_hashes(payload)
    fingerprint = "sha256:" + _hash(key_hashes)
    persist, reason, probes, changed_keys = _decide(
        fingerprint,
        now=utcnow(),
        interval=heartbeat_interval_seconds(),
        key_hashes=key_hashes,
    )
    if not persist:
        _count("suppressed")
        return "suppressed"

    full_mode = liveness_event_payload_mode() == "full"
    event_payload = (
        dict(payload)
        if full_mode
        else build_digest(
            payload,
            fingerprint=fingerprint,
            reason=reason,
            probes_since_last_event=probes,
            changed_keys=changed_keys,
        )
    )

    db = None
    try:
        from AINDY.core.system_event_service import emit_system_event
        from AINDY.db.database import SessionLocal

        db = SessionLocal()
        emit_system_event(
            db=db,
            event_type=EVENT_TYPE,
            payload=event_payload,
            source="health",
            required=False,
        )
    except Exception as exc:
        logger.warning("[HealthLiveness] event emit failed: %s", exc)
        # The state was advanced before the write, so a failure here would otherwise
        # swallow the transition entirely: this probe is recorded as emitted, the next
        # identical one is suppressed, and the posture change is never written. Reset so
        # the next probe re-reports it as a boot row. Losing the probe counter is the
        # cheap half of that trade.
        reset_state()
        _count("failed")
        return "failed"
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:  # pragma: no cover - defensive
                logger.debug("[HealthLiveness] session close failed", exc_info=True)

    _count("persisted_full" if full_mode else f"persisted_{reason}")
    return f"persisted:{reason}"
