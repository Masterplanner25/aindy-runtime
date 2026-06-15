export { ROUTES } from "@aindy/ui-kit";

// Runtime-specific feature flags — controls which deferred/unserved routes render NavLinks.
// Constants in _routes.js (ui-kit) remain syntactically live; only the NavLink is hidden.
// Flip a flag to true when the backing route lands in the runtime OpenAPI.
export const FEATURE_FLAGS = Object.freeze({
  OPERATOR_FLOW_STRATEGIES:  true,  // OPER-DEFER-001: closed 2026-06-15 — /platform/flows/strategies served
  OPERATOR_AUTOMATION_LOGS:  true,  // OPER-DEFER-002: closed 2026-06-15 — /automation/logs served
  OPERATOR_SCHEDULER_STATUS: true,  // SCHED-001/002/003: fixed 2026-06-04 — direct impl, no flow dependency
  RIPPLETRACE_VIEWER:        false, // RIPPLE-ROUTES-001: load-trace path is bare monolith path
});
