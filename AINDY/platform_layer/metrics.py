"""
Prometheus metrics registry for A.I.N.D.Y.

All metrics are defined here and imported where needed.
Never use the default `prometheus_client` registry directly —
always import from this module.
"""
from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry

REGISTRY = CollectorRegistry(auto_describe=True)

# ── Execution pipeline ────────────────────────────────────────────────────────

execution_total = Counter(
    "aindy_execution_total",
    "Total executions by route and outcome",
    ["route", "status"],  # status: success | failed | waiting
    registry=REGISTRY,
)

execution_duration_seconds = Histogram(
    "aindy_execution_duration_seconds",
    "Execution handler duration in seconds",
    ["route"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
    registry=REGISTRY,
)

# ── Scheduler ────────────────────────────────────────────────────────────────

scheduler_queue_depth = Gauge(
    "aindy_scheduler_queue_depth",
    "Items in the scheduler priority queues",
    ["priority"],  # high | normal | low
    registry=REGISTRY,
)

scheduler_waiting_count = Gauge(
    "aindy_scheduler_waiting_count",
    "Flows currently registered in WAIT state (in-memory)",
    registry=REGISTRY,
)

# SYSMAX-5 — a job that could not run because the scheduler was saturated.
#
# APScheduler reports this as a per-job LOG LINE ("maximum number of running instances
# reached") and nothing else. That is what the FR-15 incident printed once per starved second
# while nobody could see it as a signal. Labelled by job id and reason so a starved *recovery*
# job — the class whose value peaks exactly when the scheduler is saturated — is
# distinguishable from a merely-skipped cleanup.
scheduler_job_starved_total = Counter(
    "aindy_scheduler_job_starved_total",
    "Scheduler job runs skipped because the scheduler was saturated",
    ["job_id", "reason"],  # reason: max_instances | missed
    registry=REGISTRY,
)

# FR-15 — time spent in the scheduler queue before dispatch. The depth gauge above says
# how many are waiting; this says how long, which is the number that was missing when a
# request took 177s to enter the pipeline. Buckets run to 5 minutes deliberately: the
# observed pathological waits were 22s / 48s / 184s, so a default histogram topping out
# around 10s would have put every interesting sample in +Inf and shown nothing.
scheduler_queue_wait_seconds = Histogram(
    "aindy_scheduler_queue_wait_seconds",
    "Seconds an execution unit waited in the scheduler queue before dispatch",
    ["priority"],  # high | normal | low
    buckets=(0.05, 0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
    registry=REGISTRY,
)

# ── Nodus warm-worker pool (NODUS-WARMPOOL-1) ────────────────────────────────

nodus_warm_pool_events_total = Counter(
    "aindy_nodus_warm_pool_events_total",
    "Warm-worker pool lifecycle events",
    ["event"],  # spawned | recycled | crashed | spilled | served
    registry=REGISTRY,
)

nodus_warm_pool_workers = Gauge(
    "aindy_nodus_warm_pool_workers",
    "Warm-worker pool worker count by state",
    ["state"],  # total | idle | busy
    registry=REGISTRY,
)

# ── Resource manager ─────────────────────────────────────────────────────────

active_executions_total = Gauge(
    "aindy_active_executions_total",
    "Total active executions across all tenants (in-memory counter)",
    registry=REGISTRY,
)

db_pool_checkedout = Gauge(
    "aindy_db_pool_checkedout",
    "Number of connections currently checked out from the pool",
    registry=REGISTRY,
)

db_pool_overflow = Gauge(
    "aindy_db_pool_overflow",
    "Number of overflow connections currently in use",
    registry=REGISTRY,
)

db_pool_size = Gauge(
    "aindy_db_pool_size",
    "Configured pool size",
    registry=REGISTRY,
)

db_pool_pressure = Gauge(
    "aindy_db_pool_pressure_ratio",
    "Connection pool pressure: checkedout / (pool_size + max_overflow). "
    "1.0 = fully saturated. Alert threshold recommended at 0.8.",
    registry=REGISTRY,
)

db_pool_exhaustion_events_total = Counter(
    "aindy_db_pool_exhaustion_events_total",
    "Number of times the pool pressure threshold was crossed (rising edge only)",
    registry=REGISTRY,
)

# ── OpenAI client ─────────────────────────────────────────────────────────────

openai_retries_total = Counter(
    "aindy_openai_retries_total",
    "Total OpenAI call retries",
    ["call_type"],  # chat | embedding
    registry=REGISTRY,
)

openai_errors_total = Counter(
    "aindy_openai_errors_total",
    "Total OpenAI call failures after all retries exhausted",
    ["call_type"],
    registry=REGISTRY,
)

deepseek_retries_total = Counter(
    "aindy_deepseek_retries_total",
    "Total DeepSeek call retries",
    ["call_type"],
    registry=REGISTRY,
)

deepseek_errors_total = Counter(
    "aindy_deepseek_errors_total",
    "Total DeepSeek call failures after all retries exhausted",
    ["call_type"],
    registry=REGISTRY,
)

embedding_generation_total = Counter(
    "aindy_embedding_generation_total",
    "Total embedding generation requests by outcome",
    ["outcome"],  # success | failure
    registry=REGISTRY,
)

embedding_generation_retries_total = Counter(
    "aindy_embedding_generation_retries_total",
    "Total embedding generation retries before a terminal outcome",
    registry=REGISTRY,
)

embedding_generation_latency_seconds = Histogram(
    "aindy_embedding_generation_latency_seconds",
    "Embedding generation latency in seconds",
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
    registry=REGISTRY,
)

mongo_health_status = Gauge(
    "aindy_mongo_health_status",
    "MongoDB connectivity status reported by startup health checks",
    registry=REGISTRY,
)

# Queue

async_queue_depth = Gauge(
    "aindy_async_queue_depth",
    "Pending async jobs currently queued",
    ["backend"],
    registry=REGISTRY,
)

async_queue_in_flight = Gauge(
    "aindy_async_queue_in_flight",
    "Async jobs currently being processed",
    ["backend"],
    registry=REGISTRY,
)

async_queue_delayed = Gauge(
    "aindy_async_queue_delayed",
    "Async jobs currently delayed before enqueue",
    ["backend"],
    registry=REGISTRY,
)

async_queue_dlq_depth = Gauge(
    "aindy_async_queue_dlq_depth",
    "Async jobs currently in the dead-letter queue",
    ["backend"],
    registry=REGISTRY,
)

async_queue_capacity = Gauge(
    "aindy_async_queue_capacity",
    "Configured async queue capacity",
    ["backend"],
    registry=REGISTRY,
)

async_queue_enqueue_total = Counter(
    "aindy_async_queue_enqueue_total",
    "Total async job enqueue attempts by outcome",
    ["backend", "outcome"],
    registry=REGISTRY,
)

async_queue_dequeue_total = Counter(
    "aindy_async_queue_dequeue_total",
    "Total async job dequeues",
    ["backend"],
    registry=REGISTRY,
)

async_queue_failure_total = Counter(
    "aindy_async_queue_failure_total",
    "Total async job queue failures by stage",
    ["backend", "stage"],
    registry=REGISTRY,
)

queue_backend_mode = Gauge(
    "aindy_queue_backend_mode",
    "Active queue backend: 1=redis, 0=in_memory (degraded)",
    registry=REGISTRY,
)

queue_backend_fallback_total = Counter(
    "aindy_queue_backend_fallback_total",
    "Number of times the queue fell back from Redis to in-memory",
    registry=REGISTRY,
)

quota_redis_mode = Gauge(
    "aindy_quota_redis_mode",
    "Active quota backend: 1=redis (cross-instance), 0=in_memory (per-instance only)",
    registry=REGISTRY,
)

quota_redis_fallback_total = Counter(
    "aindy_quota_redis_fallback_total",
    "Number of times the quota backend fell back from Redis to in-memory",
    registry=REGISTRY,
)

request_metric_drops_total = Counter(
    "aindy_request_metric_drops_total",
    "Number of RequestMetric rows dropped due to queue saturation",
    registry=REGISTRY,
)

memory_ingest_dropped_total = Counter(
    "aindy_memory_ingest_dropped_total",
    "Number of memory ingest writes dropped due to bounded queue backpressure",
    registry=REGISTRY,
)

memory_ingest_queue_depth = Gauge(
    "aindy_memory_ingest_queue_depth",
    "Current depth of the bounded memory ingest queue",
    registry=REGISTRY,
)

memory_ingest_queue_capacity = Gauge(
    "aindy_memory_ingest_queue_capacity",
    "Configured capacity of the bounded memory ingest queue",
    registry=REGISTRY,
)

startup_recovery_failure_total = Counter(
    "aindy_startup_recovery_failure_total",
    "Number of startup recovery scan failures",
    ["recovery_type"],
    registry=REGISTRY,
)

startup_recovery_runs_recovered_total = Counter(
    "aindy_startup_recovery_runs_recovered_total",
    "Number of stuck runs recovered at startup",
    ["recovery_type"],
    registry=REGISTRY,
)

flow_runs_dead_lettered_total = Counter(
    "aindy_flow_runs_dead_lettered_total",
    "Total FlowRun rows moved to dead_letter status",
    ["reason"],
    registry=REGISTRY,
)

rippletrace_engine_runs_total = Counter(
    "aindy_rippletrace_engine_runs_total",
    "Total RippleTrace engine runs",
    ["engine", "status"],
    registry=REGISTRY,
)

rippletrace_engine_duration_seconds = Histogram(
    "aindy_rippletrace_engine_duration_seconds",
    "RippleTrace engine run duration",
    ["engine"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 30.0],
    registry=REGISTRY,
)

rippletrace_drop_points_processed_total = Counter(
    "aindy_rippletrace_drop_points_processed_total",
    "Total drop points processed by threadweaver",
    ["status"],
    registry=REGISTRY,
)

wait_recovery_poll_failure_total = Counter(
    "aindy_wait_recovery_poll_failure_total",
    "Number of wait recovery poll failures (background job)",
    registry=REGISTRY,
)

system_health_tier = Gauge(
    "aindy_system_health_tier",
    "Current system health tier: 0=healthy, 1=degraded, 2=critical",
    registry=REGISTRY,
)

ai_circuit_breaker_state = Gauge(
    "aindy_ai_circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=half_open, 2=open)",
    ["provider"],
    registry=REGISTRY,
)

deferred_boundary_violations_total = Gauge(
    "aindy_deferred_boundary_violations_total",
    "Number of deferred cross-domain imports detected in router files "
    "(function-body imports crossing app domain boundaries)",
    registry=REGISTRY,
)

resume_watchdog_resumes_total = Counter(
    "aindy_resume_watchdog_resumes_total",
    "Number of flows resumed by the watchdog due to missed Redis events",
    registry=REGISTRY,
)

event_handler_timeouts_total = Counter(
    "aindy_event_handler_timeouts_total",
    "Number of event handler invocations that exceeded the timeout",
    ["event_type"],
    registry=REGISTRY,
)

event_handler_duration_seconds = Histogram(
    "aindy_event_handler_duration_seconds",
    "Wall-clock time per event handler invocation",
    ["event_type", "handler_name", "result"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
    registry=REGISTRY,
)

infinity_score_write_failures_total = Counter(
    "aindy_infinity_score_write_failures_total",
    "Total number of Infinity score write failures",
    ["reason"],
    registry=REGISTRY,
)


# ── Effect ledger / idempotency gate (IDEM-11) ───────────────────────────────
#
# ★ Until 2026-08-19 NOTHING observed this gate. `aindy_durable_effects` and
# `aindy_effect_attribution` are ContextVars, not metrics — so with
# AINDY_SYSCALL_IDEMPOTENCY enabled an operator had no way to tell whether the gate was
# firing, replaying, or silently degrading. That absence is why the flag could not be
# soaked in production: there was nothing to read.
#
# ★ `degraded` is the label that matters and the reason this is one counter with an
# outcome label rather than a single "gate fired" counter. EXACTLY_ONCE is NOT
# exactly-once under contention — when the gate loses the insert race to a live pending
# row it degrades to AT_LEAST_ONCE for that call (strict at-most-once needs advisory
# locking; see IDEMPOTENCY_CONTRACT.md). That downgrade is correct and documented, and it
# must be COUNTABLE: a deployment where `degraded` is a meaningful fraction of `reserved`
# is one where the guarantee an operator thinks they enabled is not the one they have.
effect_gate_outcomes_total = Counter(
    "aindy_effect_gate_outcomes_total",
    "Idempotency gate outcomes by resolution: reserved (this caller runs the effect), "
    "replayed (a completed record was returned instead of executing), degraded (lost the "
    "race to a live pending row, downgraded to AT_LEAST_ONCE for this call), "
    "degraded_gate_error (the gate machinery itself failed and the caller was downgraded), "
    "reclaimed (took over a stale or failed slot). ★ BOTH degraded* labels mean at-most-once "
    "did not hold for that call; they are separate because one is contention and the other "
    "means the gate is broken.",
    ["outcome"],
    registry=REGISTRY,
)


# ── Tool return contract (TOOL-SEAM-ISOLATION-1 step C1) ─────────────────────
#
# ★ This counter is the GATE ON C2, not a style check. A tool's return has to marshal for the
# tool to run behind a process boundary at all — you cannot hand a `UUID` or an open session
# across a pipe. Every tool that exists already returns a dict by convention (18/18, all typed
# `-> dict`), but NOTHING enforced it, so "they all comply" was an assumption rather than a
# measurement. This makes it a number.
#
# ★ Violations are counted and warned, never rejected. By the time the return is inspected the
# handler has already run and its effect is real — failing the call there would discard a real
# effect, which is strictly worse than passing an awkward value through. The syscall path made
# the same call for the same reason (`SyscallDispatcher`: "a ledger failure must never turn
# that into a caller-visible error"), and the two boundaries should not disagree.
tool_return_contract_violations_total = Counter(
    "aindy_tool_return_contract_violations_total",
    "Tool returns that would not survive a process boundary, by reason: not_a_dict (the seam "
    "and the effect ledger both assume a dict) or not_json_serializable (marshals nowhere). "
    "A non-zero count is the list of tools that cannot be moved behind C2's boundary yet.",
    ["reason", "declared_isolation"],
    registry=REGISTRY,
)
