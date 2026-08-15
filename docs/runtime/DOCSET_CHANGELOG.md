---
title: "Docset Changelog"
last_verified: "2026-08-13"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Docset Changelog

This file records notable changes to the runtime docset structure and governance layer.

It is intentionally short.

---

## 2026-08-13 — DOCS-STALE-1 closed; `status: complete` introduced

The seven documents whose `last_verified` predated this repository have all now been read
against source. Six were corrected in place; the seventh, `RUNTIME_DOCSET_BOUNDARY.md`, was a
**finished migration plan** and is now marked as one.

**New frontmatter value `status: complete`**, defined in `RUNTIME_DOCSET_GOVERNANCE.md`. It
covers a one-time plan whose work is done but whose rules may still hold — neither `current`
(the work is not ongoing) nor `outdated` (the reasoning is not wrong). The failure it prevents
is a reader treating a completed plan's task lists as a queue.

`RUNTIME_DOCSET_BOUNDARY.md` itself: all three sections verified done. The third,
*"Shared Or Split Later"*, resolved in a way the plan did not anticipate — rather than splitting
each shared monolith doc, the runtime grew its own companions under different names
(`DEPLOYMENT_PROFILES` + `PROFILE_SUPPORT_MATRIX` for `BOOT_PROFILES`; `ARCHITECTURE` +
`RUNTIME_MODULE_MAP` for `ARCHITECTURE_MAP`; the four `EXTENSION_*` docs for
`PLUGIN_REGISTRY_PATTERN`; `PUBLIC_API_CONTRACT` + `PUBLIC_RUNTIME_SURFACES` +
`ROUTE_OWNERSHIP_INVENTORY` for `API_CONTRACTS`). Intent satisfied, mechanism different.

Also corrected there: its Current Boundary Notes named *"the agent, memory, watcher, and
coordination surfaces"* as runtime-owned `/apps/*` routes. `agent_router` moved to the plugin
layer, and `watcher_router` is not an `/apps/*` route at all — it serves `/watcher/signals` from
`ROOT_ROUTERS`. `APP_ROUTERS` is exactly `memory_router` and `coordination_router`.

**The most-repeated error in the docset**, found in four separate documents during this sweep: a
router file left in `AINDY/routes/` but absent from `APP_ROUTERS` reads as runtime-owned until
you boot the runtime with no plugins.

---

## 2026-08-13 — correctness audit of all 80 runtime docs

Every file in `docs/runtime/` checked on three axes: does it matter, is it in the right folder,
is it correct. Frontmatter was already clean (80/80 complete, all `status: current`), so the
audit went after claims that resolve to something checkable.

**Repaired in place — 28 citations across 12 documents:**

- **8 phantom test files** in 6 docs, several with precise test counts. None has ever existed in
  either repo. Replaced with dated notes naming the removed claim; filed as
  `DOCS-COVERAGE-CLAIM-1`.
- **`MEMORY_ADDRESS_SPACE.md`** cited an Alembic migration (`g5h6i7j8k9l0_...`) for the MAS path
  columns. No such migration; `memory_nodes` is create_all-managed through the schema contract
  and deliberately absent from `env.py`'s `_RUNTIME_TABLES` allowlist. The four columns
  themselves were verified present and correct.
- **`EXTENSION_TRUST_MODEL.md`** — 5 links emitted as `/abs/path/C:/dev/aindy-runtime/...`,
  broken for every reader.
- **`SECURITY_MATRIX.md`** cited `AINDY/platform_layer/extension_trust_model.py`. That module
  does not exist — the *document* name `EXTENSION_TRUST_MODEL.md` had been written as if it were
  a module. The real one is `extension_policy.py`.
- **`UI_CONTRACT.md`** — both cited tests are real but carry a `_ui` suffix added later.
- **10 cross-repo references written as same-repo relative links** (`../apps/`,
  `../architecture/`) in `AGENT_RUNTIME.md` and `RUNTIME_DOCSET_BOUNDARY.md`. The targets exist —
  in `aindy-apps-monolith`. Now plain labelled paths, so the repo boundary is visible in the
  citation. This resolves residual 7 of the closed `DOCS-BUCKET-A-1`, which had recorded them as
  "unresolved by design".
- **`AGENT_RUNTIME.md`** pointed at `AINDY/runtime/RETRY_POLICY.md`; **`EXECUTION_INVARIANTS.md`**
  at `AINDY/docs/runtime/DEPLOYMENT_PROFILES.md`. `docs/` is not inside the package.
- **`RUNTIME_BEHAVIOR.md`** pointed at `docs/deployment/DEPLOYMENT_MODEL.md` — absent from both
  repos. Repointed to `DEPLOYMENT_PROFILES.md`.

**Filed, not fixed:** `DOCS-STALE-1` — seven docs whose `last_verified` predates the repo's first
commit. Repairing citations does not certify prose; those need reading against source.

**Checked and cleared:** dead relative links now zero; cited symbols now zero. Bare module paths
in `RUNTIME_MODULE_MAP.md` and `UI_CONTRACT.md` (`platform/flows_router.py`) are
section-relative by design, not defects.

---

## 2026-05-29

- Introduced the runtime governance-layer docs for boundary, security posture, cross-repo compatibility, degraded-mode truth, stability interpretation, profile support, dependency criticality, release gates, test strategy, change impact, incident classification, decision logging, open questions, and docset governance.
- Added explicit docset precedence so newer narrower governing docs override broader older framing where they conflict.
- Began in-place reconciliation of older runtime docs to align current claims with the newer trusted-internal runtime posture.
