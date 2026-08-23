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

// ── Runtime-only operator routes (FR-21) ─────────────────────────────────────
// The app team rebuilt an operator SPA beside this one and offered it back. Of the five
// panels they called "clearly runtime", four already existed here; the two that did not —
// webhooks and the dead-letter queue — drive routes this runtime owns and its own operator
// surface did not expose. An operator should not open an app repo's UI to drain a runtime
// DLQ.
//
// These paths live here rather than in `@aindy/ui-kit`'s ROUTES because ui-kit is a
// separate package with its own release train, and a panel should not wait on one. They
// are a staging area, not a second source of truth: fold them into ROUTES on the next
// ui-kit release and delete this block. `docs/runtime/UI_CONTRACT.md` lists them as
// canonical either way, and `tests/unit/test_platform_operator_panels.py` fails if a path
// here does not exist on the running app — a typo in a URL string is otherwise a
// runtime-only 404 nobody sees until an operator clicks it.
export const RUNTIME_ROUTES = Object.freeze({
  WEBHOOKS: Object.freeze({
    LIST: "/platform/webhooks",
    CREATE: "/platform/webhooks",
    DELETE: (subscriptionId) => `/platform/webhooks/${encodeURIComponent(subscriptionId)}`,
  }),
  QUEUE: Object.freeze({
    HEALTH: "/platform/queue/health",
    DEAD_LETTERS: "/platform/queue/dead-letters",
    DEAD_LETTERS_DRAIN: "/platform/queue/dead-letters/drain",
    DEAD_LETTER_REPLAY: (jobId) => `/platform/queue/dead-letters/${encodeURIComponent(jobId)}/replay`,
    DEAD_LETTER_DELETE: (jobId) => `/platform/queue/dead-letters/${encodeURIComponent(jobId)}`,
  }),
});
