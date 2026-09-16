from AINDY.core.retry_policy import decide_retry
from AINDY.runtime.flow_engine.node_executor import resolve_frontier, resolve_next_node
from AINDY.runtime.flow_engine.registry import FLOW_REGISTRY
from AINDY.runtime.flow_engine.runner_completion import maybe_finalize_completion
from AINDY.runtime.flow_engine.serialization import (
    _format_execution_response,
    _json_safe,
    _serialize_flow_events,
)
from AINDY.runtime.flow_engine.shared import (
    SystemEventTypes,
    _default_wait_deadline,
    _resolve_retry_policy,
    logger,
    queue_system_event,
    reset_parent_event_id,
    set_parent_event_id,
    time,
)


def _get_flow_wait_timeout(flow_name: str) -> int | None:
    flow_def = FLOW_REGISTRY.get(flow_name)
    if flow_def is None:
        return None
    return flow_def.get("wait_timeout_minutes")


def _claim_waiting_run(self, run, db_run_id: str):
    from AINDY.db.models.flow_run import FlowRun

    if run.status != "waiting":
        return None

    claimed = (
        self.db.query(FlowRun)
        .filter(FlowRun.id == db_run_id, FlowRun.status == "waiting")
        .update(
            {
                "status": "executing",
                "waiting_for": None,
                "wait_deadline": None,
            },
            synchronize_session=False,
        )
    )
    try:
        self.db.commit()
    except Exception as exc:
        logger.warning(
            "[Flow] resume claim commit failed for run=%s: %s",
            db_run_id,
            exc,
        )
        try:
            self.db.rollback()
        except Exception:
            pass
        return _format_execution_response(
            status="SKIPPED",
            trace_id=db_run_id,
            result={
                "skipped": True,
                "reason": "claim commit failed - concurrent resume likely",
            },
            events=[],
            next_action=None,
            run_id=db_run_id,
        )

    if claimed == 0:
        logger.info(
            "[Flow] resume skipped: run=%s already claimed by another instance",
            db_run_id,
        )
        return _format_execution_response(
            status="SKIPPED",
            trace_id=db_run_id,
            result={
                "skipped": True,
                "reason": "already claimed by another instance",
            },
            events=[],
            next_action=None,
            run_id=db_run_id,
        )
    run.status = "executing"
    return None


def _execute_current_node(
    self,
    run,
    state: dict,
    context: dict,
    current_node: str,
    node_started_event_id,
) -> dict:
    node_parent_token = set_parent_event_id(
        str(node_started_event_id) if node_started_event_id else self._root_event_id
    )
    try:
        resource_response = self._check_resources(
            run,
            state,
            current_node,
            node_started_event_id,
        )
        if resource_response is not None:
            return {"final_response": resource_response}

        node_t_start = time.monotonic()
        try:
            from AINDY.runtime import flow_engine as flow_engine_module

            result = flow_engine_module.execute_node(current_node, state, context)
        except PermissionError as exc:
            self._queue_node_failure(run, current_node, node_started_event_id, str(exc))
            return {
                "final_response": self._fail_execution(
                    str(exc),
                    failed_node=current_node,
                    parent_event_id=str(node_started_event_id)
                    if node_started_event_id
                    else None,
                )
            }
        except Exception as exc:
            logger.error("Node %s raised exception: %s", current_node, exc)
            self._queue_node_failure(run, current_node, node_started_event_id, str(exc))
            return {
                "final_response": self._fail_execution(
                    str(exc),
                    failed_node=current_node,
                    parent_event_id=str(node_started_event_id)
                    if node_started_event_id
                    else None,
                )
            }

        exec_ms = result.get("_execution_time_ms", 0) or int(
            (time.monotonic() - node_t_start) * 1000
        )
        self._record_resource_usage(exec_ms)
        return {"result": result, "exec_ms": exec_ms, "final_response": None}
    finally:
        reset_parent_event_id(node_parent_token)


def _check_resources(self, run, state: dict, current_node: str, node_started_event_id):
    """Admission + per-unit budget, at node entry.

    ★ **The tenant slot is acquired HERE, once per acquisition, and only while the runner holds
    none** (`ACTIVE-COUNT-WAIT-LEAK-1`). A run holds a slot exactly while it is executing:
    acquired at its first node after a start OR a resume, released by every WAIT
    (`_release_slot_on_wait`) and by completion/failure. Before this the slot was taken
    unconditionally at start, never released on WAIT, and `can_execute` was re-asked at EVERY
    node with the run's own slot in the count — so at exactly `MAX_CONCURRENT_PER_TENANT` the
    last-admitted run parked itself on `resource_available` while still holding the slot it
    was being refused for.

    ★ Admission is judged against a count that does NOT include this run — it holds nothing
    when it asks. A refusal parks the run on `resource_available` holding nothing, so a parked
    run costs the tenant no admission budget, which is what "parked as a row" promised.

    ★ `can_execute` short-circuits to True under `settings.is_testing`; a test of the refusal
    path patches `is_testing` on the settings class (it is a pydantic property).
    """
    try:
        from AINDY.kernel.resource_manager import get_resource_manager as get_rm

        rm = get_rm()
        tenant_id = getattr(self, "_tenant_id", str(self.user_id or ""))
        eu_id_str = str(getattr(self, "_eu_id", "") or "")
        if getattr(self, "_holds_slot", False):
            can_run, run_reason = True, None
        else:
            can_run, run_reason = rm.can_execute(tenant_id, eu_id_str)
            if can_run:
                rm.mark_started(tenant_id, eu_id_str or None)
                self._holds_slot = True
        if not can_run:
            run.status = "waiting"
            run.waiting_for = "resource_available"
            _timeout = _get_flow_wait_timeout(run.flow_name)
            run.wait_deadline = _default_wait_deadline(_timeout)
            run.current_node = current_node
            run.state = _json_safe(state)
            self.db.commit()
            self._park_execution_unit(run, "resource_available")
            try:
                from AINDY.core.flow_run_rehydration import build_flow_resume_callback
                from AINDY.core.wait_condition import WaitCondition
                from AINDY.kernel.scheduler_engine import get_scheduler_engine

                this_run_id = str(run.id)
                this_trace = str(run.trace_id or this_run_id)
                get_scheduler_engine().register_wait(
                    run_id=this_run_id,
                    wait_for_event="resource_available",
                    tenant_id=tenant_id,
                    eu_id=eu_id_str,
                    resume_callback=build_flow_resume_callback(
                        r_id=this_run_id,
                        flow_name=run.flow_name,
                        user_id=self.user_id,
                        workflow_type=self.workflow_type or "flow",
                        eid=eu_id_str,
                    ),
                    priority=getattr(self, "priority", "normal"),
                    correlation_id=this_trace,
                    trace_id=this_trace,
                    eu_type="flow",
                    wait_condition=WaitCondition.for_event(
                        "resource_available",
                        correlation_id=this_trace,
                    ),
                )
            except Exception as exc:
                logger.debug("[Flow] scheduler register_wait skipped: %s", exc)
            return _format_execution_response(
                status="WAITING",
                trace_id=run.trace_id or str(run.id),
                result={"waiting_for": "resource_available", "reason": run_reason},
                events=_serialize_flow_events(self.db, run.id),
                next_action=None,
                run_id=run.id,
                state=state,
            )

        quota_ok, quota_reason = rm.check_quota(eu_id_str)
        if not quota_ok:
            return self._fail_execution(
                quota_reason,
                failed_node=current_node,
                parent_event_id=str(node_started_event_id)
                if node_started_event_id
                else None,
            )
    except (ImportError, AttributeError) as exc:
        logger.debug("[Flow] resource check skipped: %s", exc)
    return None


def _release_slot_on_wait(self, run, wait_for: str) -> None:
    """ACTIVE-COUNT-WAIT-LEAK-1 — a parked run holds no tenant slot.

    Releases through `mark_waiting` (slot back, usage snapshot kept — the run will resume and
    keep accruing), flips `_holds_slot` so the next node entry re-acquires through admission,
    and moves the run's own ExecutionUnit to `waiting` so the durable record agrees with the
    row (`flow|flow_run` units were observed stuck `executing` for parked runs).
    """
    if not getattr(self, "_holds_slot", False):
        return
    self._holds_slot = False
    try:
        from AINDY.kernel.resource_manager import get_resource_manager as get_rm

        eu_id = getattr(self, "_eu_id", None)
        get_rm().mark_waiting(
            getattr(self, "_tenant_id", str(self.user_id or "")),
            str(eu_id) if eu_id else None,
        )
    except Exception as exc:
        logger.debug("[EU] resource_manager.mark_waiting skipped: %s", exc)
    self._park_execution_unit(run, wait_for)


def _park_execution_unit(self, run, wait_for: str) -> None:
    """Move the run's own EU to `waiting` with its condition; non-fatal like every EU hook."""
    eu_id = getattr(self, "_eu_id", None)
    if not eu_id:
        return
    try:
        from AINDY.core.execution_unit_service import ExecutionUnitService
        from AINDY.core.wait_condition import WaitCondition

        eus = ExecutionUnitService(self.db)
        eus.update_status(eu_id, "waiting")
        eus.set_wait_condition(
            eu_id,
            WaitCondition.for_event(wait_for, correlation_id=str(run.trace_id or run.id)),
        )
    except Exception as exc:
        logger.debug("[EU] park to waiting skipped for eu=%s: %s", eu_id, exc)


def _queue_node_failure(self, run, current_node: str, node_started_event_id, error: str) -> None:
    queue_system_event(
        db=self.db,
        event_type=SystemEventTypes.FLOW_NODE_FAILED,
        user_id=self.user_id,
        trace_id=run.trace_id or str(run.id),
        parent_event_id=node_started_event_id,
        source="flow",
        payload={
            "run_id": str(run.id),
            "workflow_type": self.workflow_type,
            "node": current_node,
            "error": error,
        },
        required=True,
    )


def _record_resource_usage(self, exec_ms: int) -> None:
    try:
        from AINDY.kernel.resource_manager import get_resource_manager as get_rm

        eu_id_str = str(getattr(self, "_eu_id", "") or "")
        if eu_id_str:
            get_rm().record_usage(
                eu_id_str,
                {"wall_time_ms": exec_ms, "syscall_count": 0},
            )
    except Exception as exc:
        logger.debug("[Flow] resource record skipped: %s", exc)


from AINDY.runtime.flow_engine.state_merge import declared_policies, merge_state

# The node statuses whose output patch lands on the run's state. FAILURE and RETRY produce no
# state; SUCCESS always did; WAIT joined 2026-09-13 (`NODUS-RESUME-BRIDGE-1`) — see
# `_merge_superstep`. `AINDY/core/flow_history_fold.py` mirrors this set and a test pins the two
# equal, because a fold that disagrees with the engine reconstructs a state the engine never had.
_MERGED_STATUSES = frozenset({"SUCCESS", "WAIT"})


def _merge_superstep(self, state: dict, outcomes: list[dict]) -> dict:
    """FLOW-PARALLEL-1 phase 0 — merge a superstep's successful branches into ``state``.

    ``outcomes`` is the superstep's branches in **declaration order** — the order they appear in
    the flow definition, never the order they completed. Today a superstep is exactly one node,
    so this is a one-element list and `merge_state` is byte-for-byte ``state.update(patch)``.

    ★ **This is the widened transaction, and it is on the live path deliberately.** The first
    fan-out has to be written against the seam the engine actually uses; a merge helper that
    exists beside the real path is `ROUTE-AST-UNWIRED-1`, which this repository has catalogued.

    ★ **SUCCESS and WAIT branches contribute; FAILURE and RETRY never do.** Until 2026-09-13
    only SUCCESS did — the rule the engine had when the merge lived in `_handle_node_status`,
    preserved through the phase-0 refactor because changing it there would have been a
    behaviour change smuggled inside a refactor. It was then changed HERE, deliberately, as
    `NODUS-RESUME-BRIDGE-1`: a WAIT patch is the node's durable request for its own re-run
    (`nodus_wait_event_type` is how `nodus.execute` knows, on re-entry, which wait the injected
    `event` answers), and a patch that reaches `flow_history.output_patch` but never
    `flow_runs.state` is a patch the re-run cannot see. Observed live: `WAIT, WAIT, WAIT…` —
    every resume re-parked the script because the bridge found no pending type.
    `flow_history_fold.py` applies the same rule so the DUR-4 reconstruction stays in parity.

    ★ A WAIT patch can only ever be the single node of a one-node superstep: `_execute_superstep`
    refuses a WAIT inside a fan-out group before anything is written, so a group's merge still
    sees SUCCESS patches only.

    ★ **One `merge_state` call for the whole superstep, never one per branch.** Per-branch calls
    would apply patches in completion order regardless of the declared policy, which is the
    nondeterminism `state_merge` exists to prevent.

    ★ Merging on the RUNNER's session, single-threaded, is design section 3c: branches will hold
    their own sessions (`AGENT_WORKING_RULES` section 5 forbids sharing one), so shared state
    must be written by exactly one writer.
    """
    landed = [
        (outcome["node"], outcome.get("patch") or {})
        for outcome in outcomes
        if outcome.get("status") in _MERGED_STATUSES
    ]
    if not landed:
        return state
    return merge_state(
        state,
        landed,
        policies=declared_policies(getattr(self, "flow", {}) or {}),
    )


def _handle_node_status(
    self,
    run,
    state: dict,
    context: dict,
    current_node: str,
    result: dict,
    patch: dict,
    node_status: str,
    node_started_event_id,
):
    if node_status == "SUCCESS":
        # FLOW-PARALLEL-1 phase 0 — the merge MOVED OUT of here, to `_merge_superstep` on the
        # runner. It is not gone: state is already merged by the time this is called.
        #
        # ★ Why it could not stay: a merge belongs to the SUPERSTEP, not to one node's status.
        # `merge_state` must receive every successful branch's patch together, in declaration
        # order, or `last_write_wins` resolves by completion order and the module's whole
        # determinism guarantee is void. Called per node it can only ever see one patch — so
        # this location was correct exactly while a superstep was guaranteed to be one node,
        # and fan-out is the change that ends that guarantee.
        #
        # ★ It also has to run on the RUNNER's session, single-threaded (design section 3c):
        # branches get their own sessions and must not write shared state.
        #
        # WAIT-TYPED-CONTRACT-1 — a node that succeeded is no longer asking for anything: the
        # pending request its WAIT recorded is consumed. Left in place it would type the NEXT
        # wait on this run with a schema nobody declared for it.
        from AINDY.core.pending_request import PENDING_REQUEST_KEY

        state.pop(PENDING_REQUEST_KEY, None)
    elif node_status == "RETRY":
        attempts = context["attempts"].get(current_node, 0)
        node_cfg = self.flow.get("node_configs", {}).get(current_node, {})
        run_policy = _resolve_retry_policy(
            execution_type="flow",
            node_max_retries=node_cfg.get("max_retries"),
        )
        # RETRY-CLASSIFY-1 — classify the node's whole result (a `failure_class` the node
        # declared wins; the substring table is the fallback), decide, and count the decision.
        _should_retry, _failure = decide_retry(
            result if isinstance(result, dict) else None,
            site="flow_node", attempt=attempts, attempts_allowed=attempts < run_policy.max_attempts,
        )
        if _should_retry:
            logger.warning("Node %s retrying (attempt %d)", current_node, attempts)
            return "retry"
        return self._fail_execution(
            f"Node {current_node} failed after {attempts} retries",
            failed_node=current_node,
            parent_event_id=str(node_started_event_id) if node_started_event_id else None,
        )
    elif node_status == "FAILURE":
        return self._fail_execution(
            result.get("error", f"Node {current_node} failed"),
            failed_node=current_node,
            parent_event_id=str(node_started_event_id) if node_started_event_id else None,
        )
    elif node_status == "WAIT":
        wait_for = result.get("wait_for")
        if not wait_for:
            return self._fail_execution(
                f"Node {current_node} returned WAIT without wait_for",
                failed_node=current_node,
                parent_event_id=str(node_started_event_id) if node_started_event_id else None,
            )
        # WAIT-TYPED-CONTRACT-1 — the node may declare what is allowed to resume it. Recorded on
        # the run beside the wait (a runner-level write, like `route_event`'s `state["event"]`;
        # NOT the node's patch, so `flow_history.output_patch` stays what the node returned), and
        # checked by `route_event` before a payload is injected. No declaration → no record →
        # the wait is untyped, exactly as before. A malformed declaration fails the node LOUDLY:
        # silently degrading to untyped would make a typo indistinguishable from no guard.
        try:
            from AINDY.core.pending_request import (
                PENDING_REQUEST_KEY,
                RESUME_SCHEMA_KEY,
                build_pending_request,
            )

            pending = build_pending_request(
                node=current_node, event=wait_for, schema=result.get(RESUME_SCHEMA_KEY)
            )
        except ValueError as exc:  # InvalidResumeSchema
            return self._fail_execution(
                str(exc),
                failed_node=current_node,
                parent_event_id=str(node_started_event_id) if node_started_event_id else None,
            )
        if pending is not None:
            state[PENDING_REQUEST_KEY] = pending
        else:
            # This wait declared nothing; a record from an earlier wait must not type it.
            state.pop(PENDING_REQUEST_KEY, None)
        run.status = "waiting"
        run.waiting_for = wait_for
        _timeout = _get_flow_wait_timeout(run.flow_name)
        run.wait_deadline = _default_wait_deadline(_timeout)
        run.state = _json_safe(state)
        run.current_node = current_node
        self.db.commit()
        # ACTIVE-COUNT-WAIT-LEAK-1 — the slot goes back BEFORE the wait is registered, so a
        # tenant at the cap regains a slot the moment its run parks, not when it resumes.
        self._release_slot_on_wait(run, wait_for)
        try:
            from AINDY.core.flow_run_rehydration import build_flow_resume_callback
            from AINDY.core.wait_condition import WaitCondition
            from AINDY.kernel.scheduler_engine import get_scheduler_engine

            wait_run_id = str(run.id)
            wait_trace = str(run.trace_id or wait_run_id)
            get_scheduler_engine().register_wait(
                run_id=wait_run_id,
                wait_for_event=wait_for,
                tenant_id=str(self.user_id or ""),
                eu_id=str(getattr(self, "_eu_id", "") or ""),
                resume_callback=build_flow_resume_callback(
                    r_id=wait_run_id,
                    flow_name=run.flow_name,
                    user_id=self.user_id,
                    workflow_type=self.workflow_type or "flow",
                    eid=str(getattr(self, "_eu_id", "") or ""),
                ),
                priority=getattr(self, "priority", "normal"),
                correlation_id=wait_trace,
                trace_id=wait_trace,
                eu_type="flow",
                wait_condition=WaitCondition.for_event(
                    wait_for,
                    correlation_id=wait_trace,
                ),
            )
        except Exception as exc:
            logger.debug("[Flow] node-WAIT scheduler register_wait skipped: %s", exc)
        queue_system_event(
            db=self.db,
            event_type=SystemEventTypes.FLOW_WAITING,
            user_id=self.user_id,
            trace_id=run.trace_id or str(run.id),
            parent_event_id=node_started_event_id,
            source="flow",
            payload={
                "run_id": str(run.id),
                "workflow_type": self.workflow_type,
                "node": current_node,
                "waiting_for": wait_for,
            },
            required=True,
        )
        return _format_execution_response(
            status="WAITING",
            trace_id=run.trace_id or str(run.id),
            result={"waiting_for": wait_for},
            events=_serialize_flow_events(self.db, run.id),
            next_action=None,
            run_id=run.id,
            state=state,
        )

    return maybe_finalize_completion(
        self,
        run,
        state,
        current_node,
        self._root_event_id,
        node_started_event_id,
    )


def _execute_superstep(
    self,
    run,
    state: dict,
    context: dict,
    branches: list[str],
    node_started_event_id,
) -> str:
    """FLOW-PARALLEL-1 phases 1+2 — run a declared fan-out group as ONE superstep.

    Returns the node the flow continues from. Raises `FanOutWaitRefused` if a branch WAITs and
    `ValueError` if the join is not satisfied or the surviving branches do not converge.

    ★★ **The join is the group's declared policy, resolved at the barrier (phase 2).** `all`
    (the default) is phase 1 exactly: every branch must succeed. `any` and `quorum(k)` proceed
    once enough have — and when they proceed past a failed branch, the superstep is a
    **`partial`** outcome (`EFFECT-PARTIAL-1`): the failed branches are named on the run's
    state under `_superstep_partials`, on the completion event, and on the `flow.run`
    envelope. Never silent. The merge takes only SUCCESS patches here — a WAIT is refused before
    the merge, and no other status carries state — and
    **convergence is required of the branches that succeeded** — a failed branch's successor
    is not consulted, because it produced no state to choose a successor against.

    ★ **The branches never become `run.current_node`.** The run stays parked on the node that
    declared the group until the whole superstep commits, so a crash mid-superstep resumes by
    re-running the group. That is at-least-once for the branches, which is the guarantee the rest
    of the runtime already assumes — `DUR-2` is what stops mediated effects double-firing.

    ★ **The runner's session is the only `FlowHistory` writer** (design §3c). Branches ran on
    their own sessions and returned patches; everything below is on `self.db`, single-threaded.
    """
    from AINDY.runtime.flow_engine.fan_out import (
        FanOutEdgeGroup,
        FanOutWaitRefused,
        resolve_join,
        run_fan_out_branches,
    )
    from AINDY.runtime.flow_engine.node_executor import fan_out_group_for
    from AINDY.db.models.flow_run import FlowHistory

    group = fan_out_group_for(run.current_node, self.flow) or FanOutEdgeGroup(branches)

    def _execute(node, branch_state, branch_context):
        from AINDY.runtime import flow_engine as flow_engine_module

        return flow_engine_module.execute_node(node, branch_state, branch_context)

    input_snapshot = dict(state)
    results = run_fan_out_branches(branches, state, context, _execute)

    # ★ Refuse WAIT before writing anything. Design §5 option 3, approved 2026-09-08: a
    #   suspended branch would need a durable partial-superstep record, and lifting this is
    #   coupled to RECOVERY-GRANULARITY-1. Loud and declared, never discovered.
    waiting = [r["node"] for r in results if (r["result"] or {}).get("status") == "WAIT"]
    if waiting:
        raise FanOutWaitRefused(
            f"branch(es) {waiting} returned WAIT inside the fan-out group from "
            f"{run.current_node!r}. A WAIT inside a group is refused in phase 1: the other "
            "branches' patches would have to be held durably across the suspension, and no "
            "such record exists. Move the waiting node out of the group."
        )

    # ★ Ordinals for the WHOLE superstep, at the barrier, in declaration order (§4). History
    #   order and merge order are then identical by construction rather than by luck.
    ordinals = self._allocate_sequence_numbers(run, len(results))

    patches: list[dict] = []
    for ordinal, entry in zip(ordinals, results):
        node = entry["node"]
        result = entry["result"] or {"status": "FAILURE", "error": entry["error"]}
        status = result.get("status", "FAILURE")
        patch = result.get("output_patch", {}) or {}
        self.db.add(
            FlowHistory(
                flow_run_id=run.id,
                node_name=node,
                status=status,
                input_state=_json_safe(input_snapshot),
                output_patch=_json_safe(patch),
                execution_time_ms=result.get("_execution_time_ms", 0) or 0,
                error_message=result.get("error") or entry["error"],
                sequence_number=ordinal,
            )
        )
        patches.append({
            "node": node, "patch": patch, "status": status,
            "error": result.get("error") or entry["error"],
        })
    self.db.commit()

    # The central merge, on the runner's session, in declaration order (§3c).
    self._merge_superstep(state, patches)

    # ★ The join, at the barrier (phase 2). History is already written for every branch —
    #   the record must show what was attempted whatever the join decides.
    outcome = resolve_join(group, {p["node"]: p["status"] for p in patches})
    if not outcome.satisfied:
        raise ValueError(
            f"branch(es) {list(outcome.failed)} failed in the fan-out group from "
            f"{run.current_node!r}; join={outcome.join} needs {outcome.required} of "
            f"{len(group.targets)} to succeed and {len(outcome.succeeded)} did."
        )
    if outcome.partial:
        # Proceeding past a failure is a PARTIAL outcome, and it is recorded where it is
        # durable (the run's state), not only where it is convenient (a log line).
        logger.warning(
            "[FlowFanOut] superstep from %r proceeds under join=%s with failed branch(es) %s",
            run.current_node, outcome.join, list(outcome.failed),
        )
        partials = list(state.get("_superstep_partials") or [])
        errors = {p["node"]: p.get("error") for p in patches}
        partials.append({
            "superstep": run.current_node,
            "join": outcome.join,
            "succeeded": list(outcome.succeeded),
            "failed": [{"branch": b, "error": errors.get(b)} for b in outcome.failed],
        })
        state["_superstep_partials"] = partials

    # ★ Convergence, enforced over the SUCCEEDED branches. Resolved AFTER the merge so each
    #   branch's successor is chosen against the merged state, which is what a sequential run
    #   would have seen.
    successors = {node: resolve_next_node(node, state, self.flow) for node in outcome.succeeded}
    distinct = set(successors.values())
    if len(distinct) != 1 or None in distinct:
        raise ValueError(
            f"fan-out branches from {run.current_node!r} did not converge: {successors}. "
            "Every succeeding branch of a group must resolve to the same successor."
        )
    return distinct.pop()


def _advance_to_next_node(
    self,
    run,
    state: dict,
    current_node: str,
    node_started_event_id,
):
    # FLOW-PARALLEL-1 phase 1 — the frontier, not the successor.
    #
    # ★ A frontier of ONE is today's path byte-for-byte: `resolve_frontier` delegates to
    #   `resolve_next_node` for both existing edge shapes, so every flow that declares no group
    #   takes exactly the code it took before. That is what makes this a small diff against a
    #   reviewed seam rather than a rewrite of the loop.
    try:
        frontier = resolve_frontier(current_node, state, self.flow)
    except (KeyError, ValueError) as exc:
        # FLOW-PARALLEL-1 phase 3a — an edge that cannot be resolved (a `when` naming an
        # unregistered predicate, a dict edge with both or neither gate, a group beside sibling
        # edges) FAILS THE RUN with the reason, never ends it quietly or escapes as a 500 that
        # leaves the row `executing`. MAF's `_missing_callable`: fail loudly on restore.
        return self._fail_execution(
            f"cannot resolve the edge out of {current_node!r}: {exc}",
            failed_node=current_node,
            parent_event_id=str(node_started_event_id) if node_started_event_id else None,
        )

    if len(frontier) > 1:
        try:
            next_node = self._execute_superstep(
                run, state, self._current_context, frontier, node_started_event_id
            )
        except Exception as exc:  # a refused WAIT, a failed branch, or a non-convergence
            return self._fail_execution(
                str(exc),
                failed_node=current_node,
                parent_event_id=str(node_started_event_id) if node_started_event_id else None,
            )
    else:
        next_node = frontier[0] if frontier else None

    if not next_node:
        return self._fail_execution(
            f"No next node from {current_node} - flow graph incomplete",
            failed_node=current_node,
            parent_event_id=str(node_started_event_id) if node_started_event_id else None,
        )
    run.current_node = next_node
    run.state = _json_safe(state)
    self.db.commit()
    return next_node
