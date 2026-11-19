---
title: "App Handoff — Runtime v2.39.0"
api_version: "1.0"
last_verified: "2026-11-19"
status: current
owner: "platform-team"
---

# App handoff: runtime v2.39.0

**Answers FR-55, all three asks.** **No schema step**: Alembic stays at `0022` and the schema contract at
`2026-11-09`. No dependency pin moved. No ui-kit or aindy-sdk release. **Nothing to do on upgrade**: both new functions
are opt-in, and the one change to existing behaviour (§3) only gives a wait the deadline it already declared.

Bump the `constraints.txt` pin to `==2.39.0` (your contract test moves the floor with it), **rebuild the image**
(`mcp` shares it, so recreate both).

> ## ★ After the rebuild: confirm what you are running
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v; from AINDY.core.wait_on_timeout import MAX_WAIT_SECONDS; print(v.__version__, MAX_WAIT_SECONDS, list(AINDY.__path__))"
> # expect: 2.39.0 2592000 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 1. ★ FR-55: a `.nd` workflow can loop and sleep

Your Resolution Check (ask 6 questions a minute from a 1-minute tick, progress in `resolution_answers`) can now be one
workflow that sleeps between batches. Two new guest functions, both documented in the runtime's
`NODUS_DEVELOPER_GUIDE.md` §4:

**(b) `prior_state(key)` (DEC-135, #986).** A resumed script still runs from the top with an empty namespace. That is
DEC-012, which stands: `get_state` is unchanged. `prior_state(key)` returns what this node saved with `set_state` in any
earlier run, or `nil` on a first run. Keys saved in earlier parks are kept until the node finishes, even if a later run
does not save them again. The value is a copy: to carry progress forward, save it again. The runtime's own keys
(`nodus_*`, `__*`) are never returned.

**(a) `await_event_for(event_type, schema, seconds)` (DEC-136, #987).** It parks like `await_event`, with **this wait's
own deadline**. If the event arrives first you get its payload; when the deadline passes the run is **resumed, not
failed**, and the same call returns `{"timed_out": true, "deadline": "<ISO-8601>"}`. With a name nothing emits, it is a
durable sleep: the deadline lives on the run row and survives a restart.

**(c) a per-workflow wait timeout** is answered by (a): a wait declares its own seconds, so nothing raises the
30-minute default for every workflow. There is no `wait_timeout_minutes` on `register_nodus_workflow`.

```nd
let done = prior_state("batches_done")
if (done == nil) { done = 0i }
// ... ask batch number `done` (its six questions), once ...
set_state("batches_done", done + 1i)
if (done + 1i < 9i) {
    let r = await_event_for("resolution.batch." + str(done), nil, 60i)   // sleep a minute
}
```

**What to know before porting:**

- **A distinct event name per iteration.** `await_event` and `await_event_for` return at once for a name that has
  already been delivered, so a loop that waits on one fixed name parks only the first time.
- **`0i`, not `0`.** A bare `0` is a float in nodus: `str(0)` is `"0.0"`, which changes your event names.
- **Seconds:** 1 to 2,592,000 (30 days), rounded up. Outside that the script fails. **Precision is about 15 s**: a
  leader-only scheduler job delivers due deadlines on that interval, so "60 seconds" wakes between 60 and ~75.
- **Event and deadline in the same window:** either may be the one you get. Check `r["timed_out"]`.
- **A typed wait** (`schema` not `nil`) still accepts its own timeout marker; only your callers' payloads are checked.
- **Everything before the wait still runs again on each resume.** `prior_state` is what lets you skip the work you
  already did; a batch's external calls belong after the check, not before it.

**FR-55 closes** when a ported loop runs on 2.39.0: report the workflow, how many iterations it parked and resumed, and
whether any wake landed outside the ~15 s window.

## 2. Your 2.38.0 report

Read (`RUNTIME_2_38_0_UPGRADE.md`, app #627/#628): the confirm line, `alembic current` through the normalised
`env.py`, and `deploy-bootstrap-guard` passing on the adoption PR are everything the handoff asked. Your AST guard over
`create_engine` (with the workflow-YAML scan) is the right shape. Nothing open from it.

## 3. One behaviour change to existing waits

The stuck-run watchdog dead-lettered **every** run waiting longer than `FLOW_WAIT_TIMEOUT_MINUTES` (30), even one whose
own `wait_deadline` was still ahead, which a flow's `wait_timeout_minutes` sets. It now leaves such a run alone until its
own deadline, and never takes a run `await_event_for` resumes. Only a flow that declares `wait_timeout_minutes` above 30
sees a difference, and it now gets the wait it declared.

## 4. Your register

- **FR-55: released here**; it closes on your ported-loop report (§1).
- Still open from earlier handoffs, no new ask: the trace question in your 2.34.0 report (§5 there).
