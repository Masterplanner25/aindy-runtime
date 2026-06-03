import { authRequest, unwrapEnvelope } from "./_core.js";
import { ROUTES } from "./_routes.js";

export function getAgents() {
  return authRequest(ROUTES.AGENT.LIST, { method: "GET" }).then(unwrapEnvelope);
}

export function recallFromAgent(agentId, query) {
  return authRequest(ROUTES.AGENT.RECALL(agentId), {
    method: "POST",
    body: JSON.stringify({ query }),
  }).then(unwrapEnvelope);
}

export function getFederatedMemory(query) {
  return authRequest(ROUTES.AGENT.FEDERATED_MEMORY, {
    method: "POST",
    body: JSON.stringify({ query }),
  }).then(unwrapEnvelope);
}

export function createAgentRun(payload) {
  return authRequest(ROUTES.AGENT.RUNS, {
    method: "POST",
    body: JSON.stringify(payload),
  }).then(unwrapEnvelope);
}

export function getAgentRuns() {
  return authRequest(ROUTES.AGENT.RUNS, { method: "GET" }).then(unwrapEnvelope);
}

export function getAgentRun(runId) {
  return authRequest(ROUTES.AGENT.RUN(runId), { method: "GET" }).then(unwrapEnvelope);
}

export function approveAgentRun(runId) {
  return authRequest(ROUTES.AGENT.RUN_APPROVE(runId), { method: "POST" }).then(unwrapEnvelope);
}

export function rejectAgentRun(runId) {
  return authRequest(ROUTES.AGENT.RUN_REJECT(runId), { method: "POST" }).then(unwrapEnvelope);
}

export function getAgentRunSteps(runId) {
  return authRequest(ROUTES.AGENT.RUN_STEPS(runId), { method: "GET" }).then(unwrapEnvelope);
}

export function getAgentTools() {
  return authRequest(ROUTES.AGENT.TOOLS, { method: "GET" }).then(unwrapEnvelope);
}

export function getAgentTrust() {
  return authRequest(ROUTES.AGENT.TRUST, { method: "GET" }).then(unwrapEnvelope);
}

export function updateAgentTrust(payload) {
  return authRequest(ROUTES.AGENT.TRUST, {
    method: "PUT",
    body: JSON.stringify(payload),
  }).then(unwrapEnvelope);
}

export function getAgentSuggestions() {
  return authRequest(ROUTES.AGENT.SUGGESTIONS, { method: "GET" }).then(unwrapEnvelope);
}

export function fetchRunEvents(runId) {
  return authRequest(ROUTES.AGENT.RUN_EVENTS(runId), { method: "GET" }).then(unwrapEnvelope);
}