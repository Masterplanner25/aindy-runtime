---
title: "Flow Fan-Out and Supersteps — Design"
api_version: "1.0"
last_verified: "2026-09-08"
status: current
owner: "platform-team"
---

# Flow fan-out and supersteps — design

**`FLOW-PARALLEL-1`, the scheduling half. Proposal only; no code exists.**

Written because `AGENT_WORKING_RULES.md` **§8 Proposal-First Rule** requires an approved
proposal before implementing a large refactor, a runtime behaviour change, or a cross-layer
boundary change. This is all three. §10 below is the impact analysis that rule asks for.

**Read §3 first.** It is a hard constraint the `TECH_DEBT.md` entry does not mention, and it
narrows the design more than anything in the comparative research does.

---

## 1. What is already settled, and must not be re-litigated

The conflict half shipped in #569 (`AINDY/runtime/flow_engine/state_merge.py`):

- a declared per-cell policy (`state_policies` on the flow definition): `last_write_wins`,
  `reduce`, `barrier`;
- **no default** — an undeclared double-write raises `StateMergeConflict`;
- **determinism, not merging, is the property**: `last_write_wins` resolves in *declaration*
  order, `reduce` accepts only commutative-and-associative operators;
- branches writing *different* cells need no declaration.

`merge_state(state, patches, policies=…)` already takes an ordered sequence of
`(writer, patch)` pairs, and with `len(patches) <= 1` it is byte-for-byte `state.update(patch)`.
**The merge seam is finished and live on the sequential path** — this design has to feed it, not
build it.

Also settled, with `EFFECT-PARTIAL-1`: **the barrier is the commit boundary, a branch is a unit,
and a superstep in which some branches fail is a `partial` outcome.**

## 2. The reframe

From the MAF study: `PersistentFlowRunner` already commits one `FlowHistory` row per node with a
monotonic `sequence_number`.

> **A superstep is the existing per-node commit boundary widened to span a barrier-delimited
> group.** The work is not "add concurrency to the flow engine" — it is *widen the transaction*.

Today `resolve_next_node()` (`node_executor.py:49`) returns exactly one successor or `None`, and
`runner.py`'s `while True:` advances one node at a time. Three independent API calls in a plan
take the **sum** of their latencies.

---

## 3. ★★ The constraint that shapes everything, and is absent from the entry

**Concurrent branches cannot share the runner's database session.**

`AGENT_WORKING_RULES.md` §5 is unconditional: *"Never share SQLAlchemy sessions across threads or
requests."* `INVARIANTS.md` (17) says the same from the request side. This is not advisory —
SQLAlchemy sessions are not thread-safe, and the failure is silent corruption rather than an
exception.

`PersistentFlowRunner` holds exactly one: **19 uses of `self.db` in `runner.py`, 10 more in
`runner_steps.py`**, and node execution is handed `db=self.db` directly. So every branch that
runs concurrently needs **its own session**, and that has three consequences the entry does not
address:

**(a) Sessions are drawn from a shared, finite budget, and this repo has already been burned by
exactly this.** `SYSMAX-5` was fixed *by isolation rather than capacity*, on the finding that
scheduler threads share the DB connection budget with request handling — so adding threads
starves the API instead of speeding anything up.
`tests/unit/test_scheduler_executor_lanes.py::test_total_scheduler_threads_leave_db_headroom`
pins that trade so it cannot be made accidentally. **A fan-out width of N takes N connections
from the same budget.** Unbounded fan-out is therefore not merely inefficient, it is an
availability regression in the API, and the branch width must be bounded and accounted for the
same way the scheduler lanes are.

**(b) A branch's session must not be held across the branch's slow work.** `RT-MEMTXN-LEAK-1`'s
rule — never hold an open transaction on a shared session across a slow external call, order the
code so the external call precedes the DB work — applies per branch, and more sharply, because N
branches multiply the holding time by N.

**(c) The merge and the commit stay on the RUNNER's session, single-threaded.** Branches produce
patches; the runner merges them in declaration order and writes the superstep. This keeps
`merge_state`'s determinism guarantee intact and keeps exactly one writer on `FlowHistory`.

**★ This makes the "collect patches, merge centrally" shape not just tidy but forced.** A design
where branches write state directly would need cross-session coordination the runtime has no
primitive for.

---

## 4. `sequence_number` breaks under concurrency, and says so itself

`runner.py` allocates the ordinal as `max(sequence_number) + 1`, under this comment:

> *"max()+1 is safe: a run's nodes execute sequentially (no concurrent writers), and it continues
> correctly across a resume (seeds from the highest existing sequence)."*

**The stated precondition is exactly what fan-out removes.** Two branches allocating concurrently
would collide, and `DUR-4`'s fold depends on that ordinal being a deterministic total order.

Since §3(c) keeps the runner the only writer, the fix follows: **the runner allocates the whole
superstep's ordinals at the barrier, in declaration order**, not per branch as it finishes.
Declaration order is already the merge order, so history order and merge order agree by
construction rather than by luck.

**★ Do not make the ordinal a compound key** (`(group, branch)`), tempting as it looks. `DUR-4`
folds a flat monotonic sequence; a compound key changes the fold's contract and every reader of
it, for a property a flat allocation already provides.

---

## 5. The open question this design cannot dodge: resume mid-superstep

A run suspends when a node returns `WAIT`. **What happens when one branch of four WAITs?**

Three candidates, and this needs a decision before code:

1. **The superstep suspends whole.** The other three branches' patches are held un-merged until
   the waiter resumes. Simple, matches "the barrier is the commit boundary", and **loses work on
   crash** unless the held patches are durable — which means a new durable record for
   partially-completed supersteps.
2. **Completed branches commit; the superstep re-enters on resume.** Needs the re-entry to skip
   already-completed branches, which is `RECOVERY-GRANULARITY-1`'s problem arriving here: the
   flow layer already commits `FlowHistory` *before* the snapshot advance — the `FlowHistory(...)`
   add and its `self.db.commit()` precede `_handle_node_status` — so the record to skip from exists.

   > **★ Cited by symbol, not by line, deliberately.** `RECOVERY-GRANULARITY-1` records this as
   > `runner.py:347-359`. At HEAD on 2026-09-08 that range is the node-execution call; the commit
   > is at `:392`–`:403`. **The claim is still true and its coordinates rotted** — the same decay
   > this document is trying not to inherit. Corrected in that entry too.
3. **Refuse `WAIT` inside a fan-out group in phase 1**, and lift it later.

**Recommendation: (3) for phase 1, then (2).** (1) invents a durable record for a case that may
never be wanted; (2) is right but couples this work to `RECOVERY-GRANULARITY-1`; (3) is a loud,
declared limitation that keeps phase 1 reviewable and cannot silently lose anything. A `WAIT`
raised inside a group refuses at the barrier with a message naming the branch.

**★ This interacts with `FR-15`.** A distributed resume rebuilds the run from `run_id` + `eu_type`;
a half-finished superstep is state that reconstruction would have to know about. Deciding (3)
first keeps the two entries independent, which they are not under (1) or (2).

---

## 6. Predicates as data — real, and separable

`FLOW-GRAPH-SIGNATURE-1` records a **deliberate blind spot**: a changed predicate does not
quarantine a suspended run, because the signature hashes topology and not predicate
implementations *or names*. It names this entry as the fix.

MAF hit the same wall and documented it: predicates do not serialize, `_missing_callable` appears
at four separate sites in `_edge.py`, and their answer was consistent — **serialize the shape,
name the predicate, fail loudly if it is missing on restore.**

Ours is harder: `node_executor.py:57` calls `edge["condition"](state)`, a **Python closure over
in-process state**. Naming those is a prerequisite for workflow-as-data.

**★ It is NOT a prerequisite for fan-out**, and should not be bundled with it. `FanOutEdgeGroup`
needs no predicate; only `SwitchCaseEdgeGroup` does — and in MAF's model a switch *subclasses*
fan-out, so the ordering is: build fan-out, then express switch as a constrained fan-out once
predicates are named. Bundling them makes one reviewable change into two unreviewable ones.

---

## 7. Graph-signature compatibility

`graph_signature._canonical_edges` today canonicalises two shapes: a dict edge (recorded as
`{"target": …, "gated": True}`) and a bare string. A fan-out group is a **third shape**.

Two requirements, and the first is the one that would bite:

- **Introducing the shape must not change the signature of any existing flow.** Every flow in
  flight is hashed under today's canonicalisation; a canonicaliser that reorders or re-encodes the
  existing two shapes would quarantine every suspended run on upgrade. `FLOW-GRAPH-SIGNATURE-1`'s
  own rule — *absent ≠ mismatch* — exists because that class of mistake is easy.
- **Adding a fan-out group to a flow SHOULD change its signature.** That is a topology change and
  a suspended run planned against the sequential shape must quarantine. This is the mechanism
  working, not a problem to design around.

A regression test pinning the signature of an existing fixture flow across this change is the
cheapest guard, and it belongs in the same PR as the shape.

---

## 8. Phasing

| | | |
|---|---|---|
| **0** | The superstep seam: the runner loop takes a **frontier** (a set of nodes) that is always a singleton today; ordinals allocated for the frontier in declaration order | inert — identical behaviour, real seam |
| **1** | `FanOutEdgeGroup` declared in the flow graph, executed with **bounded** width and **per-branch sessions**; `WAIT` inside a group refused | the behaviour change, default-off |
| **2** | `FanInEdgeGroup` / join policies (`all`, `any`, `quorum(k)`) resolved at the barrier, partial outcomes per `EFFECT-PARTIAL-1` | |
| **3** | Named predicates, then `SwitchCaseEdgeGroup` as a constrained fan-out; closes `FLOW-GRAPH-SIGNATURE-1`'s blind spot | separable, see §6 |
| **4** | Flip the default once a real flow declares a group and a superstep has been observed | evidence, not code |

**Phase 0 is worth landing alone** and is the honest first step: it puts the widened transaction
on the live path with a frontier of one, so phase 1 is a small diff against a reviewed seam
rather than a large diff against today's loop. It is the same reason `state_merge` was wired in
before there was a second writer.

**★ Do not close `FLOW-PARALLEL-1` on phase 0 or 1.** Fan-out without a join is half a primitive.

---

## 9. What not to build

- **Do not take MAF's durability posture.** Its checkpoint chain does not survive the process;
  crash-durable orchestration is delegated to Azure Durable Task. **Take the topology model,
  refuse the delegation** — durability is the part this runtime already has.
- **Do not take reducer-mediated channels wholesale.** That is a language-layer concern and
  belongs on Nodus's absorb list. What belongs here is the narrower thing already shipped: a
  declared merge policy per key.
- **Do not make fan-out width unbounded or configurable per flow without an accounting story.**
  See §3(a): the width spends the API's connection budget.
- **Do not add a second `FlowHistory` writer.** §3(c) and §4 both depend on there being one.
- **Do not bundle predicate-naming with fan-out.** §6.

---

## 10. Impact analysis (`AGENT_WORKING_RULES.md` §8)

**Invariants touched** (`docs/platform/governance/INVARIANTS.md`):

- **(17) per-request DB session isolation** — not violated, and §3 is written to keep it that
  way: branches get their own sessions, the runner's session stays single-threaded. A design
  sharing `self.db` across branches would violate both (17)'s intent and §5 outright.
- **(2.2) required `SystemEvent` emission fails closed** — a superstep emits per-branch node
  events; the emission contract is unchanged, the count per superstep is not.
- **`DUR-4`'s fold** depends on `sequence_number` being a deterministic total order. §4 preserves
  it by allocating at the barrier in declaration order.

**Schema and migration implications:** **none for phases 0–2.** `FlowHistory` already carries
`node_name` and `sequence_number`; a superstep is N existing rows, not a new table. Phase 5's
option (1) in §5 *would* need a durable partial-superstep record — a further reason to prefer (3).

**API contract implications:** none. Fan-out is a flow-definition feature; no route signature or
envelope changes. The envelope's `partial` status already exists (`EFFECT-PARTIAL-1`) and is the
vocabulary a partially-failed superstep reports through — **no consumer change is required**,
because consumers were already told to branch `!= "success"` in the 2.9.0 handoff.

**Blast radius:** `AINDY/runtime/flow_engine/` only. No `AINDY/routes/`, no `AINDY/db/models/`,
no `AINDY/config.py`, no `AINDY/db/database.py` — none of §1's approval-required surfaces are
touched by phases 0–2.

**Rollback:** phase 1 ships default-off behind a flag, and a flow that declares no group takes a
frontier of one, which is today's path.

---

## 11. What is being asked

Approval to implement **phase 0** as described in §8, and a decision on **§5** (the recommendation
is option 3: refuse `WAIT` inside a group in phase 1, lift it in a later phase).
