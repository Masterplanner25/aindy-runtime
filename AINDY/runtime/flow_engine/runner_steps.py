from AINDY.core.retry_policy import is_retryable_error
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
    try:
        from AINDY.kernel.resource_manager import get_resource_manager as get_rm

        rm = get_rm()
        tenant_id = getattr(self, "_tenant_id", str(self.user_id or ""))
        eu_id_str = str(getattr(self, "_eu_id", "") or "")
        can_run, run_reason = rm.can_execute(tenant_id, eu_id_str)
        if not can_run:
            run.status = "waiting"
            run.waiting_for = "resource_available"
            _timeout = _get_flow_wait_timeout(run.flow_name)
            run.wait_deadline = _default_wait_deadline(_timeout)
            run.current_node = current_node
            run.state = _json_safe(state)
            self.db.commit()
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


def _merge_superstep(self, state: dict, outcomes: list[dict]) -> dict:
    """FLOW-PARALLEL-1 phase 0 — merge a superstep's successful branches into ``state``.

    ``outcomes`` is the superstep's branches in **declaration order** — the order they appear in
    the flow definition, never the order they completed. Today a superstep is exactly one node,
    so this is a one-element list and `merge_state` is byte-for-byte ``state.update(patch)``.

    ★ **This is the widened transaction, and it is on the live path deliberately.** The first
    fan-out has to be written against the seam the engine actually uses; a merge helper that
    exists beside the real path is `ROUTE-AST-UNWIRED-1`, which this repository has catalogued.

    ★ **Only SUCCESS branches contribute.** That is not a new rule — it is what the engine did
    when the merge lived in `_handle_node_status`'s SUCCESS branch, and a WAIT branch's patch was
    never merged. Preserved exactly, because changing it here would be a behaviour change
    smuggled inside a refactor.

    ★ **One `merge_state` call for the whole superstep, never one per branch.** Per-branch calls
    would apply patches in completion order regardless of the declared policy, which is the
    nondeterminism `state_merge` exists to prevent.

    ★ Merging on the RUNNER's session, single-threaded, is design section 3c: branches will hold
    their own sessions (`AGENT_WORKING_RULES` section 5 forbids sharing one), so shared state
    must be written by exactly one writer.
    """
    successful = [
        (outcome["node"], outcome.get("patch") or {})
        for outcome in outcomes
        if outcome.get("status") == "SUCCESS"
    ]
    if not successful:
        return state
    return merge_state(
        state,
        successful,
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
        pass
    elif node_status == "RETRY":
        attempts = context["attempts"].get(current_node, 0)
        node_cfg = self.flow.get("node_configs", {}).get(current_node, {})
        run_policy = _resolve_retry_policy(
            execution_type="flow",
            node_max_retries=node_cfg.get("max_retries"),
        )
        node_error = result.get("error") if isinstance(result, dict) else None
        if attempts < run_policy.max_attempts and is_retryable_error(node_error):
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
        run.status = "waiting"
        run.waiting_for = wait_for
        _timeout = _get_flow_wait_timeout(run.flow_name)
        run.wait_deadline = _default_wait_deadline(_timeout)
        run.state = _json_safe(state)
        run.current_node = current_node
        self.db.commit()
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
    """FLOW-PARALLEL-1 phase 1 — run a declared fan-out group as ONE superstep.

    Returns the node the flow continues from. Raises `FanOutWaitRefused` if a branch WAITs and
    `ValueError` if the branches do not converge.

    ★★ **Phase 1 requires all branches to converge on the SAME successor**, and enforces it
    rather than picking one. This is the degenerate join — an implicit `all` — and it is the
    narrowest thing that makes fan-out coherent without the join policies phase 2 owns. The
    design says *"fan-out without a join is half a primitive"*; this is the half, made explicit
    instead of left undefined. Declared join policies (`all`, `any`, `quorum(k)`) generalise it.

    ★ **The branches never become `run.current_node`.** The run stays parked on the node that
    declared the group until the whole superstep commits, so a crash mid-superstep resumes by
    re-running the group. That is at-least-once for the branches, which is the guarantee the rest
    of the runtime already assumes — `DUR-2` is what stops mediated effects double-firing.

    ★ **The runner's session is the only `FlowHistory` writer** (design §3c). Branches ran on
    their own sessions and returned patches; everything below is on `self.db`, single-threaded.
    """
    from AINDY.runtime.flow_engine.fan_out import FanOutWaitRefused, run_fan_out_branches
    from AINDY.db.models.flow_run import FlowHistory

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
        patches.append({"node": node, "patch": patch, "status": status})
    self.db.commit()

    # The central merge, on the runner's session, in declaration order (§3c).
    self._merge_superstep(state, patches)

    failed = [p["node"] for p in patches if p["status"] == "FAILURE"]
    if failed:
        raise ValueError(
            f"branch(es) {failed} failed in the fan-out group from {run.current_node!r}. "
            "Phase 1 has no join policy, so a failed branch fails the superstep; `any` and "
            "`quorum(k)` are phase 2."
        )

    # ★ Convergence, enforced. Resolved AFTER the merge so each branch's successor is chosen
    #   against the merged state, which is what a sequential run would have seen.
    successors = {node: resolve_next_node(node, state, self.flow) for node in branches}
    distinct = set(successors.values())
    if len(distinct) != 1 or None in distinct:
        raise ValueError(
            f"fan-out branches from {run.current_node!r} did not converge: {successors}. "
            "Phase 1 requires every branch of a group to resolve to the same successor "
            "(the degenerate `all` join). Declared join policies are phase 2."
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
    frontier = resolve_frontier(current_node, state, self.flow)

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
