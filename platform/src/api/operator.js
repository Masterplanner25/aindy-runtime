import { adminRequest as authRequest, unwrapEnvelope } from "./_core.js";
import { ROUTES } from "./_routes.js";

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
