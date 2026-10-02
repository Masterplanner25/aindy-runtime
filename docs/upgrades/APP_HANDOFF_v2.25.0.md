---
title: "App Handoff — Runtime v2.25.0"
api_version: "1.0"
last_verified: "2026-10-01"
status: current
owner: "platform-team"
---

# App handoff: runtime v2.25.0

Pin bump `constraints.txt` `==2.24.0` → `==2.25.0` (your contract test moves the floor with it).
No ui-kit change: `@aindy/ui-kit` 2.1.1 stays current.

**Your register, answered again.** FR-48 (§3), FR-49 (§4) and FR-50 (§5) are in this release, plus
two items announced earlier that land now: authority negotiation on by default (§1) and `nltk` /
`textstat` leaving the runtime's dependencies (§2). **Required of you: the pin and a rebuild.**
There is no migration. **Asked of you:** declare `result_schema` on two tools (§3), and read one
soak window off the new counter (§4).

> ## ★ Confirm what you are actually running: in the container, printing the path.
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
> # expect: 2.25.0 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 0. Schema: none

No change under `AINDY/db/models/`. Alembic head stays **`0020`**, schema contract **`2026-09-20`**.
`bootstrap-schema` exits 0 on a current stack. The `Upgrade Path Guard` passes **trivially** on a
release like this, because there is no drift to find; its negative control is the half that carried
meaning, and it was green.

## 1. ★ Authority negotiation is ON by default (#771, DEC-081)

`AINDY_AUTHORITY_NEGOTIATION` now defaults on. Only `0` / `false` / `no` / `off` disable it. Any
other value, blank included, is the default.

**For your stack this should change nothing**, because you already run it on (soak register §7).
On a deployment that did not set it, a tool that declares `on_denial="wait"`, which is
`leadgen.act`, now **parks** its run on a capability denial and waits for an operator's
`skip` / `abort` instead of failing it. A tool that declares neither `degraded_variant` nor
`on_denial` behaves exactly as before.

One change you can see in metrics: on `nodus_vm`, the worker no longer pre-checks capability for a
tool that declared no recovery. Before the flip it checked every step twice. As a result, an
undeclared tool's denial on `nodus_vm` no longer counts
`aindy_authority_negotiation_total{outcome="no_variant"}`.

## 2. ★ `nltk` and `textstat` are no longer installed with the runtime (#784, PACK-DEBT-6)

Announced in 2.22.0 for the first release on or after 2026-10-01. You declare both yourself since
your #391, so after the rebuild they should still be there **from your declaration**:

```bash
docker exec <api-container> pip show nltk textstat | grep -E "^(Name|Version|Required-by)"
# Required-by should name your package, not aindy-runtime
```

The runtime's four nltk `pip-audit` exemptions are gone with them. Any nltk advisory is now your
audit's to triage.

## 3. FR-48: a tool can declare what it returns (#769, DEC-077..079)

`register_tool(..., result_schema={...})`, in the same dialect as `args_schema`, nested through
`properties` / `items` / `additionalProperties`. Two effects:

- The planner's catalog line gains `returns={...}`, so the planner sees result shapes.
- **With `AINDY_PLAN_STEP_REFERENCES` on (you run it on)**, each `{"$from_step": N, "path": "…"}`
  is checked against step N's tool schema **at plan time**. `615b67ea`'s `"path": "results"` on
  `research.query` would now be refused before step 0 ran, with an error naming what the tool
  returns.

**Ask:** declare it on every tool whose result a later step may reference. Start with
`research.query` and `search.query`:

```python
register_tool(..., result_schema={"type": "object", "properties": {"raw_result": {"type": "string"}}})
register_tool(..., result_schema={"type": "object", "properties": {
    "results": {"type": "array", "items": {"type": "object",
                "properties": {"id": {"type": "string"}, "title": {"type": "string"}}}}}})
```

The rules (DEC-077):
- A node that lists `properties` is **closed**: a key it does not list is refused, unless you set
  `additionalProperties` (`true`, or a schema for the other keys).
- A node that declares nothing is open. A tool with no schema gets the form check only, as before.
- Results are never validated at run time.

Your #415 prose can stay.

**A refused plan fails run creation; it is not re-planned (DEC-078).** The reason reaches your
route through the existing plan-failure path. FR-46's own default flip comes in a later release,
now that this has shipped (DEC-079).

## 4. FR-49: recall failures are counted (#782, DEC-083)

`aindy_memory_recall_failures_total{site, stage}`. `stage` is one of:
- `recall`: the recall failed and returned an empty context.
- `own_session`: `AINDY_MEMORY_RECALL_OWN_SESSION` could not open its session and fell back to
  the caller's; the recall still ran.
- `setup`: the request pipeline failed before the recall started. This is now WARNING; it was
  DEBUG.

`MemoryOrchestrator.get_context()` takes an optional `site=`. Your nine recall sites count as
`unspecified` until they pass one, which is worth doing.

**A correction to the filing, in your favour:** a failure *inside* the pipeline's recall was always
logged at WARNING as `[MemoryOrchestrator] recall failed`, by the orchestrator the pipeline calls.
So soak row 1's absence signal did cover the 1,332 pipeline recalls. Only the pre-recall branch was
DEBUG. The real gap was that the only witness was a log line, and your api was recreated about six
times.

**Ask:** one soak window on 2.25.0 with the flag still on, then report the counter. **Zero across
every `stage` is the condition for flipping `AINDY_MEMORY_RECALL_OWN_SESSION` by default
(DB-NODUS-BUDGET-1).** Soak register row 1 now reads the counter.

Persisting the per-request `side_effects` map (your ask 3) was declined. For recall it is almost
never populated, and it would grow `system_events` (FR-18).

## 5. FR-50 fixed, and it was wider than filed (#783)

`_dispatch_memory` now leaves an unset optional field out. Three calls answered **400** since the
routes moved onto the dispatcher (2026-08-16):
- `POST /memory/recall` with only `query` (yours);
- `POST /memory/recall` with only `tags`;
- `POST /memory/nodes` without `node_type`.

All three now succeed. A recall with neither `query` nor `tags` is still a 400. If you added the
`"tags": [], "node_type": "insight"` workaround, you can drop it.

## 6. DEBT-COMPAT-1: the runtime checks your declared range at boot (#770, DEC-080)

At plugin load the runtime finds the installed distribution that owns each plugin module (yours:
`aindy-apps-monolith`) and compares its declared `aindy-runtime` range with the running version.
It **warns, never refuses**, on:
- unsatisfied (the running runtime is outside your range);
- undeclared (you declare no dependency on the runtime at all);
- no upper bound.

The result is served as `compatibility.consumers` on `GET /api/version`.

**It reads what pip installed, not your `pyproject.toml`.** On our dev host your installed metadata
said `aindy-runtime>=2.9.0` while your source says `>=2.24.0`. **Ask:** reinstall in your dev venv
(`pip install -e . -c constraints.txt`) so the installed range is the declared one. The container
build already does this.

## 7. Dependencies (#781)

OpenTelemetry `api` / `sdk` / `exporter-otlp-proto-grpc` 1.44.0 → **1.45.0** and
`instrumentation-fastapi` 0.65b0 → **0.66b0**, which moved together as a version-locked family. Also
`annotated-types` 0.8.0, `charset-normalizer` 3.5.1 and `greenlet` 3.5.6. If you pin any
`opentelemetry-*` package yourself, move it in step.

OTel 1.45.0 has one breaking change: a subclass of the OTel `Logger` must implement `enabled()`.
The runtime has none. The release notes for every moved pin were read, and none is
security-classed.

---

## Asks, in one place

1. Pin `==2.25.0` and rebuild. Confirm the version and path, and that `nltk` / `textstat` come from
   your declaration (§2).
2. Declare `result_schema` on `research.query` and `search.query`, and on any other tool a later
   step references (§3).
3. One soak window with `AINDY_MEMORY_RECALL_OWN_SESSION` on, then report
   `aindy_memory_recall_failures_total` by `stage` (§4). Optionally pass `site=` at your recall
   sites.
4. Reinstall your dev venv so the installed `aindy-runtime` range matches `pyproject.toml` (§6).
