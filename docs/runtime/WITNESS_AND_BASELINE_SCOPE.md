---
title: "Substrate Witness and Performance Baseline — Scope"
api_version: "1.0"
last_verified: "2026-09-03"
status: current
owner: "platform-team"
---

# Scope — `SUBSTRATE-WITNESS-1` and `PERF-BASELINE-1`

**These two are filed separately and share one root: a capability exists and nothing exercises
it.** Neither is a missing mechanism, so neither is finished by writing more runtime code — which
is exactly why both have sat open while code-shaped entries around them closed.

Everything below is **measured at `9e6df8c`**, not read from the registry. Three registry entries
were found stale today; the numbers here were re-derived.

---

## Part 1 — `PERF-BASELINE-1`

### What changed since filing, measured

| | at filing | at `9e6df8c` |
|---|---|---|
| test files reading a metric (`get_sample_value` / `generate_latest` / `.collect()`) | **0** | **7** |
| test files driving concurrency (`ThreadPoolExecutor` / `gather` / `Barrier`) | **0** | **7** |
| soak suites | 0 | 4 (`soak_harness` + idempotency contention, scheduler dispatch, distributed resume) |
| **latency assertions** | **0** | **1** |

**The half that was actually blocking is closed.** The original finding was not "we lack a
benchmark" — it was that 52 registered metrics existed and *nothing read them*, and that the
integration suite was entirely sequential, so flags had been proven correct and never under
contention. Both are fixed: the harness exists, it does before/after metric readback, and it has
found real defects (`EXACTLY_ONCE` is not exactly-once under contention).

### ★ What remains is the original headline, and it is still true: **one latency assertion**

That is the part to be careful about, because it is the part most likely to be "fixed" badly.

**Do not add latency assertions to the existing suites.** A wall-clock threshold in a test that
runs on shared CI hardware is a flake generator, and this repository has already paid for one
flake hunt (`FLAKY-1`) that produced three wrong readings before the fourth run refuted them. A
threshold that fails intermittently gets widened until it asserts nothing, which is worse than
having none — it is `DOCS-COVERAGE-CLAIM-1`'s shape with a number attached.

**What to build instead — a regression *shape*, not a *threshold*:**

1. **Count work, not time.** The failures this repo actually had were `MEM-RECALL-N1-1` (3 queries
   per candidate) and `RT-MEMTXN-LEAK-1` (a transaction held across a slow call). Neither needs a
   clock: both are visible as *query counts* and *connection-hold counts*, which are deterministic
   and do not flake. `CANCEL-REACH-1`'s suite already asserts "1 status read for 50 checks" — that
   is the model, and it is the cheapest useful thing here.
2. **Assert against the metric an operator reads**, per the soak-harness rule. If the assertion
   and the dashboard disagree about where to look, the test measures a proxy.
3. **Only then, a latency floor**, and only on the soak path where the harness controls
   concurrency — as an *order-of-magnitude* guard against a pathological regression, never a tight
   bound.

**Recommended slice, and it is small:** a `query_count` context manager beside `soak_harness`,
plus assertions on the two paths that already regressed once. That closes the class the entry was
really about; the timing half can stay open honestly.

---

## Part 2 — `SUBSTRATE-WITNESS-1`

### Measured at `9e6df8c`, in `C:\dev\claw`'s own source

| | |
|---|---|
| files referencing `execute_tool` | **0** |
| files referencing `EffectRecord` | **0** |

**Unchanged since filing.** The flagship consumer integrates in ~334 lines across 3 files, all
optional and mostly HTTP, and **its real effects cross no chokepoint**.

### ★ The corollary that matters for reading everything else

The coverage percentages across nine comparative audits describe capabilities the runtime **has**,
not capabilities anything **uses**. That applies directly to the work merged this week: the
outcome vocabulary, the conflict policy, the environment descriptors and the token meter are all
correct, tested, and **exercised by nothing outside their own suites**. That is not an argument
against having built them — the vocabulary has to exist before an emitter can exist — but "we
shipped it" and "it is load-bearing" are different claims and only the first is currently true.

### What needs to be done

**One slice, and the entry already names the right one: route Claw's outbound message delivery
through `execute_tool` with `EXACTLY_ONCE`.**

Why that one specifically:

- It is a **real external effect** — a message that is either sent or not, where a duplicate is
  user-visible. That is the only kind of consumer that would *notice* if the guarantee broke.
- It exercises the chokepoint end to end: `execute_tool` → capability check → `EffectRecord` →
  the idempotency gate, which is on by default since #519.
- It produces the evidence three other entries are waiting on. `IDEM-11`'s production soak has a
  counter to read (`aindy_effect_gate_outcomes_total`) and no traffic; `FR-15`'s distributed
  half needs a real deployment; `DEBT-COMPAT-1` needs a consumer that declares a version range.
  One integration feeds all three.

### ★ What not to do

- **Do not close this with a synthetic fixture.** What is missing is not test coverage of the
  chokepoint — that exists. It is *a consumer that would notice if the guarantee broke*, and a
  fixture written by the same people who wrote the guarantee notices nothing.
- **Do not route everything at once.** One effect, chosen because duplicates are visible in it.
  A broad integration produces a large diff and no sharper evidence.
- **Do not treat this as blocked.** Corrected 2026-08-19 and worth repeating, because eight
  entries inherited the wrong word: every consumer is first-party and owner-controlled, so the
  integration is available whenever it is wanted. "Blocked" was never true; "not yet chosen" is.

### The honest sequencing note

This is the only item in the current backlog whose completion is **not** a runtime change, and it
gates more of the remaining work than any runtime change does. `FR-15`'s production evidence and
the `planner_anthropic` seam adoption are the same shape — three items, all "point a real
consumer at a finished mechanism", and all outside this repository.
