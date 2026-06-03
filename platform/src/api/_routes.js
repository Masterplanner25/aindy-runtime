export { ROUTES } from "@aindy/ui-kit";

// Runtime-specific feature flags — controls which deferred/unserved routes render NavLinks.
// Constants in _routes.js (ui-kit) remain syntactically live; only the NavLink is hidden.
// Flip a flag to true when the backing route lands in the runtime OpenAPI.
export const FEATURE_FLAGS = Object.freeze({
  OPERATOR_FLOW_STRATEGIES:  false, // OPER-DEFER-001: /platform/flows/strategies not served
  OPERATOR_AUTOMATION_LOGS:  false, // OPER-DEFER-002: /automation/logs lives in monolith
  OPERATOR_SCHEDULER_STATUS: false, // SCHED-001/002/003: returns 500 in platform-only profile
  RIPPLETRACE_VIEWER:        false, // RIPPLE-ROUTES-001: load-trace path is bare monolith path
});
