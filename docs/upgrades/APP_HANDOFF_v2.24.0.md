---
title: "App Handoff — Runtime v2.24.0"
api_version: "1.0"
last_verified: "2026-09-26"
status: current
owner: "platform-team"
---

# App handoff: runtime v2.24.0 (+ `@aindy/ui-kit` 2.1.1)

Pin bump `constraints.txt` `==2.23.0` → `==2.24.0` (your contract test moves the floor with it),
and `@aindy/ui-kit` `2.1.0` → **`2.1.1`**.

**This release is your register, answered.** Every open runtime FR you filed against 2.22.0 and
2.23.0 is in it: FR-43 (§1), FR-44 (§2), FR-45 (§3), FR-46 (§4, default off), and FR-47 (§6, the
ui-kit half). So is one question of ours that the owner decided (IDEM-14, §5). **Required of you:
the pin, the ui-kit bump, and a rebuild.** There is no new migration. §1 says why a stack left
narrow by 2.22.0 now stops at exit 3, and why yours will not. Three items change behaviour you
can observe (§2, §5, §6).

> ## ★ Confirm what you are actually running: in the container, printing the path.
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
> # expect: 2.24.0 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 1. Schema: no migration, and FR-43 makes the last one honest (#761)

No change under `AINDY/db/models/`. Alembic head stays **`0020`** and the schema contract stays
**`2026-09-20`**.

**What changed is what `bootstrap-schema` can see.** It compared column type NAMES only, so
`varchar(32)` and `varchar(128)` were the same to it. That is how your first boot of 2.22.0 printed
`(no table changes)` and stamped `0020` over an unwidened `system_events.source`, as you filed. Now:

- String length and `numeric` precision/scale are compared. A **widening** is a new drift class,
  `additive_column_widen`. `bootstrap-schema` exits **3**, and `--reconcile` widens the column,
  which is metadata-only in PostgreSQL. A **narrowing** stays exit 4.
- `(no table changes)` is printed only when the report is ok. The stamp line names the revision
  it moved from: `stamped … from revision 0019 to 0020`.
- Our Upgrade Path Guard now checks every bounded string column's width in `information_schema`
  against the models, and its negative control injects exactly your case.

**Your stack:** you widened `source` to 128 out of band, so your entrypoint sees exit 0 and nothing
changes. Any other 2.22.0-built stack still at 32 now gets exit 3, and `--reconcile` fixes it.

★ **Correction to the 2.22.0 handoff.** Its §1 promised that a bare `bootstrap-schema` would exit
3 on an existing deployment. On a create_all-built stack that was not true; no code path could
produce it. It is true from this release. The published text stays as it was, and this is the
correction.

What the check still does **not** compare: indexes, constraints and data
(`docs/runtime/SCHEMA_LIFECYCLE.md` now says so).

---

## 2. FR-44: a resume served by another process now moves the run (#760)

`POST /apps/agent/runs/{id}/resume` published the run's wait event and, for an authority-gate
decision, answered `run_status: "resuming"` whatever the publish reached. A run's wait lives in
memory in the process that parked it, so on your FR-38 re-run the resume reached nobody. You got a
200 with `waiters_notified: 0`, and the run stayed `waiting` until a restart.

Now the route arms the run's wait on the serving process first, if that process holds none. It
does this by rehydrating the one run, and duplicate registrations are safe because the resume's
claim is atomic. The response is honest either way:

- `authority_gate.run_status` is **`"resuming"` only when a waiter was woken here**.
- Otherwise it is **`"waiting"`** with **`reason: "no_local_waiter_woken"`**. The decision stays
  recorded on the step row and replays on the next re-drive.

**If your resume surface reads `run_status`, branch on `"waiting"`**; it was never returned
before. An `abort` is unchanged.

---

## 3. FR-45: the runtime's own adapters stamp `X-AINDY-Envelope` (#759)

`raw_canonical_adapter`, `legacy_envelope_adapter`, `memory_execute_adapter` and
`memory_completion_adapter`'s success path now set the header whenever their body is an envelope,
meaning a dict with a top-level `data`. `raw_json_adapter` never does.

**For you:**
- The routes you register with the runtime's adapters are now stamped by the runtime. Your
  `apps/_shared/envelope.py` sets the same value on top; that is harmless, and you can drop it for
  those routes. **Keep it for any adapter of your own**, because an app's adapter still decides
  for itself (`docs/runtime/UI_CONTRACT.md`).
- **ui-kit 2.1.1** exports `_resetEnvelopeDetection` and `ENVELOPE_HEADER` from the package entry
  (your ask 2), so your tests can reset the latch between cases. The latch is documented next to
  `unwrapEnvelope` (ask 3).

---

## 4. FR-46: a plan step can use an earlier step's result, **default off** (#764)

Built to the design (`docs/design/FR46_STEP_REFERENCES_DESIGN.md`, DEC-073..075, accepted).

- **The form.** An argument value may be exactly `{"$from_step": N, "path": "a.b"}`. It is
  replaced by tool step N's result, or a field inside it. N counts tool steps only and must be
  earlier than the current step. List items are addressed by number, so your `abf834d4` case is
  `{"$from_step": 0, "path": "results.0.id"}`.
- **Plan time.** A reference to the same or a later step, a malformed path, or extra keys make
  `generate_plan` refuse the plan, and the reason reaches you the way any plan failure does.
- **Run time.** The value is resolved **before** `execute_tool` on both backends, so:
  - your `args_schema` (FR-33) validates the **value**, including under `enforce` (your ask 3);
  - the idempotency key covers the value;
  - `agent_steps.tool_args` records what the tool was actually called with.
- **When it cannot resolve.** A reference to a step that failed, was skipped at the authority
  gate, has no result, or lacks the path fails the step with `failure_class: "invalid"`. It is not
  retried, and **the tool is never called with the placeholder**.
- **The planner line is ours.** With the flag on, the runtime's tool catalog appends one line
  describing the form. **You do not need to add anything to `PLANNER_SYSTEM_PROMPT`.**
- **Size (your ask 4).** The runtime truncates no step result, so a reference carries exactly what
  the tool returned. The 2 KB mid-word cut is yours: `apps/search/syscalls.py:133`
  (`raw[:2000]`), and again at `apps/search/services/search_service.py:446`. A reference passes
  that cut along. Lifting it, or cutting at a word boundary with a marker, is your change.

Turn it on with **`AINDY_PLAN_STEP_REFERENCES=1`**. It ships off until the evidence run in §8.

---

## 5. IDEM-14: the tool idempotency key is per plan STEP (#763, DEC-076)

Found reading FR-46's seam and decided by the owner: *"idempotent per tool call / individual step
— that's the actual safe bet."* The key was `(tool, args, run)`. So a plan whose steps 1 and 4 called
the same `EXACTLY_ONCE` tool with identical args **ran step 1 and replayed it as step 4**: the
second effect never happened, and the run said `success`. Now the scope is `run#step:N` when an
agent backend names the step. A retry of one step still replays. Syscalls, MCP and extensions keep
the run scope.

**For you:**
- **`execute_tool` has a new optional keyword, `step_index`.** Any test stub of `execute_tool`
  with a fixed signature must accept it. Ours had five, and one made CI red.
- You have no `EXACTLY_ONCE` tool today (your 2.23.0 adoption), so the behaviour change does not
  reach your traffic. It will when you declare one.

---

## 6. FR-47: ui-kit 2.1.1 takes a per-call timeout

The ui-kit half of your ask (aindy-ui-kit #6 via #7; ships with #5 as **2.1.1**, published alongside this release):

- **`timeoutMs` per call**, e.g. `request(path, { method: "POST", timeoutMs: 90_000 })`. The
  default is `DEFAULT_TIMEOUT_MS` (30000, unchanged and exported). `0` arms no kit timer, so your
  `signal` governs. An invalid value falls back to 30 s, never to "no timeout". It is not forwarded
  to `fetch`.
- **A 408 now means the kit's own timer fired** (your ask 2), and its message names the timeout
  used. ★ **Behaviour change:** an abort from YOUR `signal` now rejects with the `AbortError`
  itself, not `ApiError(408)`. Code that caught 408 to detect its own aborts must check
  `err.name === "AbortError"`.
- Found on the way: the 503 `Retry-After` retry rebuilt the options, which would have dropped a
  per-call timeout. It keeps it now.

**For you:** `createAgentRun` passes `{ timeoutMs: 90_000 }`, and your #410 poll-on-408
workaround can go.

**Two runtime facts about the same problem that you did not ask about.** They are recorded
because either is the structural fix for a synchronous LLM call inside a request; both are your
choice, not changes we made:

1. **Run creation has no idempotency key.** A resubmit after any client-side timeout is always a
   second run. If you want the runtime to deduplicate a retried create, that is a new FR.
2. **The non-blocking shape already exists.** With `AINDY_ASYNC_HEAVY_EXECUTION=1`,
   `POST /apps/agent/runs` returns **202 + a job** immediately instead of planning inline. It is
   off in your deployment (the default). It applies to every heavy route, not only this one, so
   it is a deployment decision.

---

## 7. Dependency: nodus-lang 5.15.0 (#758)

`nodus-lang` 5.14.0 → **5.15.0**. The public surface the runtime uses is identical: we diffed it
between the two versions in throwaway venvs. Two of its fixes tighten guest confinement across a
park/resume:

- Nodus #873: a resume now inherits the caller's instruction and time bounds. A guest could escape
  both by parking.
- Nodus #868: a derived VM keeps its host state, and `agent_call` in a module no longer reaches the
  process-global registry.

Its three listed behaviour changes (checkpoint replay, the `nodus run` 200 ms default,
`max_terminal_runs`) do not reach a runtime call path. **If you pin `nodus-lang` yourself, move it
with ours.** `nodus-mcp` 0.1.5 (a version-string fix) resolves under the existing `>=0.1.4`.

---

## 8. Asks

1. **Run the FR-46 evidence with `AINDY_PLAN_STEP_REFERENCES=1`.** Re-run the owner's goal
   (*"Research SEO, AI Search and Marketing Strategies. Then use the research to create a
   strategy…"*). The pass condition is that the `memory.write` step stores step 0's findings, not
   a sentence written before step 0 ran. That run decides the default flip.
2. **Re-run the FR-44 two-process resume on 2.24.0.** Park in a `docker exec`, then resume from an
   api that booted before the park. Expected: `waiters_notified: 1` and `completed`, with no
   restart.

---

## 9. Acknowledged from your 2.23.0 adoption

- **`nltk` / `textstat`:** declared in your `pyproject.toml` since your #391 (2026-09-18). Our
  2.23.0 handoff §5 should not have said otherwise. The runtime drops its pins in the release
  after 2026-10-01, as announced. Nothing is owed from you.
- **The `nodus_vm` denial re-run:** done 2026-09-23 on 2.22.0 (run `bea83301…`), so the phase-3
  flip of `AUTHORITY-NEGOTIATION-1` has evidence from both backends. That run is what found FR-44.
- **`AINDY_TOOL_IDEMPOTENCY_STRICT`:** understood that you are not the traffic, because none of
  your tools is `EXACTLY_ONCE`. The ask is withdrawn.
- **FR-44 vs FR-15:** you were right that they are different defects (single-instance vs
  distributed). FR-44 is closed by §2; FR-15 stays open on our side.

---

## 10. What did not change

No syscall added or removed; no route contract change beyond §2's new `run_status` value; no
capability vocabulary change; the SPA is unchanged. `AINDY_SYSCALL_IDEMPOTENCY_STRICT` and
`AINDY_TOOL_IDEMPOTENCY_STRICT` are untouched (§5 changes the tool key's scope, not either flag).
