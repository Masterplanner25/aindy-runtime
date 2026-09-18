---
title: "Authority Lifetime — Design"
api_version: "1.0"
last_verified: "2026-09-17"
status: current
owner: "platform-team"
---

# `AUTHORITY-LIFETIME-1` — a token is valid while its run is live, not while the clock says so — design

**IMPLEMENTED 2026-09-17 (#720) — DEC-056..059 accepted as written. `cancellation.run_terminal_status`
+ `TERMINAL_RUN_STATUSES`; `capability_service._run_authority_ended` before the HMAC check; the
dispatcher's cancel branch widened in place; counter `aindy_authority_lifetime_refusals_total`.
Tests: `tests/unit/test_authority_lifetime.py`.** Originally a proposal under `AGENT_WORKING_RULES.md`
§8 (an authority check gains a stateful component on the hot path). The entry asks three questions to settle *before*
code: where the check lives, whether a negative cache is used, fail-open or fail-closed. §2
answers all three by pointing at a mechanism that already exists and already made the same
three choices; §4 is what not to build.

---

## 1. The finding, verified

`capability_service.py:25` — `TOKEN_TTL_HOURS = 24`; `verify_token` is a stateless HMAC +
expiry check, and `revoke|revocation|invalidate` is absent from the module. A token minted for a
run that finished in 90 seconds is presented and accepted for the rest of the day. Every
check goes through `check_tool_capability` (`:724`), called from `execute_tool` on every tool
invocation and, on the agent path, from the dispatcher's capability gate.

**Two corrections that narrow it:**

1. **It is not a bearer-escape.** The token is bound to a `run_id` and a capability ceiling
   (`AUTHORITY-VALUE-1`); what it authorises after the run ends is *more calls under that run's
   ceiling, charged to that run*. The exposure is a stale worker, a replayed request, or a
   leaked token continuing a finished run's work — not doing new work.
2. **The runtime already has a stateful, run-keyed, hot-path check of exactly this shape.**
   `CANCEL-REACH-1` reads the run's status at the tool seam and at the dispatcher, on its own
   session, cached per run for 2 s, failing OPEN (`cancellation.is_run_cancelled`,
   `cancellation.py:93`). The three questions the entry asks were answered there, with
   evidence, for the `cancelled` value of the same column.

---

## 2. The mechanism: the cancel check, widened to every terminal status

`is_run_cancelled(run_id)` reads `AgentRun.status` and answers `== "cancelled"`. The lifetime
check is the same read answering `status in TERMINAL_RUN_STATUSES`
(`completed | failed | verify_failed | cancelled | refused` — the same set
`finalize_for_run_status` maps, `EU-DOUBLE-FINALIZE-1`). Concretely:

- `cancellation.py` gains `run_terminal_status(run_id) -> str | None` — the existing cached,
  own-session, fail-open read, returning the status instead of a bool; `is_run_cancelled`
  becomes `run_terminal_status(run_id) == "cancelled"` (unchanged behaviour, pinned).
- **Where it lives — the same two sites as cancel, and no third:** `check_tool_capability`
  (before the HMAC check — a terminal run refuses without verifying) and the dispatcher's
  agent-span gate. The token itself stays stateless: `verify_token` is untouched, so the
  planner path and any caller without a run keep the pure HMAC check.
- **Refusal shape:** `failure_class="permission"` (a terminal run is not a transient condition;
  `RETRY-CLASSIFY-1` gives it one attempt), error `run <id> is <status>; authority ended with
  the run`. Metric `aindy_authority_lifetime_refusals_total{status, surface}`.
- **Negative cache — yes, and it is the positive cache's own entry:** the 2-second per-run
  cache already holds the last read; a terminal status is *sticky* (a run never leaves a
  terminal state, `_STATUS_TRANSITIONS`), so a terminal entry is kept for the process lifetime
  instead of 2 s. Cost after the first terminal read: zero queries.
- **Fail-open**, as cancel does and for the same reason: an unreadable status must not fail a
  live run; the token's expiry still bounds the window. **★ This is the one place the entry's
  reference (OpenHands, "nulled on pause") is *not* followed: a `waiting` run keeps its
  authority.** A parked run resumes with the same token (`FR-31`), and revoking on wait would
  make every resume a re-mint — `AUTHORITY-NEGOTIATION-1`'s wait gate would then need a grant
  path it was deliberately denied (`DEC-016`).

**What this composes with, unchanged:** `capability_ceiling` (what the bearer may do) — this
adds *while what is true* beside it, the entry's own framing; `CANCEL-REACH-1`'s pre-spawn
refusal and parent-side kill (a cancelled run is just one terminal value).

---

## 3. The hot-path cost, measured against what exists

`is_run_cancelled` already runs once per tool call and once per dispatch on every agent run
at HEAD. This design adds **no read** — it widens the predicate on the read that is already
there, and makes terminal answers sticky, which *removes* re-reads after the run ends. The
"stateless HMAC check becomes stateful" cost the entry warned of was paid by `CANCEL-REACH-1`
on 2026-09-15; this is its second consumer.

---

## 4. What not to build

- **Not a revocation list** — a terminal status *is* the revocation; a second store to keep in
  sync is `AUDIT-CORRELATION-1`'s "joined by convention" shape.
- **Not a shorter TTL** — the entry's point is that the clock is the wrong axis; 1 hour has
  the same defect as 24.
- **Not revocation on `waiting`** — §2; and not on `pending_approval`, where no token exists.
- **Not a token version / rotate-on-resume** — the run id is the version; a resumed run is the
  same run.
- **Not `KEY-SCOPE-ESCALATION-1`** (API keys have no run) and **not `AUTHORITY-VALUE-1`** (the
  ceiling is untouched).

## 5. Decisions this design asks for (`DEC-NNN` in the implementing PR)

1. Authority ends with the run: a token presented for a run in a terminal status is refused at
   `check_tool_capability` and the dispatcher's agent gate — the two sites `CANCEL-REACH-1`
   already reads — and nowhere else.
2. The read is `cancellation.py`'s existing cached own-session read, widened to return the
   status; terminal answers are sticky for the process lifetime (negative cache = the cache).
3. Fail-open, as cancel does; the token's expiry remains the outer bound.
4. A `waiting` run keeps its authority (OpenHands' pause-null is declined).

## 6. Tests

- Through the real `execute_tool` with a valid token: run `completed` → refused,
  `failure_class="permission"`, counter moves; run `executing` → runs (control).
- Sticky cache: after one terminal read, mutate the row back to `executing` (impossible in
  production, the point of the test) — still refused, zero further queries (spy the session
  factory).
- `waiting` run → runs; resumed run with the same token → runs.
- Fail-open: session factory raises → runs, `aindy_run_cancel_check_errors_total`-style counter
  moves (reuse cancel's).
- `is_run_cancelled` behaviour pinned identical before/after (the existing
  `test_cancel_reach.py` suite unchanged and green).
- Mutation: narrow the predicate back to `== "cancelled"` → the `completed` test goes red.
