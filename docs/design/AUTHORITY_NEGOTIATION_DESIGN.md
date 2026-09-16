---
title: "Authority Negotiation — Design"
api_version: "1.0"
last_verified: "2026-09-15"
status: current
owner: "platform-team"
---

# Authority negotiation — design

**`AUTHORITY-NEGOTIATION-1`. PHASES 0, 1 AND 2 SHIPPED (0: 2026-09-08 #600; 1: 2026-09-10; 2: 2026-09-15 — the WAIT gate, §5a); phase 3 is evidence, not code.** Read this before building it — §2
overturns the mechanism the entry itself proposes, and §7 is the list of things not to build.

---

## 1. The finding

A denied capability check **terminates the step**. `CAPABILITY_DENIED` is emitted at
`nodus_adapter.py:188` and `nodus_execution_service.py:335`, and the handler returns
`{"status": "FAILED", ...}`. There is no path that asks *"this step was refused at the authority
it requested; may it proceed at a lower one?"*

> **★★ CORRECTED at phase 1 — this section named two sites and there are FOUR emissions, of which
> exactly ONE can negotiate.** The census above was written by hand and was wrong in the way
> `CLAUDE.md`'s green-check variant 12 describes: a literal list inside a spec, never re-derived.
> Measured at HEAD:
>
> | Site | What is denied | Negotiable? |
> |---|---|---|
> | `nodus_adapter.py:188` | a **tool**, via `check_tool_capability` | **yes — this is the one** |
> | `agent_runtime/execution.py:51` | a **missing token** | no — nothing to check a fallback against |
> | `nodus_execution_service.py:335` | run-level `execute_flow` | no — no tool, so no `degraded_variant` |
> | `nodus_execution_service.py:1056` | run-level `execute_flow` | no — same |
>
> `degraded_variant` is declared on a **tool**, so negotiation is only meaningful where a tool was
> refused. Phase 1 wires that one site. **This is not a reduction in scope** — the other three
> were never reachable by this mechanism, and wiring them would have produced a call that could
> only ever return `no_variant`.

Because approval is **whole-plan**, the only recovery is a human approving an entirely new run —
which **discards the durable state the original accumulated**. A run that did nine steps of real
work and was refused on the tenth starts again from zero.

The shape to keep, taken from the comparison that produced the entry: **bounded** (exactly one
attempt), **directional** (downgrade only, never escalate), and **recorded**.

---

## 2. ★★ The entry says the missing primitive is `amend_token`. For this recovery path, it is not.

The entry proposes `amend_token` — "an authenticated, audited, monotonic-under-ceiling authority
amendment" — as the piece it describes a use for without specifying. Verified at HEAD:
`capability_service.py` has `mint_token` (`:442`) and `refresh_token` (`:560`) and no amendment
primitive, and `refresh_token` deliberately never widens.

**But the downgrade path does not need one, and building it here would add a second minting path
for no gain.** Work through what a denial actually is:

- The token grants `allowed_capabilities` (a set) and `granted_tools` (a list).
- A step requests capability `C`. The check fails because **`C` is not in that set**.
- A fallback that requires `D` where `D ⊆ token.allowed_capabilities` is **already authorised by
  the token the run is holding**. Nothing has to be minted, amended, or re-approved.

So the executable condition is not *"`D` is a subset of the denied capability `C`"* — that is the
tempting formulation and it is **wrong**, because `D ⊂ C` says nothing about whether the token
grants `D` either. The condition is:

> **`required_capabilities(fallback) ⊆ token.allowed_capabilities`.**

Checked at negotiation time against the token in hand. If it does not hold, negotiation fails and
the step terminates exactly as it does today — no widening, no new token, no approval.

★ **`amend_token` remains worth having for a different question** — `AUTHORITY-LIFETIME-1`, where
a token outlives the run it authorises and wants *narrowing* on reaching a terminal state. Do not
fold the two: one is about recovering a refused step, the other about revoking a finished one.

---

## 3. Who declares the fallback

**The tool declares it, at registration.** Not the plan, and not the model.

```python
register_tool(
    name="send_invoice_email",
    ...,
    degraded_variant="queue_invoice_for_review",   # ← the declaration
)
```

★ **The reason is the same one that makes `env_spec` safe in `EXEC-ENV-BIND-1` phase 3: the thing
being constrained must never choose its own constraint.** If the plan named the fallback, a model
that was refused could nominate whatever it liked as its "lower authority" option, and the
runtime would have no basis to disagree. A registration-time declaration is first-party,
reviewable, and fixed before any run exists.

**Verified at registration, refused if it does not hold:**

1. The named fallback is a registered tool.
2. `required_capabilities(fallback) ⊊ required_capabilities(original)` — a **strict** subset, so
   a "fallback" cannot be a lateral move to a different authority wearing the word *degraded*.
3. The fallback declares **no** `degraded_variant` of its own. See §4.

Failing at registration rather than at first denial is the `EXEC-ENV-BIND-1` phase 3 rule again:
an operator should see a malformed declaration at startup, not the first time a run happens to be
refused — and denials are, by construction, rare and stressful moments to discover a typo.

---

## 4. Bounded means exactly one attempt, and no chains

One negotiation per denial. The fallback is executed once; if **it** is denied, the step
terminates.

★ **A fallback may not declare a fallback.** Otherwise a chain of downgrades is an unbounded
search over the authority lattice, executed automatically, at the moment the runtime has least
reason to trust the plan. "At most once" written as a counter is weaker than a structural
guarantee: with no chains, the bound is a property of the registry rather than of a variable
someone can get wrong.

---

## 5. The fallback kinds, and the one that is deliberately excluded

**Included:**

- **A reduced-scope tool variant** (§3). The real recovery.
- **A human WAIT gate.** The run suspends on the existing durable wait rather than failing, and
  an operator decides. This is strictly better than today because the accumulated state survives,
  and it needs no new machinery — the wait/resume path already exists and is durable.

**Excluded, and this is a decision rather than an omission:**

- **Automatic substitution of `sys.v1.agent.simulate`.** The entry notes, correctly, that the
  runtime has a *better* fallback available than the system that prompted the finding: a
  zero-side-effect rehearsal against virtual tools. It is better as a **diagnostic** and worse as
  a **recovery**. A simulated result is not a result; feeding predicted output into the next step
  means the rest of the run computes on data describing an effect that never happened, and every
  downstream step believes it. That is a *lie* in exactly `EFFECT-PARTIAL-1`'s sense, and this
  runtime just spent a change removing one.

  ★ The right place for simulate is **at the WAIT gate**: the operator deciding whether to grant
  authority can ask what would have happened. Rehearsal informing a human is the use it was built
  for; rehearsal silently replacing an effect is not.

### 5a. The WAIT gate as built (phase 2, 2026-09-15)

**Declared by the tool:** `register_tool(..., on_denial="wait")` (default `"fail"`, every tool's
behaviour today). Independent of `degraded_variant`; the two compose in a fixed order with no
chain: the variant first (one attempt, phase 1), and only if there is none or the token does not
grant it does the gate apply. The gate is a park, not another downgrade, so §4's bound holds.

**Where:** `agent_execute_step` — the one negotiable site — returns `WAIT` on the event
`agent.authority.decision`, with an `authority_gate` record (step, tool, denial, what the
negotiation found) on the flow state, and a **`resume_schema`** (`WAIT-TYPED-CONTRACT-1`, the
runtime's first typed wait): `{"required": ["decision"], "properties": {decision: string,
note: string}}`. `AUTHORITY_NEGOTIATED` is recorded with `outcome: "waiting"`; the counter gets
`waiting`.

**★ `AGENT_FLOW` had never waited.** Its orchestration read any non-`SUCCESS` flow result as
failure — a parked run would have been marked `failed` while its FlowRun sat `waiting`. It now
mirrors the nodus_vm chain: `AgentRun.status = "waiting"`, a durable `wait_state {event_type,
flow_run_id, authority_gate}`, the `WAITING` agent event. The agent's own execution unit stays
`executing` while parked (nothing resumes an agent EU; parking it would strand it).

**The decisions — two, and the two that are absent are decisions too:**

- **`skip`** — the step is recorded as an `AgentStep` with `status="skipped"` and the note; the
  run advances. The nine steps of work survive; the tenth is a human's call.
- **`abort`** — the run fails with the operator's reason, recorded on the step and the run.
- **Not `grant`.** §7: no widening path, not even an authorised one. The vocabulary does not
  contain the word, and an unknown decision **re-parks** the run (recorded as
  `last_refused_decision` on the gate) rather than failing it — a typo must not kill a run an
  operator is trying to steer.
- **Not provide-a-result** (human-as-the-tool). Deferred, not declined: it is the most useful
  human-in-the-loop kind and the most dangerous — an asserted result is `EFFECT-PARTIAL-1`'s lie
  in a nicer costume until it is designed on its own terms (who vouches, how it is marked, what
  downstream steps may assume). If built, it is a third decision on this gate, not a new gate.

**One gap the gate exposed and closed on the way:** on this backend a failure AFTER a resume
never reached the AgentRun — the orchestration's post-hoc "flow failed → run failed" block runs
only after the original `runner.start` returns, and a resumed run finishes on a scheduler thread.
`agent_execute_step`'s failure branches now mark the AgentRun `failed` themselves, and
`agent_finalize_run` / the failure path sync the agent's execution unit (`_sync_agent_eu_terminal`),
because `execute_run`'s tail — which does that on the original path — never runs for a resume.
Cancel-while-parked needs nothing new: `CANCEL-REACH-1` refuses the resumed step's tool call.

**Still behind the same flag.** The gate is a negotiation outcome; phase 3's flip covers both.

---

## 6. Recorded, or it does not exist

- `AgentEvent` `AUTHORITY_NEGOTIATED`, carrying the denied capability, the fallback tool, and the
  outcome. It goes in the set in `AINDY/db/models/agent_event.py` beside `CAPABILITY_DENIED`.

  ★ **Correction to a warning that is easy to inherit wrongly:** this does **not** trip the frozen
  event-contract baseline. `tests/baselines/system_event_contract.json` covers `SystemEventTypes`;
  `CAPABILITY_DENIED` is not in it, because agent events are a separate list with no hash guard.
  `INFINITY-RUNTIME-1`'s "regenerate the baseline in lockstep" gotcha applies to *system* events
  only — checked, because the two lists are easy to confuse and the wrong half of that advice
  sends someone regenerating a baseline that has nothing to do with their change.
  **The absence of a guard is worth noting on its own:** nothing pins the agent-event vocabulary,
  so a typo'd event type is accepted silently.
- Both attempts in `FlowHistory`, so the record shows what was refused *and* what ran. A record
  showing only the successful fallback describes a run that never hit a denial.
- `aindy_authority_negotiation_total{outcome}` — `succeeded | no_variant | variant_denied |
  refused_not_granted`.

★ **The counter is not optional decoration.** Without it, "negotiation never fires because
denials are rare" and "negotiation is not wired to anything" are indistinguishable — which is
precisely the ambiguity that made `CANCEL-REACH-1` ship a counter, and precisely the class
`ROUTE-AST-UNWIRED-1` catalogues. The `no_variant` label matters most: it says the mechanism ran
and had nothing to offer, which is the expected steady state until tools start declaring variants.

---

## 7. What not to build

- **Do not build a widening path**, not even one that is "only" authorised. The ceiling
  (`capability_service.py:479-491`) and the single minting path are the one hard cryptographic
  guarantee in the enforcement matrix; a second way to grant is how that stops being hard. If
  `amend_token` is ever built, **implement narrowing and leave widening unimplemented rather than
  merely unauthorised** — an unimplemented direction cannot be reached by a bug.
- **Do not make the fallback implicit.** No "retry with whatever the token happens to allow."
  Undeclared recovery is the flow-state problem in another costume: the runtime picking an answer
  it has no basis to pick (`FLOW-PARALLEL-1`).
- **Do not negotiate on anything but a capability denial.** A tool failing for its own reasons is
  `RETRY-CLASSIFY-1`'s question and has a different correct answer.
- **Do not start this before `TOOL-SEAM-ISOLATION-1`'s registration surface is understood** — the
  declaration lands on `register_tool`, beside `env_spec` and `isolation`, and a fourth
  independent vocabulary there would be the mistake `FS-SCOPE-1` warns about.

---

## 8. Phasing

| | | |
|---|---|---|
| ~~**0**~~ | ~~`degraded_variant=` on `register_tool`, validated at registration, **consulted by nothing**~~ | **DONE — #600.** One correction to this row: validation had to SPLIT. Local checks are in the decorator; the three cross-tool rules are a STARTUP sweep (`validate_degraded_variants`), because a forward reference is legitimate and the capability *definitions* the subset rule needs load later, from plugin providers. "At registration" was not achievable as written |
| ~~**1**~~ | ~~The negotiation stage at the two `CAPABILITY_DENIED` sites, gated default-off~~ | **DONE.** Two corrections to this row: it is **one** site, not two (see §1's correction), and the subset rule of §2 is **asked of `check_tool_capability` rather than reimplemented** — a hand-rolled set comparison beside the real one would have omitted the granted-tools test and the agent capabilities that function also enforces |
| ~~**2**~~ | ~~The WAIT-gate fallback kind~~ | **DONE — 2026-09-15**, §5a. The row's "no new machinery" was half right: the durable wait was reused as is, but **`AGENT_FLOW` had never waited** and its orchestration had to learn that `WAITING` is a park, not a failure; and a failure after a resume had never reached the AgentRun on that backend |
| **3** | Flip the default once a real tool declares a variant or a gate and a denial has been observed | evidence, not code — zero tools declare either at HEAD |

Phase 0 is worth landing alone: it is inert, it makes the vocabulary reviewable, and it is the
same declare-then-enforce sequence that made `EXEC-ENV-BIND-1` safe to land in pieces.

★ **Do not close this entry on phase 0.** A declaration nothing consults is `G4a` — built and
inert — and this repository already has one of those. **Phase 0 shipped 2026-09-08 and the entry
stays OPEN.** Its inertness is pinned by
`tests/unit/test_authority_negotiation_declaration.py::test_nothing_in_the_execution_path_consults_the_field`,
an AST scan that fails the moment any execution path reads the field. **When phase 1 lands, that
test must be REPLACED deliberately with one asserting the negotiation behaviour — not deleted
because it went red.**

★ **One thing phase 0 found that this document had wrong:** it specified validation "at
registration", which is not achievable for the cross-tool rules. See the phase table above.
