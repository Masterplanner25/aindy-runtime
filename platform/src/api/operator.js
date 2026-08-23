import { adminRequest as authRequest, unwrapEnvelope } from "./_core.js";
import { ROUTES, RUNTIME_ROUTES } from "./_routes.js";

export function getFlowRuns(status = null, workflowType = null, limit = 20) {
  const params = new URLSearchParams();
  if (status) params.append("status", status);
  if (workflowType) params.append("workflow_type", workflowType);
  params.append("limit", limit);
  return authRequest(`${ROUTES.OPERATOR.FLOW_RUNS}?${params.toString()}`, { method: "GET" }).then(unwrapEnvelope);
}

export function getFlowRun(runId) {
  return authRequest(ROUTES.OPERATOR.FLOW_RUN(runId), { method: "GET" }).then(unwrapEnvelope);
}

export function getFlowRunHistory(runId) {
  return authRequest(ROUTES.OPERATOR.FLOW_RUN_HISTORY(runId), { method: "GET" }).then(unwrapEnvelope);
}

export function resumeFlowRun(runId, eventType, payload = {}) {
  return authRequest(ROUTES.OPERATOR.FLOW_RUN_RESUME(runId), {
    method: "POST",
    body: JSON.stringify({ event_type: eventType, payload }),
  }).then(unwrapEnvelope);
}

export function getFlowRegistry() {
  return authRequest(ROUTES.OPERATOR.FLOW_REGISTRY, { method: "GET" }).then(unwrapEnvelope);
}

export function getFlowStrategies() {
  return authRequest(ROUTES.OPERATOR.FLOW_STRATEGIES, { method: "GET" }).then(unwrapEnvelope);
}

export function getAutomationLogs(status = null, source = null, limit = 50) {
  const params = new URLSearchParams();
  if (status) params.append("status", status);
  if (source) params.append("source", source);
  params.append("limit", limit);
  return authRequest(`${ROUTES.OPERATOR.AUTOMATION_LOGS}?${params.toString()}`, { method: "GET" }).then(unwrapEnvelope);
}

export function getAutomationLog(logId) {
  return authRequest(ROUTES.OPERATOR.AUTOMATION_LOG(logId), { method: "GET" }).then(unwrapEnvelope);
}

export function replayAutomationLog(logId) {
  return authRequest(ROUTES.OPERATOR.AUTOMATION_REPLAY(logId), { method: "POST" }).then(unwrapEnvelope);
}

export function getSchedulerStatus() {
  return authRequest(ROUTES.OPERATOR.SCHEDULER_STATUS, { method: "GET" }).then(unwrapEnvelope);
}

export function getObservabilityRequests(windowHours = 24, limit = 50, errorLimit = 25) {
  const params = new URLSearchParams({
    window_hours: String(windowHours),
    limit: String(limit),
    error_limit: String(errorLimit),
  });
  return authRequest(`${ROUTES.OPERATOR.OBSERVABILITY_REQUESTS}?${params.toString()}`, { method: "GET" }).then(unwrapEnvelope);
}

export function getObservabilityDashboard(windowHours = 24) {
  const params = new URLSearchParams({
    window_hours: String(windowHours),
  });
  return authRequest(`${ROUTES.OPERATOR.OBSERVABILITY_DASHBOARD}?${params.toString()}`, { method: "GET" }).then(unwrapEnvelope);
}

export function getSystemState() {
  return authRequest("/platform/observability/system", { method: "GET" }).then(unwrapEnvelope);
}

// ── Webhooks (FR-21) ─────────────────────────────────────────────────────────
// These routes return a BARE body: `webhooks_router` calls the pipeline with
// `return_result=True` and returns `result.data` itself, so no envelope is built. The
// DLQ helpers below are the opposite — same `/platform` tree, different shape. That is
// FR-19's contract question sitting inside one console, which is why every helper here
// goes through `unwrapEnvelope` (a no-op on a bare body) rather than each caller
// guessing.

export function getWebhooks() {
  return authRequest(RUNTIME_ROUTES.WEBHOOKS.LIST, { method: "GET" }).then(unwrapEnvelope);
}

export function createWebhook({ event_type, callback_url, secret }) {
  // `owner_class: "first-party-app"` is deliberate: an operator registering a callback on
  // their own deployment is first-party. The external-third-party class additionally
  // requires declared provenance, which is not something a console can supply honestly.
  const body = { event_type, callback_url, owner_class: "first-party-app" };
  if (secret) body.secret = secret;
  return authRequest(RUNTIME_ROUTES.WEBHOOKS.CREATE, {
    method: "POST",
    body: JSON.stringify(body),
  }).then(unwrapEnvelope);
}

export function deleteWebhook(subscriptionId) {
  // 204, no body — nothing to unwrap.
  return authRequest(RUNTIME_ROUTES.WEBHOOKS.DELETE(subscriptionId), { method: "DELETE" });
}

// ── Queue dead letters (FR-21) ───────────────────────────────────────────────
// The async job queue's DLQ, not the flow-run dead-letter list under /observability.
// Both exist and they are different records: this one holds queue jobs that exhausted
// their attempts and can be replayed onto the queue.

export function getQueueHealth() {
  return authRequest(RUNTIME_ROUTES.QUEUE.HEALTH, { method: "GET" }).then(unwrapEnvelope);
}

export function getDeadLetters(limit = 100) {
  const params = new URLSearchParams({ limit: String(limit) });
  return authRequest(`${RUNTIME_ROUTES.QUEUE.DEAD_LETTERS}?${params.toString()}`, {
    method: "GET",
  }).then(unwrapEnvelope);
}

export function replayDeadLetter(jobId) {
  return authRequest(RUNTIME_ROUTES.QUEUE.DEAD_LETTER_REPLAY(jobId), { method: "POST" }).then(
    unwrapEnvelope,
  );
}

export function deleteDeadLetter(jobId) {
  return authRequest(RUNTIME_ROUTES.QUEUE.DEAD_LETTER_DELETE(jobId), { method: "DELETE" }).then(
    unwrapEnvelope,
  );
}

export function drainDeadLetters() {
  return authRequest(RUNTIME_ROUTES.QUEUE.DEAD_LETTERS_DRAIN, { method: "POST" }).then(
    unwrapEnvelope,
  );
}
