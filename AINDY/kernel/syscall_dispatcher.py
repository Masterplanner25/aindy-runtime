"""
SyscallDispatcher â€" single entry point for all A.I.N.D.Y. system calls.

All sys.v{N}.{domain}.{action} calls route through dispatch(). The dispatcher:

  1. Parses version and action from the syscall name.
  2. Validates the syscall name exists in the registry.
  3. Enforces the required capability against the caller's SyscallContext.
  4. Validates input payload against the entry's input_schema (if present).
  5. Checks tenant isolation and resource quota.
  6. Executes the registered handler.
  7. Validates output against output_schema (non-fatal â€" logs warning only).
  8. Emits deprecation warning if the syscall is marked deprecated.
  9. Wraps the result in the standard response envelope.
 10. Emits a SYSCALL_EXECUTED SystemEvent (non-fatal, swallowed on failure).

Standard response envelope
--------------------------
All calls return::

    {
        "status":            "success" | "error",
        "data":              dict,      # handler output on success, {} on error
        "trace_id":          str,
        "execution_unit_id": str,
        "syscall":           str,       # fully-qualified syscall name
        "version":           str,       # parsed ABI version (e.g. "v1")
        "duration_ms":       int,
        "error":             str | None,
        "warning":           str | None # set when syscall is deprecated
    }

The dispatcher never raises for transient or expected errors — every such path
returns the envelope. The one exception is SyscallContractViolation, which is
raised when an EXACTLY_ONCE handler returns a non-dict value. This is a
programming error and callers must not catch it silently.

Usage
-----
    from AINDY.kernel.syscall_dispatcher import get_dispatcher, SyscallContext

    ctx = SyscallContext(
        execution_unit_id="run-123",
        user_id="user-456",
        capabilities=["memory.read", "event.emit"],
        trace_id="run-123",
    )
    result = get_dispatcher().dispatch("sys.v1.memory.read", {"query": "auth"}, ctx)
    assert result["status"] == "success"
    nodes = result["data"]["nodes"]
    version = result["version"]   # "v1"
"""
from __future__ import annotations

import logging
import os
import time
import uuid as _uuid
from contextvars import ContextVar

# MEB-1a — the EffectRecord idempotency primitive is shared with the agent tool path
# (MEB-0) in kernel/effect_ledger.py. The dispatcher gate uses it via these aliases;
# the previously-duplicated private copies were removed here. See
# docs/runtime/MEDIATED_EFFECT_BOUNDARY_PROGRAM.md.
from AINDY.kernel.effect_ledger import (
    STALE_PENDING_THRESHOLD_SECONDS,
    complete_effect_record as _complete_effect_record,
    durable_effects_active as _durable_effects_active,
    resolve_effect_record as _resolve_effect_record,
)
from typing import Any

from AINDY.config import settings
from AINDY.kernel.circuit_breaker import CircuitOpenError
# Re-export SyscallContext so callers only need one import.
from AINDY.kernel.syscall_registry import (  # noqa: F401
    DEFAULT_NODUS_CAPABILITIES,
    SYSCALL_REGISTRY,
    SyscallContext,
    SyscallEntry,
    register_syscall,
)
from AINDY.kernel.syscall_versioning import (
    parse_syscall_name,
    validate_input,
    validate_output,
    resolve_version,
    SYSCALL_VERSION_FALLBACK,
)
from AINDY.platform_layer.otel import get_tracer, span_context_from_trace_id

try:
    from opentelemetry import trace
    from opentelemetry.trace import NonRecordingSpan, Status, StatusCode, set_span_in_context

    _OTEL_AVAILABLE = True
except ImportError:
    trace = None
    NonRecordingSpan = None
    Status = None
    StatusCode = None
    set_span_in_context = None
    _OTEL_AVAILABLE = False

__all__ = [
    "SyscallDispatcher",
    "SyscallContractViolation",
    "STALE_PENDING_THRESHOLD_SECONDS",
    "SyscallContext",
    "DEFAULT_NODUS_CAPABILITIES",
    "register_syscall",
    "get_dispatcher",
    "dispatch_syscall",
    "make_syscall_ctx_from_flow",
    "make_syscall_ctx_from_tool",
    "child_context",
]

# â"€â"€ Trace propagation ContextVars â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
# These carry the root trace_id and execution_unit_id across nested dispatch()
# calls within the same thread or asyncio task.  The root dispatch() sets them
# and always resets them in a finally block â€" nested calls inherit them without
# writing a new token.
_TRACE_ID_CTX: ContextVar[str] = ContextVar("syscall_trace_id", default="")
_EU_ID_CTX: ContextVar[str] = ContextVar("syscall_eu_id", default="")


def _is_uuid(value: Any) -> bool:
    """True when ``value`` is a bare UUID (str or uuid.UUID)."""
    if isinstance(value, _uuid.UUID):
        return True
    if not isinstance(value, str):
        return False
    try:
        _uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _gate_scope_engaged(value: Any) -> bool:
    """True when a run scope should engage the idempotency gate: a bare UUID, or a
    prefixed run id whose tail (after the last ``_`` or ``:``) is a UUID — e.g.
    ``run_<uuid>`` or ``flow:<uuid>``.

    Historically the gate only fired for a bare UUID because it bound the scope to a
    UUID primary-key column and PostgreSQL aborts the transaction on a bad cast (#157).
    MEB-1b removed that EU-PK lookup — the scope is now only hashed into the ``action_id``
    (never cast) — so this widened match is #157-safe and lets the gate cover real
    flow/run scopes that carry a ``run_``/``flow:`` prefix.
    """
    if _is_uuid(value):
        return True
    if isinstance(value, str) and value:
        tail = value
        for sep in ("_", ":"):
            if sep in tail:
                tail = tail.rsplit(sep, 1)[-1]
        return _is_uuid(tail)
    return False


def _syscall_idempotency_enabled() -> bool:
    """MEB-1b global gate. When off (default), the syscall idempotency gate never fires."""
    return os.getenv("AINDY_SYSCALL_IDEMPOTENCY", "").strip().lower() in {"1", "true", "yes"}


# Lazy import of ResourceManager to avoid circular imports at module load.
# The resource manager is only consulted inside dispatch(), so it's safe.
def _get_rm():
    from AINDY.kernel.resource_manager import get_resource_manager
    return get_resource_manager()

logger = logging.getLogger(__name__)

def _quota_backend_failure_may_fail_open() -> bool:
    return bool(settings.is_testing or settings.is_dev)


def _validate_runtime_owned_call_metadata(context: SyscallContext) -> str | None:
    metadata = context.metadata if isinstance(context.metadata, dict) else {}
    extension_call = metadata.get("_extension_call")
    if extension_call is None:
        return None
    if not isinstance(extension_call, dict):
        return "TENANT_VIOLATION: extension runtime call metadata must be a dict"
    tenant_user_id = str(extension_call.get("tenant_user_id") or "").strip()
    extension_name = str(extension_call.get("extension_name") or "").strip()
    if not tenant_user_id:
        return "TENANT_VIOLATION: extension runtime call is missing tenant_user_id"
    if not extension_name:
        return "TENANT_VIOLATION: extension runtime call is missing extension_name"
    if tenant_user_id != str(context.user_id or ""):
        return (
            f"TENANT_VIOLATION: extension runtime call tenant {tenant_user_id!r} "
            f"does not match execution context {context.user_id!r}"
        )
    return None


class SyscallContractViolation(Exception):
    """Raised when an EXACTLY_ONCE syscall handler returns a non-dict value.

    This is a programming error in the handler, not a transient failure.
    The EffectRecord is finalized as 'failed' before this exception is raised.

    AT_LEAST_ONCE handlers never raise this — non-dict returns on that path
    produce a normal error envelope so execution continues.

    Layer 2 note: registration-time return-annotation checking is not implemented.
    execution_guarantee is an EU-level attribute (ExecutionUnit.extra), not a
    per-syscall attribute. A single syscall name may be dispatched from both
    AT_LEAST_ONCE and EXACTLY_ONCE EUs, so the guarantee is not knowable at
    registration time. Contract enforcement happens here, at dispatch time.
    """


# _resolve_effect_record / _complete_effect_record now live in kernel/effect_ledger.py
# (imported as the private aliases at the top of this module). MEB-1a removed the
# duplicated copies; the gate call sites below are unchanged. See
# docs/runtime/MEDIATED_EFFECT_BOUNDARY_PROGRAM.md.


class SyscallDispatcher:
    """Routes sys.v1.* calls to registered handlers with capability enforcement.

    Instantiate once and reuse (the module-level singleton is the normal path).
    The dispatcher itself is stateless â€" all state lives in the handlers and DB.
    """

    def dispatch(
        self,
        name: str,
        payload: dict[str, Any],
        context: SyscallContext,
    ) -> dict[str, Any]:
        """Execute a syscall and return the standard response envelope.

        This method never raises. All errors â€" unknown syscall, permission
        denial, handler failure â€" are captured in the returned envelope.

        Trace propagation
        -----------------
        If a parent dispatch() is already active in this thread / asyncio task,
        the child inherits its trace_id and execution_unit_id automatically via
        ContextVars â€" even if the caller passed a freshly-constructed context.
        This ensures a single trace_id across the full nested execution chain.

        If this is the root call (no parent active), the context's existing
        trace_id / execution_unit_id are used; empty strings cause a new UUID
        to be generated so observability is never missing an ID.

        Args:
            name:    Fully-qualified syscall name (sys.v1.{domain}.{action}).
            payload: Arbitrary handler-specific arguments.
            context: Caller's execution context (user_id, capabilities, trace_id).

        Returns:
            Standard response envelope (see module docstring).
        """
        t_start = time.monotonic()
        _orig_eu_id = context.execution_unit_id or ""
        context, _tok_trace, _tok_eu = self._resolve_trace_context(context)
        try:
            return self._dispatch(name, payload, context, t_start, _orig_eu_id)
        except SyscallContractViolation:
            raise
        except Exception as exc:  # belt-and-suspenders â€" _dispatch shouldn't leak
            logger.error(
                "[SyscallDispatcher] unhandled exception for '%s': %s",
                name, exc, exc_info=True,
            )
            return self._error_envelope(name, context, str(exc), t_start)
        finally:
            if _tok_trace is not None:
                _TRACE_ID_CTX.reset(_tok_trace)
            if _tok_eu is not None:
                _EU_ID_CTX.reset(_tok_eu)

    # â"€â"€ Private â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

    def _resolve_trace_context(
        self,
        context: SyscallContext,
    ) -> tuple[SyscallContext, object, object]:
        """Return a (context, trace_token, eu_token) triple.

        If a parent dispatch() is active (ContextVars are set):
          - return a new SyscallContext inheriting the parent's trace_id and
            execution_unit_id; tokens are None (nothing to reset).

        If this is the root call (ContextVars are empty):
          - fill in any missing trace_id / execution_unit_id with new UUIDs,
          - set both ContextVars so nested dispatches inherit them,
          - return the tokens so dispatch() can reset them in its finally block.
        """
        inherited_trace = _TRACE_ID_CTX.get()
        inherited_eu = _EU_ID_CTX.get()

        if inherited_trace:
            # â"€â"€ Nested call â€" inherit parent trace/EU â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
            if (
                context.trace_id != inherited_trace
                or context.execution_unit_id != inherited_eu
            ):
                logger.debug(
                    "[SyscallDispatcher] trace inherit: caller trace=%r eu=%r "
                    "â†’ parent trace=%r eu=%r",
                    context.trace_id, context.execution_unit_id,
                    inherited_trace, inherited_eu,
                )
                context = SyscallContext(
                    execution_unit_id=inherited_eu or context.execution_unit_id,
                    user_id=context.user_id,
                    capabilities=context.capabilities,
                    trace_id=inherited_trace,
                    memory_context=context.memory_context,
                    metadata=context.metadata,
                )
            return context, None, None

        # â"€â"€ Root call â€" establish trace context â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
        trace_id = context.trace_id or str(_uuid.uuid4())
        eu_id = context.execution_unit_id or str(_uuid.uuid4())
        if not context.trace_id or not context.execution_unit_id:
            context = SyscallContext(
                execution_unit_id=eu_id,
                user_id=context.user_id,
                capabilities=context.capabilities,
                trace_id=trace_id,
                memory_context=context.memory_context,
                metadata=context.metadata,
            )
        tok_trace = _TRACE_ID_CTX.set(context.trace_id)
        tok_eu = _EU_ID_CTX.set(context.execution_unit_id)
        return context, tok_trace, tok_eu

    def _dispatch(
        self,
        name: str,
        payload: dict[str, Any],
        context: SyscallContext,
        t_start: float,
        _orig_eu_id: str = "",
    ) -> dict[str, Any]:
        # Step 1 â€" parse version and validate syscall exists
        try:
            parsed_version, _ = parse_syscall_name(name)
        except ValueError:
            parsed_version = "unknown"

        # Version fallback: if requested version has no entries, try latest stable
        if parsed_version != "unknown":
            available = frozenset(SYSCALL_REGISTRY.versions())
            resolved = resolve_version(parsed_version, available, SYSCALL_VERSION_FALLBACK)
            if resolved is None and parsed_version not in available:
                return self._error_envelope(
                    name, context,
                    f"Unknown syscall version: {parsed_version!r}; "
                    f"available versions: {sorted(available)}",
                    t_start,
                    version=parsed_version,
                )
            # If fallback resolved to a different version, rewrite the lookup key
            if resolved and resolved != parsed_version:
                _, action = parse_syscall_name(name)
                fallback_name = f"sys.{resolved}.{action}"
                logger.warning(
                    "[SyscallDispatcher] version fallback: %r â†’ %r",
                    name, fallback_name,
                )
                name = fallback_name
                parsed_version = resolved

        entry: SyscallEntry | None = SYSCALL_REGISTRY.get(name)
        if entry is None:
            return self._error_envelope(
                name, context,
                f"Unknown syscall: {name!r}",
                t_start,
                version=parsed_version,
            )

        # Step 2 â€" enforce capability
        if entry.capability not in context.capabilities:
            return self._error_envelope(
                name, context,
                f"Permission denied: '{name}' requires capability "
                f"'{entry.capability}'; caller has {context.capabilities}",
                t_start,
                version=parsed_version,
            )

        # Step 2b â€" tenant isolation: validate context has a user_id
        if not context.user_id:
            return self._error_envelope(
                name, context,
                "TENANT_VIOLATION: syscall requires authenticated tenant context",
                t_start,
                version=parsed_version,
            )
        metadata_error = _validate_runtime_owned_call_metadata(context)
        if metadata_error:
            return self._error_envelope(
                name,
                context,
                metadata_error,
                t_start,
                version=parsed_version,
            )

        # Step 2c â€" resource quota check (syscall budget)
        # Quota hard denials always fail closed. Quota backend failures only
        # fail open in development/test environments; production blocks
        # execution until quota enforcement recovers.
        try:
            rm = _get_rm()
            quota_ok, quota_reason = rm.check_quota(context.execution_unit_id)
        except Exception as quota_exc:
            if _quota_backend_failure_may_fail_open():
                logger.warning("[SyscallDispatcher] resource quota check skipped: %s", quota_exc)
            else:
                logger.error("[SyscallDispatcher] resource quota check failed closed: %s", quota_exc)
                return self._error_envelope(
                    name,
                    context,
                    "Quota backend unavailable: syscall execution is blocked until quota enforcement recovers.",
                    t_start,
                    version=parsed_version,
                )
        else:
            if not quota_ok:
                return self._error_envelope(name, context, quota_reason, t_start,
                                            version=parsed_version)

        # Step 2d â€" input validation against ABI schema
        if entry.input_schema:
            errors = validate_input(entry.input_schema, payload)
            if errors:
                return self._error_envelope(
                    name, context,
                    f"Input validation failed for {name!r}: " + "; ".join(errors),
                    t_start,
                    version=parsed_version,
                )

        # Step 2e â€" deprecation check (warn but still execute)
        deprecation_warning: str | None = None
        if entry.deprecated:
            parts = [f"Syscall '{name}' is deprecated"]
            if entry.deprecated_since:
                parts.append(f"since {entry.deprecated_since}")
            if entry.replacement:
                parts.append(f"â€” use '{entry.replacement}' instead")
            deprecation_warning = " ".join(parts) + "."
            logger.warning("[SyscallDispatcher] %s", deprecation_warning)

        # Step 2f — idempotency gate (MEB-1b). Fires only when ALL hold: the global flag
        # AINDY_SYSCALL_IDEMPOTENCY is on, the syscall declares
        # execution_guarantee="EXACTLY_ONCE", the run carries a scope, and that scope engages
        # the gate (a bare UUID or a prefixed run id like run_<uuid> — see
        # _gate_scope_engaged). Default AT_LEAST_ONCE = no dedup (current behavior). The
        # guarantee now comes from the SyscallEntry — an addressable source — NOT the
        # ExecutionUnit.extra lookup, which never matched the id a syscall carries (IDEM-10).
        # The scope is only hashed into the action_id (never cast), so the widened predicate
        # stays #157-safe. A SEPARATE _gate_db session is used so a ledger failure can never
        # poison the handler's connection (#157); on any failure we degrade to AT_LEAST_ONCE.
        _gate_db = None
        _gate_action_id = None
        _entry_guarantee = str(getattr(entry, "execution_guarantee", "AT_LEAST_ONCE")).upper()
        # DUR-2 — a continued run's per-run at-most-once signal engages the gate for ANY
        # syscall (declaration-free), independent of the per-syscall guarantee + master flag.
        # Reaches in-process dispatches; a nodus subprocess doesn't inherit it (DUR-2b).
        _durable = _durable_effects_active()
        if (
            (_entry_guarantee == "EXACTLY_ONCE" or _durable)
            and _orig_eu_id
            and _gate_scope_engaged(context.execution_unit_id)
            and (_syscall_idempotency_enabled() or _durable)
        ):
            from AINDY.core.execution_gate import compute_action_id
            _gate_action_id = compute_action_id(
                action_type=name,
                input_payload=dict(payload or {}),
                scope=str(context.execution_unit_id),
            )
            try:
                from AINDY.db.database import SessionLocal
                _gate_db = SessionLocal()
                _already_done, _cached = _resolve_effect_record(
                    _gate_db, _gate_action_id, name, payload,
                    # MEB-3b — attribute the effect to the caller (tenant_id == user_id in
                    # this single-user-per-tenant model). session_id, when present, comes
                    # ambiently from the effect-attribution contextvar (e.g. a multi-tenant
                    # MCP session set in the auth_hook).
                    tenant_id=str(context.user_id) if context.user_id else None,
                )
            except Exception as _gate_exc:
                # A ledger failure must not block the syscall — degrade to AT_LEAST_ONCE.
                if _gate_db is not None:
                    try:
                        _gate_db.rollback()
                    except Exception:
                        pass
                    try:
                        _gate_db.close()
                    except Exception:
                        pass
                _gate_db = None
                _gate_action_id = None
                logger.warning(
                    "[SyscallDispatcher] idempotency gate degraded to AT_LEAST_ONCE for %r: %s",
                    name, _gate_exc,
                )
            else:
                if _already_done:
                    _gate_db.close()
                    _gate_db = None
                    return {
                        "status": "success",
                        "data": _cached or {},
                        "trace_id": context.trace_id,
                        "execution_unit_id": context.execution_unit_id,
                        "syscall": name,
                        "version": parsed_version,
                        "duration_ms": int((time.monotonic() - t_start) * 1000),
                        "error": None,
                        "warning": None,
                    }

        # Step 3 â€" execute handler
        try:
            if _OTEL_AVAILABLE:
                try:
                    tracer = get_tracer("aindy.syscall")
                except Exception:
                    tracer = trace.get_tracer("noop")
                span_kwargs: dict[str, Any] = {
                    "attributes": {
                        "syscall.name": name,
                        "syscall.version": parsed_version or "unknown",
                        "syscall.capability": entry.capability if entry else "unknown",
                        "user.id": str(context.user_id or ""),
                        "trace.id": str(context.trace_id or ""),
                    }
                }
                try:
                    current_span = trace.get_current_span()
                    current_context = current_span.get_span_context()
                    if not current_context.is_valid:
                        linked_context = span_context_from_trace_id(context.trace_id)
                        if linked_context is not None:
                            span_kwargs["context"] = set_span_in_context(
                                NonRecordingSpan(linked_context)
                            )
                except Exception:
                    pass
                try:
                    span_cm = tracer.start_as_current_span(f"syscall.{name}", **span_kwargs)
                except Exception:
                    span_cm = None
                if span_cm is None:
                    data = entry.handler(payload, context)
                else:
                    with span_cm as span:
                        try:
                            data = entry.handler(payload, context)
                        except Exception as exc:
                            try:
                                span.record_exception(exc)
                                span.set_status(Status(StatusCode.ERROR, str(exc)))
                            except Exception:
                                pass
                            raise
            else:
                data = entry.handler(payload, context)
        except Exception as exc:
            logger.warning(
                "[SyscallDispatcher] handler error '%s': %s", name, exc, exc_info=True,
            )
            self._emit_syscall_event(name, context, "error")
            if _gate_db is not None and _gate_action_id is not None:
                _complete_effect_record(_gate_db, _gate_action_id, "failed", None)
                _gate_db.close()
                _gate_db = None
            message = str(exc)
            if isinstance(exc, CircuitOpenError):
                message = f"HTTP_503:{message}"
            return self._error_envelope(name, context, message, t_start,
                                        version=parsed_version)

        # Step 3b — output type check.
        # EXACTLY_ONCE: non-dict is a hard contract violation → raise SyscallContractViolation.
        # AT_LEAST_ONCE / no gate: non-dict is a normal error envelope (existing behaviour).
        if not isinstance(data, dict):
            logger.error(
                "[SyscallDispatcher] handler contract violation for '%s': expected dict,"
                " got %s (action_id=%s)",
                name,
                type(data).__name__,
                _gate_action_id or "n/a",
            )
            self._emit_syscall_event(name, context, "error")
            if _gate_db is not None and _gate_action_id is not None:
                _complete_effect_record(_gate_db, _gate_action_id, "failed", None)
                _gate_db.close()
                _gate_db = None
                raise SyscallContractViolation(
                    f"EXACTLY_ONCE handler '{name}' returned {type(data).__name__},"
                    f" expected dict (action_id={_gate_action_id})"
                )
            return self._error_envelope(
                name,
                context,
                f"Syscall handler contract violation: '{name}' returned {type(data).__name__}, expected dict",
                t_start,
                version=parsed_version,
            )
        if entry.output_schema:
            out_errors = validate_output(entry.output_schema, data)
            if out_errors:
                detail = "; ".join(out_errors)
                if entry.stable:
                    logger.error(
                        "[SyscallDispatcher] stable output schema mismatch for '%s': %s",
                        name,
                        detail,
                    )
                    self._emit_syscall_event(name, context, "error")
                    if _gate_db is not None and _gate_action_id is not None:
                        _complete_effect_record(_gate_db, _gate_action_id, "failed", None)
                        _gate_db.close()
                        _gate_db = None
                    return self._error_envelope(
                        name,
                        context,
                        f"Stable syscall output validation failed for {name!r}: {detail}",
                        t_start,
                        version=parsed_version,
                    )
                logger.warning(
                    "[SyscallDispatcher] output schema mismatch for experimental '%s': %s",
                    name,
                    detail,
                )

        if _gate_db is not None and _gate_action_id is not None:
            _complete_effect_record(_gate_db, _gate_action_id, "success", data)
            _gate_db.close()
            _gate_db = None

        # Step 4 â€" record syscall usage in ResourceManager (non-fatal)
        try:
            duration_so_far = int((time.monotonic() - t_start) * 1000)
            _get_rm().record_usage(
                context.execution_unit_id,
                {"syscall_count": 1, "wall_time_ms": duration_so_far},
            )
        except Exception as _rm_exc:
            logger.debug("[SyscallDispatcher] resource record skipped: %s", _rm_exc)

        # Step 5 â€" emit observability event (non-fatal)
        try:
            self._emit_syscall_event(name, context, "success")
        except Exception as exc:
            logger.debug("[SyscallDispatcher] observability skipped for '%s': %s", name, exc)

        # Step 6 â€" return structured result
        return {
            "status": "success",
            "data": data,
            "trace_id": context.trace_id,
            "execution_unit_id": context.execution_unit_id,
            "syscall": name,
            "version": parsed_version,
            "duration_ms": int((time.monotonic() - t_start) * 1000),
            "error": None,
            "warning": deprecation_warning,
        }

    def _error_envelope(
        self,
        name: str,
        context: SyscallContext,
        message: str,
        t_start: float,
        version: str = "unknown",
    ) -> dict[str, Any]:
        return {
            "status": "error",
            "data": {},
            "trace_id": context.trace_id,
            "execution_unit_id": context.execution_unit_id,
            "syscall": name,
            "version": version,
            "duration_ms": int((time.monotonic() - t_start) * 1000),
            "error": message,
            "warning": None,
        }

    def _emit_syscall_event(
        self,
        name: str,
        context: SyscallContext,
        status: str,
    ) -> None:
        """Emit SYSCALL_EXECUTED to the A.I.N.D.Y. event bus.

        Non-fatal: all exceptions are swallowed and logged at DEBUG level
        so a broken event bus never kills a syscall execution.
        """
        try:
            from AINDY.db.database import SessionLocal
            from AINDY.core.system_event_service import emit_system_event
            from AINDY.core.system_event_types import SystemEventTypes

            db = SessionLocal()
            try:
                emit_system_event(
                    db=db,
                    event_type=SystemEventTypes.SYSCALL_EXECUTED,
                    user_id=context.user_id,
                    trace_id=context.trace_id,
                    source="syscall_dispatcher",
                    payload={
                        "syscall_name": name,
                        "execution_unit_id": context.execution_unit_id,
                        "status": status,
                        "extension_call": context.metadata.get("_extension_call")
                        if isinstance(context.metadata, dict)
                        else None,
                    },
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()
        except Exception as exc:
            logger.debug(
                "[SyscallDispatcher] observability event skipped for '%s': %s",
                name, exc,
            )


# â"€â"€ Module-level singleton â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

_DISPATCHER = SyscallDispatcher()


def get_dispatcher() -> SyscallDispatcher:
    """Return the module-level SyscallDispatcher singleton.

    Use this in all production code. Tests can instantiate SyscallDispatcher()
    directly if they need isolation.
    """
    return _DISPATCHER


def _infer_dispatch_capability(name: str) -> str:
    try:
        _, action = parse_syscall_name(name)
    except ValueError:
        return "event.emit"
    parts = action.split(".")
    domain = parts[0] if parts else ""
    verb = parts[-1] if parts else ""
    if verb in {"get", "list", "count", "read", "query", "fetch"} or verb.startswith(
        ("get_", "list_", "count_", "read_", "query_", "fetch_")
    ):
        return f"{domain}.read"
    if verb in {"create", "update", "delete", "ensure", "init", "write", "store", "observe"} or verb.startswith(
        ("create_", "update_", "delete_", "ensure_", "init_", "write_", "store_", "observe_")
    ):
        return f"{domain}.write"
    return action


def dispatch_syscall(
    name: str,
    payload: dict[str, Any],
    *,
    db=None,
    user_id: str | None = None,
    capability: str | None = None,
    trace_id: str | None = None,
    execution_unit_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = make_syscall_ctx_from_tool(
        str(user_id or ""),
        run_id=str(execution_unit_id or trace_id or ""),
        capabilities=[capability or _infer_dispatch_capability(name)],
        trace_id=str(trace_id or execution_unit_id or ""),
        metadata=dict(metadata or {}),
    )
    if db is not None:
        ctx.metadata["_db"] = db
    return get_dispatcher().dispatch(name, payload, ctx)


# â"€â"€ Context builder helpers â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def make_syscall_ctx_from_flow(
    context: dict,
    capabilities: list[str] | None = None,
) -> SyscallContext:
    """Build a SyscallContext from a flow node's execution context dict.

    Args:
        context:      The flow context dict (keys: run_id, user_id, trace_id,
                      workflow_type, flow_name, node_name, db).
        capabilities: Explicit capability list. Pass only what the node needs
                      (least-privilege). Defaults to DEFAULT_NODUS_CAPABILITIES.

    Returns:
        SyscallContext ready to pass to get_dispatcher().dispatch().
    """
    return SyscallContext(
        execution_unit_id=str(context.get("run_id") or ""),
        user_id=str(context.get("user_id") or ""),
        capabilities=list(capabilities) if capabilities is not None else list(DEFAULT_NODUS_CAPABILITIES),
        trace_id=str(context.get("trace_id") or context.get("run_id") or ""),
        metadata={
            "workflow_type": context.get("workflow_type"),
            "flow_name": context.get("flow_name"),
            "node_name": context.get("node_name"),
        },
    )


def make_syscall_ctx_from_tool(
    user_id: str,
    run_id: str = "",
    capabilities: list[str] | None = None,
    trace_id: str = "",
    metadata: dict | None = None,
) -> SyscallContext:
    """Build a SyscallContext for an agent tool call.

    Args:
        user_id:      The authenticated user ID.
        run_id:       Optional agent run ID for trace correlation.
        capabilities: Explicit capability list. Pass only what the tool needs.

    Returns:
        SyscallContext ready to pass to get_dispatcher().dispatch().
    """
    execution_unit_id = run_id or str(_uuid.uuid4())
    return SyscallContext(
        execution_unit_id=execution_unit_id,
        user_id=str(user_id or ""),
        capabilities=list(capabilities) if capabilities is not None else list(DEFAULT_NODUS_CAPABILITIES),
        trace_id=str(trace_id or execution_unit_id),
        metadata=dict(metadata or {}),
    )


def child_context(
    parent: SyscallContext,
    *,
    capabilities: list[str] | None = None,
    metadata: dict | None = None,
) -> SyscallContext:
    """Build a child SyscallContext that inherits trace_id and eu_id from parent.

    Use this when a handler explicitly dispatches a nested syscall and wants to
    forward the full execution identity â€" trace_id, execution_unit_id, and
    user_id â€" unchanged.  capabilities and metadata can be overridden.

    The ContextVar mechanism in dispatch() already propagates the trace
    automatically for most cases; use child_context() when you need the
    explicit form (e.g. for documentation clarity or override of capabilities).

    Example::

        def _handle_flow_run(payload, context):
            ctx = child_context(context, capabilities=["memory.read"])
            return get_dispatcher().dispatch("sys.v1.memory.read", {}, ctx)
    """
    return SyscallContext(
        execution_unit_id=parent.execution_unit_id,
        user_id=parent.user_id,
        capabilities=capabilities if capabilities is not None else list(parent.capabilities),
        trace_id=parent.trace_id,
        memory_context=list(parent.memory_context),
        metadata=metadata if metadata is not None else dict(parent.metadata),
    )

