---
title: "App Handoff — Runtime v2.4.1"
api_version: "1.0"
last_verified: "2026-08-19"
status: current
owner: "platform-team"
---

# App handoff — runtime v2.4.1

**This is a patch release and it is short on purpose.** `2.4.1` carries one security-relevant
dependency fix and the grouped dependency bumps. Nothing else changed.

- **No schema change.** Contract stays `2026-08-15.1`, Alembic head stays `0016`.
- **No migration, and nothing to reconcile.** `bootstrap-schema` exits 0 against an existing
  database.
- **No new or changed env var. No new or changed route. No route began enforcing a scope.**
- **No feature requests moved.** `APP-FR-*` is exactly where `v2.4.0` left it — **FR-6 items 2+3**
  and **FR-14's remaining half** are still open, nothing was closed, nothing new was accepted.
- **No compatibility movement.** `recommended_runtime_requirement` stays `>=2.0,<3.0`, so no
  consumer pin has to move.

**`APP_HANDOFF_v2.4.0.md` remains the document to read for behavioural change** — in particular
its §1, the scope-enforcement audit for platform API keys. Everything in it still applies
unchanged. This file only covers what `2.4.1` adds on top.

**Upgrade to `2.4.1` rather than `2.4.0`.** They are the same release except for the pin below.

---

## 1. Why this release exists

`v2.4.0` shipped with `nodus-lang` pinned at `5.0.1`. `nodus-lang <= 5.0.2` bound its
`GLOBAL_MEMORY_STORE` at **import**, so every `NodusRuntime` constructed in one process shared a
single guest memory dict. `memory_put` and `memory_get` are guest builtins available to any `.nd`
script, so one script could read another's stored values. Upstream `5.0.3` gives each runtime its
own store; sharing is now opt-in. This release pins `5.0.4`.

### Whether it reached you depends on one flag

| `AINDY_NODUS_WARM_POOL` | Exposure | What to do |
|---|---|---|
| **off** (the default) | **None.** Worker processes are not reused, so two tenants' scripts never share a process. | Upgrade at your convenience. |
| **on** | **Two tenants' `.nd` scripts served by the same warm worker could read each other's guest memory.** | Upgrade before anything else — or turn the flag off until you have. |

If you have never set `AINDY_NODUS_WARM_POOL`, it is off, and this was latent for you exactly as
it was for us.

**No app-side change is needed either way.** The fix is entirely in the pin — no API moved, no
call site changes, nothing to migrate.

### Why the runtime's own docs did not catch it

`AINDY/runtime/nodus_worker_pool.py` asserted in its docstring that a reused process *"never
leaks state between runs"*, on the strength of `run_one` rebuilding per-request state. That was
true of the state the runtime owns and false for the channel that mattered: **`run_one` cannot
reset a module global living inside a dependency.** The docstring is corrected.

We are recording this because it generalises: an upstream bug can invalidate a downstream
docstring, and nothing greps for that. If you have made similar isolation claims about processes
you reuse, the same question applies.

### What pins it now

`tests/unit/test_nodus_upgrade_contract.py::test_two_runtimes_in_one_process_do_not_share_guest_memory`
— reproduced against the real VM on our own import path before the bump (`5.0.1` printed
`password123`; `5.0.4` prints `nil`), mutation-tested 2 of 11.

---

## 2. Dependency pins that moved

| Package | From | To | Notes |
|---|---|---|---|
| `nodus-lang` | 5.0.1 | **5.0.4** | §1 |
| `SQLAlchemy` | 2.0.51 | 2.0.52 | two behaviour changes; neither API is used by this runtime |
| `uvicorn` | 0.52.1 | 0.52.3 | `zttp` parser, bodyless request receives |
| `Mako` | 1.3.12 | 1.4.1 | **breaking upstream**: Python floor 3.10, MarkupSafe floor 2.0 |
| `regex` | 2026.6.28 | 2026.7.19 | two segfault fixes; transitive, never imported here |
| `prometheus-fastapi-instrumentator` | 8.0.2 | 8.1.0 | `root_path` / nested-app label changes |

**Two of these need a second look on your side, and neither is visible in the version distance:**

- **`Mako` 1.4.0 is a breaking release.** It raises its Python floor to **3.10** and its
  `MarkupSafe` floor to **2.0**. The runtime declares `requires-python = ">=3.11"` and
  `MarkupSafe==3.0.3`, so it is satisfied here. **If your deployment pins an older Python or an
  older MarkupSafe, check it** — `1.3.12 → 1.4.1` does not look like a floor change.
- **`prometheus-fastapi-instrumentator` 8.1.0 changes handler labels** when a `root_path` is
  configured or apps are nested. **It cannot affect the runtime — the package is declared in both
  pin files and imported nowhere in our source** — but if *your* app instruments with it, and you
  have dashboards or alerts keyed on the `handler` label, read its 8.1.0 notes before upgrading.

`SQLAlchemy` 2.0.52's two behaviour changes are `aliased()` on select/union constructs and
`Table.to_metadata()` copying rather than reusing default objects. Neither API appears anywhere
under `AINDY/`; we grepped rather than assumed. If your app uses either directly, they are worth
reading.

---

## 3. Upgrade path

```bash
pip install --upgrade "aindy-runtime==2.4.1"
```

Nothing else. No `bootstrap-schema` step, no `--reconcile`, no restart ordering constraint beyond
your normal one. If you are coming from a release **before** `2.4.0`, follow
`APP_HANDOFF_v2.4.0.md` §5 — this release adds no step to it.

**Docker:** the image's builder-stage pin is `2.4.1`. As always, the SPA that ships in the image
is whatever was packaged into the pinned version, not your working tree.

---

## 4. Soak flags — unchanged

Section 7 of `APP_HANDOFF_v2.4.0.md` is still the inventory, and none of it moved. One addition,
which is a **precondition rather than a change**:

> **`AINDY_NODUS_WARM_POOL` now has a re-verification step attached.** Before enabling it after
> **any** dependency bump, re-run
> `test_two_runtimes_in_one_process_do_not_share_guest_memory`. A reused process is only as
> isolated as its most import-bound dependency, and the pool's own safety claim was false on the
> pin `v2.4.0` shipped.

The uncomfortable ordering is worth stating plainly: had that soak been run before this bump, it
would have run on a pin that made the pool's own safety claim false.

---

## 5. Known-open, so you are not surprised

Unchanged from `v2.4.0` §8. Two worth restating because this release touches their neighbourhood:

- **`GUEST-CONFINE-1` residual.** The guest VM is confined (no subprocess, network, or host env),
  but **nothing sets its `cwd`**, so `allowed_paths` inherits the server's working directory. In
  Docker that is `/home/aindy`, which holds `alembic/`. The escape is closed; the *bound* is an
  undeclared inherited default. Not changed by this release.
- **`TOOL-SEAM-ISOLATION-1`** remains the one actionable P0: `execute_tool` runs tools in-process
  with the live DB session, so every authority check at that seam is advisory with respect to the
  code that runs next.

---

## 6. Verification behind this release

Stated because "it was green" means different things per check:

- All required checks green on the tagged commit `d6c64d9` — verified bound to that SHA.
- **`Upgrade Path Guard` passed trivially**, because there is no schema change for it to detect.
  Its **`negative-control` job**, which injects synthetic drift and requires exit 3, is the half
  that carries meaning on a release like this one, and it passed.
- **Sandbox escape gate: 17/17 PASS, 0 FAIL, 0 SKIP** on native Linux (`SANDBOX_ESCAPE_AUDIT.md`
  Entry 018). **Read that entry rather than the number** — it records that this gate certifies the
  Tier-2 extension sandbox, **not** the Nodus guest boundary, and would have reported 17/17
  whether or not §1's bug existed.
- **Installed from PyPI into a clean environment**, confirming the published wheel reports
  `2.4.1` and resolves `nodus-lang 5.0.4`.
