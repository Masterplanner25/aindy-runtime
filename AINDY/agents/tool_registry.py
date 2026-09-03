"""Generic agent tool registry and execution boundary."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
from typing import Any, Callable, Optional

from AINDY.core.execution_signal_helper import queue_system_event
from AINDY.platform_layer.extension_boundary import sanitize_extension_context

logger = logging.getLogger(__name__)

TOOL_REGISTRY: dict[str, dict] = {}
_SUGGESTION_PROVIDERS: list[Callable] = []
_LOADING_PLUGINS = False


def _ensure_tools_loaded() -> None:
    global _LOADING_PLUGINS
    if _LOADING_PLUGINS:
        return
    _LOADING_PLUGINS = True
    try:
        from AINDY.platform_layer.registry import (
            _ensure_runtime_agent_defaults,
            load_plugins,
        )

        load_plugins()
        # Runtime-native tools (memory.read / memory.write) are registered by the
        # runtime agent defaults, NOT by load_plugins — the runtime manifest carries
        # no plugin modules. Ensure them here so tools resolve in EVERY process that
        # executes a tool, including the nodus_worker subprocess whose only tool-load
        # entry point is this function. Without it the nodus_vm call_tool seam returns
        # "Tool not found" for runtime tools (RTR-1 parity blocker). Idempotent.
        _ensure_runtime_agent_defaults()
        # ECOGAP-4 / G4b — register client-side MCP tools here for the same reason
        # (available wherever execute_tool runs). No-op unless AINDY_MCP_CLIENT_ENABLED;
        # memoized so the network discovery runs at most once per process. platform-only
        # must stay manifest-empty, so this is wired here rather than via a plugin entry.
        _ensure_mcp_client_tools()
        # ECOGAP-4 / G4a (MEB-2a) — register config-driven capability policies + secret
        # scopes here too, so the (dormant) enforce_capability_policy / resolve_secret gates
        # in execute_tool are active in EVERY process. No-op unless the config env is set.
        _ensure_capability_governance()
    except Exception as exc:
        logger.debug("agent tool plugin load skipped: %s", exc)
    finally:
        _LOADING_PLUGINS = False


_MCP_CLIENT_LOADED = False
_GOVERNANCE_LOADED = False


def _ensure_mcp_client_tools() -> None:
    """Bootstrap client-side MCP tools once per process (memoized, boot-safe)."""
    global _MCP_CLIENT_LOADED
    if _MCP_CLIENT_LOADED:
        return
    _MCP_CLIENT_LOADED = True
    from AINDY.platform_layer import mcp_client

    mcp_client.bootstrap()  # itself a no-op when disabled; never raises


def _ensure_capability_governance() -> None:
    """MEB-2a: register config-driven capability policies + secret scopes once per process
    (memoized). No-op unless AINDY_CAPABILITY_POLICIES / AINDY_SECRET_SCOPES are set; never
    raises (a config-load failure must not break tool execution)."""
    global _GOVERNANCE_LOADED
    if _GOVERNANCE_LOADED:
        return
    _GOVERNANCE_LOADED = True
    try:
        from AINDY.agents.capability_policy import load_capability_policies_from_env
        from AINDY.platform_layer.secret_broker import load_secret_scopes_from_env

        load_capability_policies_from_env()
        load_secret_scopes_from_env()
    except Exception as exc:
        logger.debug("capability governance load skipped: %s", exc)


def register_tool(
    name: str,
    risk: str,
    description: str,
    capability: str,
    required_capability: str,
    category: str,
    egress_scope: str,
    execution_guarantee: str = "AT_LEAST_ONCE",
    isolation: Optional[str] = None,
):
    """Register an agent tool implementation with platform metadata.

    execution_guarantee (MEB-0): "AT_LEAST_ONCE" (default) or "EXACTLY_ONCE". A tool that
    is non-idempotent (send_email, etc.) declares "EXACTLY_ONCE" to opt into the tool-path
    effect boundary — a retry with the same (run, tool, args) replays the cached result
    instead of re-executing. Only active when AINDY_TOOL_IDEMPOTENCY is also enabled.

    isolation (TOOL-SEAM-ISOLATION-1 step B): the **minimum assurance class** the host must
    provide for this tool to run — one of ``"insecure-dev"``, ``"container-grade-sandbox"``,
    ``"strong-sandbox-tier"``. ``None`` (the default) declares nothing and behaves exactly as
    before.

    ★ **This is a DECLARATION, not an application.** A tool declaring
    ``isolation="container-grade-sandbox"`` on a host that cannot provide it is **refused** —
    fail-closed. A tool that IS allowed to run still runs **in-process**; nothing here confines
    it. Step C is the process boundary, and reading this as confinement would be exactly the
    "gated path that does not actually confine" failure the scope warns about.

    ★ **Why an assurance class rather than a mechanism** (``"subprocess"`` / ``"container"`` /
    ``"strong_vm"``, as originally filed). The runtime owns the *request* vocabulary; the
    mechanism stays behind the provider boundary. ``in_process`` and ``subprocess`` are not
    distinguishable as *assurance* — both report ``insecure-dev``, because a bare subprocess is
    not a sandbox — so a mechanism-shaped field would ask callers to state something the
    runtime cannot honour or verify. It also reuses ``EXEC-ENV-BIND-1``'s existing vocabulary
    rather than inventing a second one beside it, which is the same argument that keeps
    ``FS-SCOPE-1`` a field on that descriptor.

    A misspelled class raises at REGISTRATION rather than being silently downgraded — the
    ``register_syscall`` lesson (``IDEM-11``), where an unforwarded parameter left every plugin
    syscall at the weakest setting with no way to opt in.
    """
    if isolation is not None:
        from AINDY.core.execution_environment import ASSURANCE_ORDER

        if isolation not in ASSURANCE_ORDER:
            raise ValueError(
                f"register_tool({name!r}): isolation={isolation!r} is not a known assurance "
                f"class. Expected one of {', '.join(ASSURANCE_ORDER)}. Declaring an unknown "
                f"class must fail loudly — silently downgrading it would hand the tool a weaker "
                f"boundary than it asked for."
            )

    def wrapper(fn: Callable) -> Callable:
        TOOL_REGISTRY[name] = {
            "fn": fn,
            "risk": risk,
            "description": description,
            "capability": capability,
            "required_capability": required_capability,
            "category": category,
            "egress_scope": egress_scope,
            "execution_guarantee": execution_guarantee,
            "isolation": isolation,
        }
        return fn

    return wrapper


def _tool_isolation_enforced() -> bool:
    """Whether a declared tool actually runs out of process (step C2). Default ON.

    ``AINDY_TOOL_ISOLATION=0`` reverts to declare-and-refuse only (step B): the declaration is
    still validated and still refused when the host cannot meet it, but a satisfied declaration
    runs in-process as it did before C2.

    ★ The off switch exists because C2 costs a subprocess round-trip per call. It does NOT make
    the boundary optional per-call — a deployment either enforces declarations or it does not,
    and which one it chose is visible in one place rather than inferred from behaviour.
    """
    return os.getenv("AINDY_TOOL_ISOLATION", "").strip().lower() not in {"0", "false", "no", "off"}


_TOOL_WORKER_TIMEOUT_S = 120.0


def _run_tool_out_of_process(tool_name: str, args: dict, user_id: str) -> dict:
    """Execute a tool in a one-shot worker subprocess (step C2). Returns an execute_tool envelope.

    ★ **This NEVER falls back to in-process, and that is the single most important line here.**
    The Nodus adapter deliberately does fall back — a warm-pool failure spills to a fresh
    subprocess — because there both paths provide the *same* guarantee and the fallback is
    strictly better than failing. Here they do not: falling back would run a tool that asked to
    be confined **unconfined**, which is exactly the "gated path that does not actually confine"
    failure this entry exists to prevent. A worker that crashes, times out, or cannot be spawned
    means the tool does not run.

    ★ Authority is evaluated by the caller before this is reached; the worker only executes.
    Re-checking inside would put the decision in the process the boundary distrusts, and calling
    ``execute_tool`` there would recurse.
    """
    import subprocess

    payload = json.dumps({"tool_name": tool_name, "args": args or {}, "user_id": user_id})
    cmd = [sys.executable, "-m", "AINDY.agents.tool_worker"]
    try:
        proc = subprocess.run(
            cmd,
            input=payload,
            capture_output=True,
            text=True,
            timeout=_TOOL_WORKER_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        logger.error("[AgentTool] %s worker exceeded %ss", tool_name, _TOOL_WORKER_TIMEOUT_S)
        return {
            "success": False,
            "result": None,
            "error": (
                f"tool {tool_name!r} exceeded its {_TOOL_WORKER_TIMEOUT_S}s isolated-execution "
                f"budget. It was NOT retried in-process — it declared isolation."
            ),
        }
    except Exception as exc:  # noqa: BLE001 — spawn failure is a refusal, not a fallback
        logger.error("[AgentTool] %s worker could not be started: %s", tool_name, exc)
        return {
            "success": False,
            "result": None,
            "error": (
                f"tool {tool_name!r} declares isolation and its worker could not be started "
                f"({type(exc).__name__}: {exc}). Running it in-process would defeat the "
                f"declaration, so it was refused."
            ),
        }

    if proc.returncode != 0 or not (proc.stdout or "").strip():
        logger.error(
            "[AgentTool] %s worker exited %s; stderr=%s",
            tool_name,
            proc.returncode,
            (proc.stderr or "")[-400:],
        )
        return {
            "success": False,
            "result": None,
            "error": f"tool {tool_name!r} isolated worker failed (exit {proc.returncode})",
        }

    try:
        response = json.loads(proc.stdout)
    except (TypeError, ValueError) as exc:
        return {
            "success": False,
            "result": None,
            "error": f"tool {tool_name!r} worker returned an unreadable response: {exc}",
        }

    if not response.get("ok"):
        return {"success": False, "result": None, "error": str(response.get("error") or "failed")}
    return {"success": True, "result": response.get("result"), "error": None}


def _check_tool_return(tool_name: str, entry: dict, result: Any) -> None:
    """Record whether a tool's return would survive a process boundary (step C1).

    ★ **This measures; it does not reject.** By the time the return is inspected the handler has
    already run and its effect is real. Failing the call here would discard that effect, which is
    strictly worse than passing an awkward value through — the same judgement
    ``SyscallDispatcher`` made on the syscall path ("a ledger failure must never turn that into a
    caller-visible error"), and the two boundaries must not disagree about it.

    ★ **Why it exists at all: it is the gate on step C.** A tool cannot run behind a process
    boundary unless its return marshals — you cannot hand a ``UUID``, a session, or a live object
    across a pipe. All 18 tools that exist already return a dict, and every one is typed
    ``-> dict``, but **nothing enforced it**, so "they all comply" was an assumption. This turns
    it into ``aindy_tool_return_contract_violations_total``: a non-zero count is exactly the list
    of tools that cannot be moved behind that boundary yet.

    ★ A tool that DECLARED an isolation class is logged at ERROR rather than WARNING. It has
    opted into a boundary its return cannot cross, so the mismatch is a defect in that tool
    rather than an observation about an in-process one.
    """
    declared = entry.get("isolation")
    reason = None

    if not isinstance(result, dict):
        reason = "not_a_dict"
    else:
        try:
            json.dumps(result)
        except (TypeError, ValueError):
            reason = "not_json_serializable"

    if reason is None:
        return

    try:
        from AINDY.platform_layer.metrics import tool_return_contract_violations_total

        tool_return_contract_violations_total.labels(
            reason=reason, declared_isolation=str(declared or "none")
        ).inc()
    except Exception:  # pragma: no cover - observability must never affect the effect path
        pass

    log = logger.error if declared else logger.warning
    log(
        "[AgentTool] %s return violates the tool contract (%s, type=%s)%s. The call SUCCEEDED and "
        "its effect stands; this is recorded because such a return cannot cross a process "
        "boundary (TOOL-SEAM-ISOLATION-1 step C).",
        tool_name,
        reason,
        type(result).__name__,
        f" — and this tool declares isolation={declared!r}, so it cannot be confined until fixed"
        if declared
        else "",
    )


def _isolation_refusal(tool_name: str, entry: dict) -> Optional[dict]:
    """Refuse a tool whose declared isolation this host cannot provide (fail-closed).

    Returns an error envelope to return to the caller, or ``None`` to proceed.

    ★ An envelope, not an exception: ``execute_tool``'s contract is ``{success, result, error}``
    and every caller reads it that way. A refusal that raised would be caught by the seam's own
    broad handler and reported as a tool *failure*, which reads to a caller as "the tool broke"
    rather than "this host cannot run it" — the same status-code confusion ``ROUTE-GUARD-1`` was.

    ★ Resolution failure refuses. ``_host_assurance`` reports the weakest class on any error, so
    a broken provider denies a strict declaration rather than admitting it. Failing toward
    refusal is the only safe direction for a boundary check.
    """
    declared = entry.get("isolation")
    if not declared:
        return None

    from AINDY.core.execution_environment import _host_assurance, assurance_rank

    host_class, evidence = _host_assurance()
    if assurance_rank(host_class) >= assurance_rank(declared):
        return None

    logger.warning(
        "[AgentTool] %s REFUSED: declares isolation=%s, host provides %s (%s)",
        tool_name,
        declared,
        host_class,
        evidence,
    )
    return {
        "success": False,
        "result": None,
        "error": (
            f"tool {tool_name!r} requires isolation {declared!r}; this host provides "
            f"{host_class!r}. The tool was not executed."
        ),
    }


def _tool_idempotency_enabled() -> bool:
    return os.getenv("AINDY_TOOL_IDEMPOTENCY", "").strip().lower() in {"1", "true", "yes"}


def _finalize_tool_effect(db, action_id: str, status: str, result, tool_name: str) -> None:
    """Finalize an EffectRecord best-effort — a ledger failure must never mask the tool
    outcome. On success, cache a JSON-safe result for replay; on failure, cache nothing
    (a ``failed``/left-``pending`` row does not block a later retry)."""
    from AINDY.kernel.effect_ledger import complete_effect_record

    payload = None
    if status == "success":
        try:
            import json as _json

            _json.dumps(result)
            payload = {"result": result}
        except (TypeError, ValueError):
            logger.warning(
                "[AgentTool] %s EXACTLY_ONCE result is not JSON-serializable; "
                "caching empty (replay will return None)",
                tool_name,
            )
            payload = {"result": None}
    try:
        complete_effect_record(db, action_id, status, payload)
    except Exception as exc:
        logger.warning("[AgentTool] %s effect finalize (%s) failed: %s", tool_name, status, exc)


def register_tool_suggestion_provider(provider: Callable) -> Callable:
    """Register a callable that can suggest tools for an optional context snapshot."""
    if provider not in _SUGGESTION_PROVIDERS:
        _SUGGESTION_PROVIDERS.append(provider)
    return provider


from AINDY.kernel.cancellation import is_run_cancelled, note_effect_refused


def execute_tool(
    tool_name: str,
    args: dict,
    user_id: str,
    db,
    run_id: str = None,
    execution_token: dict = None,
) -> dict:
    """Execute a registered tool by name and return a normalized result."""
    _ensure_tools_loaded()
    entry = TOOL_REGISTRY.get(tool_name)
    if not entry:
        return {
            "success": False,
            "result": None,
            "error": f"Tool '{tool_name}' not found in registry",
        }
    if run_id and execution_token is None:
        return {
            "success": False,
            "result": None,
            "error": "capability token is required for agent run tool execution",
        }
    # AGENT-HARDEN-9 — capabilities the tool may resolve secrets under (from the token).
    _scoped_caps: list = []
    _egress_domains: set = set()  # MEB-2b — socket-level allowlist for this tool's caps
    if execution_token is not None:
        if not run_id:
            return {
                "success": False,
                "result": None,
                "error": "run_id is required when execution_token is supplied",
            }
        try:
            from AINDY.agents.capability_service import check_tool_capability

            capability_check = check_tool_capability(
                token=execution_token,
                run_id=run_id,
                user_id=user_id,
                tool_name=tool_name,
            )
            if not capability_check["ok"]:
                queue_system_event(
                    db=db,
                    event_type="capability.denied",
                    user_id=user_id,
                    trace_id=str(run_id),
                    payload={
                        "run_id": str(run_id),
                        "tool_name": tool_name,
                        "error": capability_check["error"],
                        "allowed_capabilities": capability_check.get("allowed_capabilities", []),
                        "granted_tools": capability_check.get("granted_tools", []),
                    },
                    required=True,
                )
                return {
                    "success": False,
                    "result": None,
                    "error": capability_check["error"],
                }
            queue_system_event(
                db=db,
                event_type="capability.allowed",
                user_id=user_id,
                trace_id=str(run_id),
                payload={
                    "run_id": str(run_id),
                    "tool_name": tool_name,
                    "allowed_capabilities": capability_check.get("allowed_capabilities", []),
                    "granted_tools": capability_check.get("granted_tools", []),
                },
                required=True,
            )
            _scoped_caps = list(capability_check.get("allowed_capabilities", []) or [])

            # AGENT-HARDEN-8 — declarative per-capability policy (recipient / domain
            # egress allowlists). Vacuous unless a policy is registered for one of the
            # tool's required capabilities, so no behavior change until opted in.
            from AINDY.agents.capability_policy import (
                enforce_capability_policy,
                enforce_capability_rate,
                get_capability_policy,
                has_capability_policies,
            )

            if has_capability_policies():
                from AINDY.agents.capability_service import _get_capabilities_for_tool

                _tool_caps = _get_capabilities_for_tool(tool_name)
                # MEB-2b — collect the domain allowlist for socket-level egress enforcement
                # (applied around the fn call below when AINDY_EGRESS_ENFORCEMENT is on).
                for _c in _tool_caps:
                    _pol = get_capability_policy(_c)
                    if _pol is not None and _pol.domains:
                        _egress_domains.update(_pol.domains)

                def _deny_policy(result):
                    queue_system_event(
                        db=db,
                        event_type="capability.policy_denied",
                        user_id=user_id,
                        trace_id=str(run_id),
                        payload={
                            "run_id": str(run_id),
                            "tool_name": tool_name,
                            "violations": result["violations"],
                        },
                        required=True,
                    )

                policy_result = enforce_capability_policy(_tool_caps, args)
                if not policy_result["allowed"]:
                    _deny_policy(policy_result)
                    first = policy_result["violations"][0]
                    return {
                        "success": False,
                        "result": None,
                        "error": (
                            f"capability policy violation: {first['kind']} "
                            f"{first['value']!r} not allowed by capability "
                            f"'{first['capability']}'"
                        ),
                    }

                # Rate limits are checked last (they increment a counter, so only
                # otherwise-permitted calls count toward the window).
                rate_result = enforce_capability_rate(_tool_caps, scope=str(user_id))
                if not rate_result["allowed"]:
                    _deny_policy(rate_result)
                    first = rate_result["violations"][0]
                    return {
                        "success": False,
                        "result": None,
                        "error": (
                            f"capability rate limit exceeded: '{first['capability']}' "
                            f"over {first['limit']}/{first['window_secs']}s"
                        ),
                    }
        except Exception as exc:
            logger.warning("[AgentTool] %s capability check failed: %s", tool_name, exc)
            return {
                "success": False,
                "result": None,
                "error": "capability enforcement failed",
            }
    # MEB-0 — tool-path effect boundary (idempotency). Doubly-gated and opt-in: the global
    # AINDY_TOOL_IDEMPOTENCY flag AND a per-tool execution_guarantee of EXACTLY_ONCE, with a
    # stable run scope. Default AT_LEAST_ONCE = current behavior (no dedup). Keys only on
    # EffectRecord.action_id (text) — never the ExecutionUnit UUID — so it sidesteps the
    # #157 lookup path. See docs/runtime/MEDIATED_EFFECT_BOUNDARY_PROGRAM.md (MEB-0).
    # DUR-2 — a continued run's per-run at-most-once signal engages the gate for ANY tool
    # (declaration-free), independent of the tool's guarantee + AINDY_TOOL_IDEMPOTENCY.
    from AINDY.kernel.effect_ledger import durable_effects_active

    _durable = durable_effects_active()
    _guarantee = str(entry.get("execution_guarantee", "AT_LEAST_ONCE")).upper()
    _idempotent = (
        (_guarantee == "EXACTLY_ONCE" or _durable)
        and bool(run_id)
        and (_tool_idempotency_enabled() or _durable)
    )
    _action_id = None
    if _idempotent:
        from AINDY.core.execution_gate import compute_action_id
        from AINDY.kernel.effect_ledger import resolve_effect_record

        _action_id = compute_action_id(
            action_type=tool_name, input_payload=args or {}, scope=str(run_id)
        )
        try:
            _already, _cached = resolve_effect_record(
                db, _action_id, tool_name, args or {},
                # MEB-3b — attribute the effect to the caller (tenant_id == user_id).
                tenant_id=str(user_id) if user_id else None,
            )
        except Exception as exc:
            # A ledger failure must not block the tool — degrade to AT_LEAST_ONCE.
            logger.warning("[AgentTool] %s effect resolve failed; running unguarded: %s", tool_name, exc)
            _already, _cached, _idempotent = False, None, False
        else:
            if _already:
                return {
                    "success": True,
                    "result": (_cached or {}).get("result") if isinstance(_cached, dict) else None,
                    "error": None,
                    "idempotent_replay": True,
                }
    # MEB-2b — socket-level egress chokepoint. When a domain policy applies to this tool's
    # capability and AINDY_EGRESS_ENFORCEMENT is on, enforce the allowlist at DNS resolution
    # for the duration of the fn call — catching runtime-built URLs that MEB-2a's static
    # arg-string inspection misses. Inert otherwise. See MEDIATED_EFFECT_BOUNDARY_PROGRAM.md.
    _egress_cm = contextlib.nullcontext()
    if _egress_domains:
        from AINDY.platform_layer.egress_guard import egress_enforcement_enabled

        if egress_enforcement_enabled():
            from AINDY.platform_layer.egress_guard import egress_scope, install_egress_guard

            install_egress_guard()
            _egress_cm = egress_scope(_egress_domains)
    try:
        # AGENT-HARDEN-9 — a tool that calls resolve_secret(name) during execution is
        # gated by the run's granted capabilities via this ambient scope; the secret
        # is consumed inside the tool and never returned to the script.
        from AINDY.platform_layer.secret_broker import capability_scope
        from AINDY.agents.tool_session import RevocableToolSession

        # TOOL-SEAM-ISOLATION-1 step A — hand the tool a REVOCABLE HANDLE, not the live session.
        #
        # The Linux fd model: pass an opaque handle across a trust boundary, resolved through a
        # table you can validate and revoke — never a direct pointer. `execute_tool` already
        # resolves the tool by NAME through TOOL_REGISTRY (handle-shaped, correct) and then
        # handed over a live SQLAlchemy Session, which cannot be validated, revoked mid-call or
        # narrowed. Every authority check above was advisory with respect to that one argument.
        #
        # ★ Measured before changing it: of the 18 tool functions that exist (3 here, 15 in
        # aindy-apps-monolith), 18 take `db` and 0 reference `db.<anything>`. Pure ambient
        # authority with zero utility — so this narrowing breaks nothing that exists. Same
        # evidence GUEST-CONFINE-1 gathered before denying its three capabilities.
        #
        # ★ The runtime keeps the REAL session: _finalize_tool_effect runs after the tool
        # returns and needs it, and revoke() deliberately does not close it — closing a
        # request-shared session out from under its owner is RT-MEMTXN-LEAK-1.
        #
        # ★ This narrows ONE ARGUMENT. It does not bound the process: a tool can still import
        # os, spawn a thread, or open a socket. Do not read this as the entry being closed.
        # TOOL-SEAM-ISOLATION-1 step C2 — a tool that declared isolation runs OUT OF PROCESS.
        # Placed after the refusal below? No: refusal must come first, because a tool whose class
        # the host cannot meet must not be spawned at all. See the ordering immediately below.
        # TOOL-SEAM-ISOLATION-1 step B — refuse before doing anything, if the tool declared an
        # isolation class this host cannot provide. Placed here, after the authority checks and
        # before the handle is minted, so a refused tool touches nothing at all.
        _refusal = _isolation_refusal(tool_name, entry)
        if _refusal is not None:
            if _idempotent:
                _finalize_tool_effect(db, _action_id, "failed", None, tool_name)
            return _refusal

        # ★ After the refusal, before the in-process handle: a declared tool never reaches the
        # in-process path at all. The result still goes through the effect ledger and the return
        # contract check below, so a confined tool is accounted for exactly like a local one.
        if entry.get("isolation") and _tool_isolation_enforced():
            _isolated = _run_tool_out_of_process(tool_name, args or {}, user_id)
            if _idempotent:
                _finalize_tool_effect(
                    db,
                    _action_id,
                    "success" if _isolated.get("success") else "failed",
                    _isolated.get("result"),
                    tool_name,
                )
            if _isolated.get("success"):
                _check_tool_return(tool_name, entry, _isolated.get("result"))
            return _isolated

        # ── CANCEL-REACH-1: observe cancellation BEFORE the effect, not after ────
        # `sys.v1.agent.cancel` commits a terminal status in a separate session, and the Nodus
        # chain only checked it between SEGMENTS — so every remaining tool in the current
        # segment ran to completion. Checking here narrows that to effect granularity.
        #
        # ★ Cooperative, not preemptive: a tool already running is not interrupted, the NEXT one
        # is refused. Hard-kill is a function of isolation class and belongs to
        # TOOL-SEAM-ISOLATION-1; in-process degrades to this and says so.
        #
        # ★ Placed immediately before `entry["fn"]` and after the effect-ledger reservation, so
        # a refusal cannot leave a reserved effect that never resolves.
        if is_run_cancelled(run_id):
            note_effect_refused(surface="tool")
            logger.info(
                "[AgentTool] %s refused — run %s is cancelled", tool_name, run_id
            )
            return {
                "success": False,
                "result": None,
                "error": f"run {run_id} was cancelled; tool {tool_name!r} not executed",
                "cancelled": True,
            }

        _tool_db = RevocableToolSession(db, tool_name=tool_name)
        try:
            with _egress_cm, capability_scope(_scoped_caps):
                result = entry["fn"](args=args, user_id=user_id, db=_tool_db)
        finally:
            _tool_db.revoke()
        _check_tool_return(tool_name, entry, result)
        if _idempotent:
            _finalize_tool_effect(db, _action_id, "success", result, tool_name)
        return {"success": True, "result": result, "error": None}
    except Exception as exc:
        logger.warning("[AgentTool] %s failed: %s", tool_name, exc)
        if _idempotent:
            _finalize_tool_effect(db, _action_id, "failed", None, tool_name)
        return {"success": False, "result": None, "error": str(exc)}


def get_tool_risk(tool_name: str) -> str:
    """Return risk level of a registered tool, or 'high' if unknown."""
    _ensure_tools_loaded()
    entry = TOOL_REGISTRY.get(tool_name)
    return entry["risk"] if entry else "high"


def suggest_tools(
    suggestion_context: dict | None = None,
    user_id: str = None,
    db=None,
    **legacy_kwargs,
) -> list:
    """Return tool suggestions from registered providers.

    The runtime treats the suggestion context as opaque. App-owned providers may
    interpret it however they choose, or may derive their own context from
    plugin-owned jobs and services when it is absent.
    """
    _ensure_tools_loaded()
    if suggestion_context is None and "kpi_snapshot" in legacy_kwargs:
        suggestion_context = legacy_kwargs["kpi_snapshot"]
    sanitized_context = sanitize_extension_context(suggestion_context or {})
    for provider in tuple(_SUGGESTION_PROVIDERS):
        try:
            suggestions = provider(
                suggestion_context=sanitized_context,
                user_id=user_id,
            )
        except TypeError:
            suggestions = provider(kpi_snapshot=sanitized_context, user_id=user_id)
        except Exception as exc:
            logger.warning("[AgentTools] suggestion provider failed: %s", exc)
            continue
        if suggestions:
            return suggestions[:3]
    return []
