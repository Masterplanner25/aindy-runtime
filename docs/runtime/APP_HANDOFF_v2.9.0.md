---
title: "App Handoff — Runtime v2.9.0"
api_version: "1.0"
last_verified: "2026-09-04"
status: current
owner: "platform-team"
---

# App handoff — runtime v2.9.0

> ## ★ Upgrading **from 2.8.0** is a plain `pip install`. There is no schema step.
>
> 2.8.0 needed `bootstrap-schema --reconcile`; **2.9.0 adds nothing of its own.**
> `SCHEMA_CONTRACT_VERSION` and the Alembic head are unchanged and no migration ships.
>
> **★★ But if you are on 2.7.0 or earlier, you still owe 2.8.0's step.** Skipping a release does
> not skip its schema change: `flow_runs.graph_signature` is still missing on your database, a
> bare `bootstrap-schema` still exits `3`, and under `set -e` with `restart: unless-stopped` that
> is still a **crash loop** that looks like a broken image. Run
> `aindy-runtime bootstrap-schema --reconcile` once, exactly as `APP_HANDOFF_v2.8.0.md` §1
> describes.
>
> We are stating this because "2.9.0 needs no schema step" is true of the *release* and false for
> a *deployment* that skipped one — the same shape as the 2.1.0 handoff whose "nothing to backfill"
> was true about data and read as "nothing to do".
>
> **Please do not pattern-match off any single release.** They genuinely alternate, which is why
> each handoff states it explicitly rather than assuming you remember.
>
> There **is** one thing to read before you upgrade, and it is a *code* change on your side, not a
> deploy step: §1.

---

## 1. ★★ The response envelope has two new `status` values

**`status` in the syscall response envelope may now be `partial` or `unknown`, not only
`success` or `error`.**

```jsonc
{
  "status":  "success" | "partial" | "unknown" | "error",
  "outcome": null,        // or {"units": [...], "detail": "..."} when partial/unknown
  "data":    { },
  ...                     // every other key unchanged
}
```

### What you have to do

**Treat any status that is not `success` as not-success, and reconcile.** Concretely:

```python
# ✅ safe
if envelope["status"] != "success":
    handle_failure(envelope)

# ❌ not safe any more
if envelope["status"] == "error":
    handle_failure(envelope)
# a `partial` falls through to the success branch and you believe a half-applied
# effect fully applied
```

That is the whole ask. If your code already tests `!= "success"`, you have nothing to change.

**We had four of these ourselves.** The runtime's own `/platform/syscall` route,
`nodus_execution_service`, and two sites in the flow engine all branched on `== "error"`, and at
each one a `partial` would have read as success. They were fixed in the same change, and a test
now prevents a fifth. We mention it because we assume your codebase has the same shape ours did —
it is the natural thing to write against a two-valued field.

### ★ Nothing emits the new values in 2.9.0

This upgrade changes **no response you receive**. Not one. The value set widened while the set of
emitters is still empty, deliberately — so you can make the one-line change above at your own
pace, and be ready before the first emitter exists rather than during an incident.

A handler opts in explicitly; there is no path by which an existing syscall starts returning
`partial` on its own.

### Why the field widened at all

A batched effect has three outcomes and the envelope had two. A 5-unit effect with 2 failures was
forced to be either a **lie** (`success`, silently partial) or a **waste** (`error`, discarding
the 3 that landed) — and neither was recoverable afterwards, because nothing recorded *which*
units applied. `outcome.units` is that record.

★ `unknown` is narrower than it sounds: it means **dispatched, outcome unobserved** — a read
timeout after a full request write. It is a claim about the world, not about the runtime's
confidence. An exception nobody classified is still `error`.

★ A `partial` that cannot name its units is **refused** by the runtime and returned to you as an
`error`. You will never receive a `partial` with an empty `units` list; if the mechanism that
produced it could not say what happened, you get the honest failure instead.

---

## 2. `EffectRecord` rows can now say `partial` and `unknown` too

If you read `effect_records` for reconciliation or audit, its `status` column has the same two new
values, written from the same resolution as the envelope.

**This closes a real gap on our side rather than adding a feature on yours.** 2.8.0's predecessor
added those values to the column and the dispatcher's ledger write passed a hardcoded
`"success"` — so the column could hold `partial` and no code path could ever put it there. The
record is what you reconcile from once a response is long gone, which made that the half that
mattered.

Both values are **terminal**, so TTL cleanup reaps them like any other finished effect.

---

## 3. Cancelling a run now stops it sooner

`sys.v1.agent.cancel` previously took effect **between segments**, so every remaining tool in the
current segment ran. A cancelled run now refuses its **next tool call**, which moves the window
from segment granularity to effect granularity.

```jsonc
{"success": false, "cancelled": true, "error": "run <id> was cancelled; tool 'x' not executed"}
```

**Two things to know, because both are contract rather than limitation:**

- **It is cooperative.** A tool already executing is not interrupted; the *next* one is refused.
  If you cancel during a long HTTP call, that call completes.
- **It fails open.** If the runtime cannot read cancellation state, the answer is "not cancelled"
  and the effect proceeds. A missed cancel costs one more effect; a false cancel costs the run.

New metric `aindy_run_cancel_observed_total{surface}` if you want to see it working.

**Still true, and we would rather say it than have you discover it:** a tool running in the
*isolated* out-of-process worker is not reached by this. It is killed by its timeout and by
nothing else, so a cancel during one of those still runs to completion.

---

## 4. Smaller things

- **LLM token usage is metered** — `aindy_llm_tokens_total{provider,model,kind}` and
  `aindy_llm_usage_unreadable_total{provider,model}`, on the runtime's OpenAI, Azure and Anthropic
  clients, including the raw response methods a structured caller needs.

  ★ **These will read zero for you**, and that is expected: you construct provider SDK clients
  directly rather than going through the runtime's seam, so nothing routes through the meter.
  `docs/runtime/LLM_SEAM_ADOPTION_SCOPE.md` covers what routing `planner_anthropic.py` through it
  would involve — including a real regression in error diagnosability you would need to accept
  (provider status code, type and request id move to `exc.__cause__`). **Not a request; a scoped
  option.** No budget enforcement exists and none is coming until something routes through it.

- **A registered tool can declare its execution environment** — `register_tool(..., env_spec=...)`
  bounds an isolated tool worker's environment variables, working directory and wall clock.
  **An undeclared tool is completely unaffected**: no spawn arguments, same environment, same
  behaviour as 2.8.0.

- **A flow can declare per-cell conflict policy** — `state_policies` on a flow definition. Inert
  today: with one writer per node the merge is byte-for-byte what it was. It exists so that when
  fan-out arrives, two branches writing one state cell resolve deterministically instead of by
  whichever finished last. Nothing to do unless you want to declare one.

- **`aindy_syscall_outcome_total{syscall,status}`** — dispatch outcomes by envelope status. A
  non-zero `aindy_syscall_outcome_refused_total` means a *handler* made a malformed outcome claim;
  that is a bug on the emitting side, not a workload property.

---

## 5. Verification

```bash
pip install --upgrade aindy-runtime==2.9.0
aindy-runtime --version                      # 2.9.0

# Coming from 2.8.0: nothing else. Running bootstrap-schema anyway exits 0 (no drift).
# Coming from 2.7.0 or earlier: you still owe 2.8.0's step, or you get a crash loop.
aindy-runtime bootstrap-schema --reconcile   # only if you skipped 2.8.0
```

★ If you are unsure which case you are in, run the bare `aindy-runtime bootstrap-schema` first and
read the exit code: **0** means you are done, **3** means run it again with `--reconcile`, and
**4** means stop and talk to us — `--reconcile` will not help and a human has to act.

Then, once: grep your code for `status"] == "error"` and `.get("status") == "error"` against
syscall envelopes, and change those to `!= "success"`. That is the only required change in this
release.

Full pipeline green on the tagged commit; wheel and sdist pass `twine check`; the packaging,
cross-repo, Alembic-head and schema-contract suites are green.
