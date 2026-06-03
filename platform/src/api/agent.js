import { authRequest, unwrapEnvelope } from "./_core.js";
import { ROUTES } from "./_routes.js";

export function getAgents() {
  return authRequest(ROUTES.MEMORY.AGENTS, { method: "GET" }).then(unwrapEnvelope);
}

export function recallFromAgent(agentId, query) {
  return authRequest(ROUTES.MEMORY.AGENT_RECALL(agentId), {
    method: "POST",
    body: JSON.stringify({ query }),
  }).then(unwrapEnvelope);
}

export function getFederatedMemory(query) {
  return authRequest(ROUTES.MEMORY.FEDERATED_RECALL, {
    method: "POST",
    body: JSON.stringify({ query }),
  }).then(unwrapEnvelope);
}

export function createAgentRun(payload) {
  return authRequest(ROUTES.AGENT.CREATE_RUN, {
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
  return authRequest(ROUTES.AGENT.APPROVE(runId), { method: "POST" }).then(unwrapEnvelope);
}

export function rejectAgentRun(runId) {
  return authRequest(ROUTES.AGENT.REJECT(runId), { method: "POST" }).then(unwrapEnvelope);
}

export function getAgentRunSteps(runId) {
  return authRequest(ROUTES.AGENT.STEPS(runId), { method: "GET" }).then(unwrapEnvelope);
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
  return authRequest(ROUTES.AGENT.EVENTS(runId), { method: "GET" }).then(unwrapEnvelope);
}