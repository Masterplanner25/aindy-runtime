---
title: "Outbound Handoffs"
last_verified: "2026-09-13"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Outbound handoffs

**Things this repository asks of a sibling repository** (Nodus, the SDK), written from `aindy-runtime` for whoever
is working there. Each one records a defect or gap found *here*, traced into *their* source,
with the fix scoped on their side — none of it is runtime work, which is exactly why it lives in
its own folder: a reader of `docs/runtime/` wants what the runtime guarantees, not what it is
waiting on.

**Direction matters.** This is the opposite of [`docs/upgrades/`](../upgrades/README.md), which
holds what a *consumer* of the runtime must do to move between releases. If you are the app team
upgrading, you are in the wrong folder.

## Index

| Handoff | To | Written | What it asks | Status (checked 2026-09-13) |
|---|---|---|---|---|
| [`NODUS_HANDOFF_v5.0.1.md`](./NODUS_HANDOFF_v5.0.1.md) | `Nodus`, `nodus-mcp` | 2026-08-19 | **Blocking:** `nodus-mcp` capped `nodus-lang<5.0.0`, so the runtime could not adopt nodus 5 without making its own `[mcp]` extra uninstallable. Plus four §4 asks: stable denial-message text (4.1), an exported list of gated builtins (4.2), a supported way to reach the active VM instead of the private `_get_active_vm()` (4.3), builtin-override refusal treated as a security boundary (4.4). | **Blocking ask resolved** — `nodus-mcp 0.1.3` floated the cap; the runtime is on `nodus-lang==5.13.0`. **§4.2 and §4.3 still open:** `nodus_worker.py:508` still calls `runtime._get_active_vm()` (pinned by a test so a rename fails loudly), and nodus 5.13 exports no gated-builtin constant. §4.1 and §4.4 not re-verified. |
| [`NODUS_HANDOFF_a2a_mcp_packaging.md`](./NODUS_HANDOFF_a2a_mcp_packaging.md) | `Nodus` (A2A wire), `nodus-mcp`, `nodus-mcp-server` | 2026-08-19 | **Blocking:** two different packages both claim `nodus-a2a` at `0.1.0` on PyPI. The wire caps `nodus-lang<5.0.0` (third instance of `MCP-SDK-2X-1`). `nodus-mcp-server` duplicates `nodus-mcp` rather than composing with it. §4 lists what the runtime would need from the wire if A2A is ever wired up. | **Unresolved as far as is visible from here.** PyPI still lists exactly one `nodus-a2a` at `0.1.0`; the one that installs requires `nodus-events` / `nodus-queue` / `nodus-state` and not `nodus-lang` — i.e. it is the *non-wire* package, so the wire still cannot ship under that name. A2A remains out of scope for the runtime (`ECOGAP-4`); the runtime-side blocker is `INITIATOR-IDENTITY-1`, not this. |
| [`SDK_HANDOFF_1_0_0_wire_mismatches.md`](./SDK_HANDOFF_1_0_0_wire_mismatches.md) | `aindy-sdk` | 2026-09-13 | Four wire mismatches in the published 1.0.0: `events.emit` sends `type` not `event_type` (422, never worked); `upload_script` sends `source` not `content` (422); `memory.tree` docstring promises a `flat` key that does not exist; PyPI 1.0.0 predates the `flow.run` `initial_state` fix. Plus `extra` and no-whoami notes. | **Open, all four.** Found by reading the SDK against the registry and then running the tutorials live with the published wheel. The tutorials route around 1 and 2 with the generic caller. |
| [`NODUS_HANDOFF_workflow_store_migration.md`](./NODUS_HANDOFF_workflow_store_migration.md) | `Nodus` | 2026-09-09 | **`migrate-store` migrates a truncated census and reports success** — it dropped files by mtime past 30 days, not by status, carrying 432 of 629 with `skipped: 0`. `--dry-run` creates the target store file. `NODUS_WORKFLOW_STORE_ROOT` is documented in a docstring that no longer owns the behaviour. §4 records what the runtime did on its own side. | **Open.** The runtime's side shipped (#611: worker declares `sqlite` + autosweep off; compose sets `NODUS_RUN_STATE_ROOT` behind a volume — `ORCHESTRATOR-SPLIT-1`). **Do not trust `migrate-store` as the 6.0.0 mitigation until this is fixed upstream.** |

## How these are used

- **Before bumping a Nodus pin**, read every row still marked open. A handoff's blocking ask is
  usually a version constraint, and `NODUS-UPGRADE-1` / `MCP-SDK-2X-1` record what it costs to
  bump one site and not the others: the pin lives in **three** places (`pyproject.toml`,
  `AINDY/requirements.txt`, the `Install MCP extra` CI step), and a prophylactic cap on a
  fast-moving first-party dependency turns every major into a two-repo release train.
- **When an ask lands upstream**, update the *Status* cell here with the version that resolved it
  and leave the handoff itself as written — it is the record of what was found, not a living
  spec. If everything in a handoff is resolved, it moves to [`docs/archive/`](../archive/README.md)
  with a row there saying what closed it.
- **Writing a new one:** `NODUS_HANDOFF_<topic>.md` (or `<REPO>_HANDOFF_<topic>.md` for another
  sibling), five-key frontmatter (`Runtime Docs Validation` checks this folder), a *Provenance*
  or *Status* section at the end naming the runtime-side PRs, and a row above. The `TECH_DEBT.md`
  entry that produced the finding should point here.

## Provenance

The three handoffs above were written in `docs/runtime/` and moved here 2026-09-13 in the same
pass that created `docs/upgrades/`. Their `TECH_DEBT.md` pointers (`ECOGAP-4`,
`GUEST-BUILTINS-DEAD-1` / `ORCHESTRATOR-SPLIT-1`) and the `CLAUDE.md` key-file rows were updated
to the new path; nothing else cited them.
