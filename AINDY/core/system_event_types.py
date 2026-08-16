from __future__ import annotations


class SystemEventTypes:
    STARTUP_RECOVERY_FAILED = "startup.recovery.failed"
    STARTUP_RECOVERY_COMPLETED = "startup.recovery.completed"

    EXECUTION_STARTED = "execution.started"
    EXECUTION_COMPLETED = "execution.completed"
    EXECUTION_FAILED = "execution.failed"
    EXECUTION_WAITING = "execution.waiting"
    EXECUTION_STEP_COMPLETED = "execution.step.completed"
    ANALYTICS_SCORE_UPDATED = "analytics.score.updated"
    MASTERPLAN_GOAL_STATE_CHANGED = "masterplan.goal_state.changed"

    # Infinity loop-closure ledger (INFINITY-RUNTIME-1). Deliberately NOT
    # prefixed "execution." so they can be emitted outside a pipeline/async
    # context (see system_event_service execution-contract gate).
    RECALL_USED = "recall.used"
    SCORE_COMPUTED = "score.computed"
    NEXT_ACTION_CHOSEN = "next_action.chosen"
    # Deliverable C / FR-3 dispatch-outcome contract: what the runtime *did* with a
    # chosen trigger_execution (dispatched / declined-with-reason, then the resolved
    # follow-up run). Parents to NEXT_ACTION_CHOSEN via parent_event_id; the app reads
    # the CHOSEN → DISPATCHED chain from the ledger. Un-prefixed for the same reason.
    NEXT_ACTION_DISPATCHED = "next_action.dispatched"
    # RTR-6: first-class reasoning signal — a memory-derived input to the
    # learning loop (kind="recall": memory pulled into a context; kind="capture":
    # a significance-scored insight derived). Standardizes what previously lived
    # implicitly in MEMORY_WRITE payloads + MemoryNode columns. Un-prefixed for
    # the same reason as the ledger events above.
    REASONING_SIGNAL = "reasoning.signal"
    # RTR-5: runtime-driven autonomous execute-window lifecycle (started/completed).
    # Un-prefixed for the same reason as the ledger events above.
    AUTONOMY_WINDOW = "autonomy.window"
    # FR-15: an execution unit entered the scheduler queue, carrying the depth it landed
    # behind. Makes the window before `execution.started` visible in `system_events`
    # rather than a silent gap — the app team measured 177s of that silence, inside which
    # a queued request and a hung process are externally identical.
    #
    # ★ Named "scheduler.", NOT "execution.", and that is load-bearing rather than a
    # preference: the execution-contract gate in system_event_service raises for any
    # `execution.*` event emitted outside a pipeline, and the two hottest enqueue callers
    # — the event-bus subscriber thread (cross_instance.py) and wait expiry (waits.py) —
    # have no pipeline active. Same reason the ledger events above are un-prefixed.
    SCHEDULER_QUEUED = "scheduler.queued"

    FLOW_NODE_STARTED = "flow.node.started"
    FLOW_WAITING = "flow.waiting"
    WAIT_TIMEOUT = "WAIT_TIMEOUT"
    FLOW_NODE_COMPLETED = "flow.node.completed"
    FLOW_NODE_FAILED = "flow.node.failed"

    ASYNC_JOB_STARTED = "async_job.started"
    ASYNC_JOB_COMPLETED = "async_job.completed"
    ASYNC_JOB_FAILED = "async_job.failed"

    AGENT_STEP = "agent.step"
    AGENT_STEP_COMPLETED = "agent.step.completed"
    AGENT_STEP_FAILED = "agent.step.failed"

    MEMORY_WRITE = "memory.write"
    MEMORY_WRITE_FAILED = "error.memory_write"
    EMBEDDING_STARTED = "embedding.started"
    EMBEDDING_COMPLETED = "embedding.completed"
    EMBEDDING_FAILED = "embedding.failed"
    AUTONOMY_DECISION = "autonomy.decision"

    FEEDBACK_RETRY_DETECTED = "feedback.retry_detected"
    FEEDBACK_LATENCY_SPIKE = "feedback.latency_spike"
    FEEDBACK_ABANDONMENT_DETECTED = "feedback.abandonment_detected"
    FEEDBACK_REPEATED_FAILURE = "feedback.repeated_failure"

    NODUS_EXECUTE_STARTED = "nodus.execute.started"
    NODUS_EXECUTE_COMPLETED = "nodus.execute.completed"
    NODUS_EXECUTE_FAILED = "nodus.execute.failed"

    NODUS_EVENT_EMITTED = "nodus.event.emitted"
    NODUS_EVENT_WAIT_REQUESTED = "nodus.event.wait_requested"
    NODUS_EVENT_WAIT_RESUMED = "nodus.event.wait_resumed"

    NODUS_TRACE_STEP = "nodus.trace.step"
    NODUS_TRACE_COMPLETE = "nodus.trace.complete"

    SYSCALL_EXECUTED = "syscall.executed"


