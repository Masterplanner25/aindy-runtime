# RTR — Runtime Roadmap (reading aid)

**Last verified: 2026-08-01.**

A digest of where the runtime's open work actually stands, so you don't have to read
`TECH_DEBT.md` end to end to plan. **`TECH_DEBT.md` is the source of truth** — this file is a
convenience view and goes stale fast. Re-verify before planning off it.

> This was previously an untracked scratch file frozen at ~2026-07-11. By 2026-07-31 six
> items it listed as open had already shipped. It is tracked now so corrections land through
> review instead of silently drifting.

---

## Headline

The picture is better than the old snapshot implied. The harden halves are done, ECOGAP-4
(MCP/A2A) is effectively complete via the MEB program, and ECOGAP-1 Phase 3, ECOGAP-3,
ECOGAP-5a, ECOGAP-6 and NODUS-SYS-SURFACE-1 all shipped in the 07-12 → 07-19 window.

**C3's non-Linux strong sandbox is the only genuine big rock left.** A large amount of
finished capability is sitting behind default-off flags; soaking and flipping those is the
highest value-per-effort work available.

---

## Genuinely open — most remaining work first

1. **C3 non-Linux strong sandbox** — the one real big rock. Needs a platform-native
   strong-VM runner (Windows/WSL2 bridge or macOS track);
   `STRONG_SANDBOX_SUPPORTED_HOST_PLATFORMS` is Linux-only. Plan scoped, unstarted.
2. **Commercial block** — DEPLOY-TARGET-1/2, BILLING-1…5, PAYMENTS-ARCHITECTURE-1,
   TENANT-2, DATA-1. All business-gated; zero engineering started.
3. **RTR-3 BUILD half** — non-nullable `AgentRun.flow_run_id`, run-creation reorder, single
   authority + migration. Trigger: divergence seen in prod.
4. **Architecture cleanup** — LAYER-1/2/4/5, TIER3-10, CLI-1, ROUTE-EXTRACT-*,
   DEBT-COMPAT-1, SYSMAX-1/3/4. Each small, all deliberately deferred.
5. **RTR-2 per-tenant queue lanes** — blocked behind DEPLOY-TARGET-2.
6. **ECOGAP-4 G4a residual** — ungated-secret fail-open + non-inheriting-thread egress escape.
7. **ECOGAP-3 residual** — Gemini/Bedrock providers, on demand only.
8. **Trigger-only** — RTR-6 `ReasoningEvent` model, ECOGAP-5 FireTime primitive, ECOGAP-6
   `worker/__init__.py` coverage.

## Dependency / CI debt

- **DEP-UPGRADE-DEFERRED-1** — OTel must be bumped as one group (single-package PRs fail
  `ResolutionImpossible`); vite 6→8 is a breaking UI major.
- **NATIVE-CI-1** — the Rust pyo3 scorer is excluded from CI, so cargo bumps are
  green-but-unverified and need a local MSVC build.
- **MCP-SDK-2X-1** — `mcp` capped at `<2` in **two** places until nodus-mcp targets the 2.x
  server API.
- **MEM-RECALL-N1-1** — `recall()`'s scoring loop is N+1. Performance only.

## Lowest effort, highest value — soak then flip

Finished work currently dark behind default-off flags, in rough priority:
`AINDY_DURABLE_CONTINUATION[_ALL]`, `AINDY_MEMORY_IDEMPOTENCY`, `AINDY_NEXT_ACTION_ACTING`,
`AINDY_PLANNER_MEMORY_INJECTION`, `AINDY_ASYNC_JOB_LOOP_CLOSURE`,
`AINDY_DELEGATION_PRIVATE_MEMORY`, `AINDY_MEMORY_RECALL_OWN_SESSION`,
`AINDY_AUTONOMOUS_EXECUTE_WINDOW`, `AINDY_DURABLE_STEP_GRANULARITY`.

`AINDY_NODUS_WARM_POOL` is on in CI as of 2026-07-31 but still off by default in production.

---

## Recently closed (context for the above)

Shipped 07-12 → 08-01, and absent from the pre-2026-07-31 snapshot:

| Item | Outcome |
|---|---|
| RTR-4 gap c | Shipped 07-12/13 (#245/#246) — was mislabelled "deferred" in all trackers until 07-31 |
| ECOGAP-1 Phase 3 | Complete 07-12 (DUR-1..4, #235–#240) — was listed as the P0 headline gap |
| C3 | Phases 0–5 complete 06-06; only the non-Linux strong-VM runner is deferred |
| ECOGAP-3 | Both phases shipped 07-12 (#241) — OpenAI is no longer the sole embedder |
| ECOGAP-5 | 5a shipped 07-12 (#243, Alembic 0013); 5b already delivered via RTR-1 |
| ECOGAP-6 | Largely closed 07-12 (#242) — the real gap was `worker_loop.py` coverage |
| NODUS-SYS-SURFACE-1 | Closed 07-12 (#244) |
| DOCS-BUCKET-A-1 | Closed 07-17 |
| NODUS-WARMPOOL-1 | Closed 07-19 — warm worker pool, the durable cold-start fix |
| RT-MEMTXN-LEAK-1 | Fixed 07-19 across three releases (login 43.6s → 0.3s) |
| APP-FR-6 item 1 | `POST /auth/password/change` shipped 07-31; items 2+3 open, blocked on FR-1 delivery |
| DB-NODUS-BUDGET-1 | Verified + both fixes shipped 08-01; root-cause fix opt-in pending soak |

## Release

**v1.11.0 prepared 2026-08-01** — FR-6 item 1 (`POST /auth/password/change`),
`memory prune-cascade-debris`, the DB-NODUS-BUDGET-1 idle-cap raise, and the `mcp<2` cap.
Minor rather than patch because of the new endpoint.

Tag `v1.11.0` after the bump PR merges green — and note the full pipeline (Integration,
Platform UI Build, Package Build, Install Smoke), not just the three required checks, must
be green on the tagged commit. See the release flow under PYPI-PUBLISH-1 in `CLAUDE.md` and
`docs/runtime/RELEASE_CHECKLIST.md`.
