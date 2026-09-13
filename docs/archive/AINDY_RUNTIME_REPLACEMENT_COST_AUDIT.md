# aindy-runtime — Replacement Cost Audit

**Audit date:** 2026-08-19
**Repository state audited:** branch `docs/registry-accuracy`, HEAD `e9efcf7` (2026-08-17), plus five
modified working-tree documentation files and one untracked doc (listed in §3.4).
**Latest release tag:** `v2.4.0` (2026-08-17).
**Auditor posture:** adversarial. Every capability claim below was traced into source and, where
possible, into a test. Claims that could not be traced are marked as such.

> **What this document answers:** what a conventional software organization would have had to pay
> to end up holding the artifact that currently exists in this repository — without the original
> creator's uncompensated labor.
>
> **What it does not answer:** what the software is worth, what anyone would pay for it, or whether
> it has a market. See §13.

---

## 1. Executive conclusion

All figures are **fully loaded organizational cost** (salary + burden + management + overhead), in
2026 US dollars, at a blended rate of **$265,000 per engineer-year** (derived in §8.1; sensitivity
across $150k–$300k in §8.4). Person-months are abbreviated **PM**; person-years **PY**.

> **★ Scope.** The four estimates below price **`aindy-runtime` alone**, as the audit brief
> specified. The runtime is one half of a deliberate repository split, it depends on a programming
> language written by the same author in the same window, and it does not ship as a usable product
> on its own. **§16 prices the full engineering estate in four concentric scopes:**
>
> | Scope | Contents | Physical lines | Original creation (expected) |
> |---|---|---:|---:|
> | **A** | `aindy-runtime` — *the brief's question, §1–§15* | 191,416 | **$6.6M** / 281 PM / ~10 people |
> | **B** | + monolith (16 apps, 21k-line product SPA, 155 migrations), SDK, UI-kit | 335,737 | **$12.2M** / 520 PM / ~15 people |
> | **C** | + **`nodus-lang`** — lexer, compiler, **VM**, LSP, DAP, stdlib, 33 releases | **491,755** | **$16.3M** / 697 PM / ~19 people |
> | *D* | + 36 `nodus-*` satellites and `claw` | ~551,000 | *excluded — §16.5.1* |
>
> **Scope C is the honest answer to "what did this whole thing cost to create?"** All are correct
> answers to different questions; none revises another.

| # | Estimate | Low | Expected | High |
|---|---|---:|---:|---:|
| 1 | **Clean-room reproduction cost** (rebuild now, this repo as complete spec) | **$1.8M**<br>78 PM / 6.5 PY | **$3.2M**<br>140 PM / 11.7 PY | **$5.5M**<br>240 PM / 20.0 PY |
| 2 | **Original creation cost** (no spec; = reproduction × rework + **invention**, §6.3) | **$2.8M**<br>123 PM / 10.3 PY | **$6.6M**<br>281 PM / 23.4 PY | **$13.0M**<br>539 PM / 44.9 PY |
| 3 | **Production-equivalent replacement cost** (reproduce + close demonstrable gaps) | **$2.4M**<br>105 PM / 8.8 PY | **$4.5M**<br>190 PM / 15.8 PY | **$7.7M**<br>320 PM / 26.7 PY |
| 4 | **Current artifact replacement value** (replace code + tests + docs + decisions internally) | **$2.0M**<br>90 PM / 7.5 PY | **$3.6M**<br>160 PM / 13.3 PY | **$6.3M**<br>275 PM / 22.9 PY |

**Headline figures (expected case):**

- **Estimated total effort:** ~23.4 person-years to create originally; ~12 person-years to reproduce.
- **Likely conventional team size:** 9–11 people at peak (Model B, §7.2); a viable alternative is a
  5–6 person senior team (Model A, §7.1).
- **Likely calendar duration:** 24–28 months for the 10-person org; 40–48 months for the 6-person
  senior team.
- **Recurring operating cost (separate from build):** ~$1.05M/year expected (§12).
- **Confidence:** **Moderate-high.** High confidence in repository scale, subsystem inventory and
  maturity classification (all directly measured). Moderate confidence in effort conversion.
  **The predecessor repository has been located and measured** (§4.2.1), so the ~67% of current
  source that predates this repository's first commit is no longer an unbounded unknown — it is
  549 commits over 57 active days, and it is priced. That closes what was previously the report's
  single largest uncertainty; the dominant remaining one is the compensation assumption (§8.1).

### Plain language

> `aindy-runtime` is roughly a **20-person-year infrastructure system** — not an application, and
> not a prototype. If a competent company had commissioned it from nothing, it would most likely
> have taken a **cross-functional team of about ten people a little over two years**, and cost
> **around $6 million** fully loaded. If that same company were handed this repository as a
> complete specification and told to rebuild it clean-room, it would take **about six engineers a
> year and a half**, and cost **around $3 million**. Making it a *supported* production system —
> load-tested, disaster-recoverable, cloud-deployable, with the default-off capability flags
> actually soaked and flipped — adds roughly **$1.3 million** on top of reproduction.

---

## 2. What aindy-runtime verifiably is

Based on code, not branding.

**It is a self-hostable execution substrate for AI agent systems** — a server-side runtime that
owns the *loop* (schedule → admit → execute → suspend → resume → record) while the application
that mounts on it owns the domain logic. It is packaged as a PyPI wheel (`aindy-runtime`,
`Development Status :: 5 - Production/Stable`, `pyproject.toml:23`) with a CLI entry point
(`aindy-runtime`, `AINDY/runtime_only.py:main`) and a Docker Compose stack.

Concretely, and verified in source:

1. **A syscall contract.** A single dispatcher
   (`AINDY/kernel/syscall_dispatcher.py:SyscallDispatcher.dispatch`, 975 lines) mediates
   **24 registered syscalls** (enumerated live from
   `AINDY/kernel/syscall_registry.py:SYSCALL_REGISTRY`, 2,028 lines; floor pinned at
   `SYSCALL_REGISTRY_MIN_COUNT = 24`), including a versioning demonstration (`sys.v2.memory.read`
   coexisting with `sys.v1.memory.read`). Each dispatch validates the syscall exists, enforces the
   caller's declared capabilities, checks tenant isolation and resource quota, validates input and
   output against declared schemas, runs an idempotency gate, opens an OTel span, and returns a
   uniform `{status, data, trace_id, duration_ms, error}` envelope.
2. **A durable execution model.** `ExecutionUnit` (`AINDY/db/models/execution_unit.py`, 22 columns)
   is a first-class row with a real status machine in which `waiting` and `resumed` are *distinct
   states*, and which carries `memory_context_ids` / `output_memory_ids` provenance. Flow runs
   suspend on `sys.v1.event.wait`, are broadcast across instances over Redis pub/sub
   (`AINDY/kernel/event_bus.py`, 623 lines), and are re-registered on restart
   (`AINDY/core/flow_run_rehydration.py`, 404 lines; `AINDY/core/wait_rehydration.py`, 309 lines).
   Verified by `tests/unit/test_scheduler_wait_resume.py` (19 tests) and
   `tests/integration/test_multi_instance_resume.py`.
3. **A capability-token authority model.** Approval mints an HMAC-signed, scoped, run-bound token
   (`AINDY/agents/capability_service.py:mint_token`, 790-line module with a key ring, refresh,
   expiry and a `capability_ceiling`). Verified by `tests/unit/test_capability_token_integrity.py`
   (8 tests) and `tests/unit/test_delegation_hardening.py`.
4. **A mediated effect boundary.** `EffectRecord` (`AINDY/db/models/effect_record.py`, 13 columns;
   Alembic `0003`, `0004`, `0011`) plus an append-only `effect_reversals` ledger (Alembic `0008`)
   and `sys.v1.agent.undo`. Verified by `tests/integration/test_idempotency_gate_e2e.py` and
   `tests/unit/test_effect_compensation.py`.
5. **A container-grade extension sandbox with adversarial evidence.**
   `AINDY/platform_layer/sandbox_runner.py` (2,437 lines — the largest file in the repository) plus
   `sandbox_certification.py` (920) and `extension_worker.py` (1,299). **17 escape tests across 6
   attack vectors, all PASS**, with machine-readable evidence in
   `tests/sandbox/sandbox_escape_results.json` and **17 dated audit entries** in
   `docs/runtime/SANDBOX_ESCAPE_AUDIT.md` (Entry 001 2026-06-05 → Entry 017 2026-08-18), one per
   release gate.
6. **A memory system on the execution path.** Addressable
   (`/memory/{tenant}/{namespace}/{type}/{id}`, `AINDY/memory/memory_address_space.py`), scored
   (impact / usage / causal depth, `memory_scoring_service.py`), pgvector-backed, with an optional
   **Rust `cdylib` + C++ semantic kernel** accelerator (`AINDY/memory/native/memory_bridge_rs`,
   356 lines Rust/C++) pinned equal to the Python path by parity tests
   (`tests/unit/test_memory_native_scorer.py`, 58 tests) and built in CI on every PR.
7. **A distributed operating layer.** Redis-backed job queue with DLQ, delayed jobs and stale
   requeue (`AINDY/core/distributed_queue.py`, 1,236 lines); lease-based leadership election
   (`AINDY/platform_layer/leadership.py`); orphan-run watchdogs
   (`AINDY/platform_layer/scheduler_service.py`, 942 lines).
8. **An operator surface.** 147 HTTP routes across 24 routers; **11 health/readiness endpoints**;
   **52 Prometheus metric objects**; OTel tracing; a causal `SystemEvent` graph with **46 event
   types** (`AINDY/core/system_event_types.py`) and an `event_edges` table; and a React operator
   console served from the wheel (`platform/`, 7,041 lines, 12 consoles).
9. **A plugin/extension platform.** **40 `register_*` hooks**
   (`AINDY/platform_layer/registry.py`, 2,117 lines) covering routers, flows, jobs, syscalls,
   connectors, event handlers, planner backends, tools and capability providers, with a subprocess
   callback boundary (`runtime_callback_host.py` / `runtime_callback_worker.py`), extension
   signing and provenance.

**What it is not:** it is not an application. A bare install gives an execution layer and operator
surfaces, and the README says so explicitly (`README.md:19-21`). It has **no first-party consumer
that exercises the substrate claim** — the repository's own debt register records this as
`SUBSTRATE-WITNESS-1`, measured against the flagship app: `execute_tool`, `EffectRecord` and
`execution_token` appear **zero** times in that app's own source. This is load-bearing for §11.

---

## 3. Evidence and methodology

### 3.1 What was inspected

Everything in this report was derived by direct measurement against the working tree and `git`.
No figure is quoted from a document without independent verification, because the repository's own
governance rules warn repeatedly that documented claims here have historically outrun
implementation (`CLAUDE.md` §"Trusting a green check", nine catalogued instances).

Measurements taken:

- `git rev-list`, `git log --numstat`, `git shortlog`, `git for-each-ref` for history, churn,
  cadence, contributors and tags.
- A custom tokenizer pass (Python `tokenize`) over every tracked file for **logical SLOC**,
  bucketed by role — reported in §4.1. Physical line counts are reported alongside so both
  conventions are available.
- Live import of `AINDY.kernel.syscall_registry` to enumerate the *actual* registry contents rather
  than trusting string literals in source.
- `pytest --collect-only` to count real, collectible tests rather than counting `def test_`.
- Direct reads of `sandbox_escape_results.json`, `pyproject.toml`, all 9 CI workflow files, all 16
  Alembic revisions, all 30 model modules, and the largest 40 source modules.

### 3.2 Estimation method

Two independent methods, reconciled in §9:

1. **Bottom-up engineering decomposition** (§6) — 17 subsystems, each estimated across design,
   implementation, integration, test, documentation, security and operationalization, at three
   scenario levels. Coordination overhead is applied **once**, as an explicit separate multiplier,
   never folded into subsystem lines (double-counting guard).
2. **Two parametric cross-checks** (§9) — COCOMO II Post-Architecture calibrated with explicitly
   stated scale factors and effort multipliers, and a productivity-band analysis over total
   delivered artifact volume.

### 3.3 Exclusions

- **The `aindy-sdk`, `aindy-ui-kit`, `aindy-apps-monolith`, and 36 `nodus-*` sibling repositories
  are excluded *from §1–§15*.** Only work whose artifacts live in *this* repository is priced there.
  `@aindy/ui-kit` is consumed here as a published npm dependency and its source lives at
  `C:\dev\aindy-ui-kit`. **★ All of them are measured and priced separately in §16**, which reports
  the whole-product (Scope B) and constellation (Scope C) figures. Nothing in §16 revises §1–§15;
  the two answer different questions.
- **`nodus-lang` is excluded *from §1–§15*** — it is a pinned dependency at `nodus-lang==5.0.1`,
  released separately. The *integration* with it (2,700+ lines under `AINDY/runtime/nodus_*`) is
  counted there; the language is not. **★ It is measured and priced in §16.5**, where it turns out
  to be the largest single engineering artifact in the project (156,018 lines: lexer, compiler, VM,
  LSP, DAP, stdlib, package manager, 33 releases) — written by the same author in the same window,
  and therefore part of Scope C.
- **93 pinned third-party dependencies** (FastAPI, SQLAlchemy, Alembic, APScheduler, Redis,
  pgvector, OTel, Prometheus, OpenAI SDK…) are excluded from size and effort. Only the integration
  and the pin-management discipline are counted.
- The `build/`, `logs/`, `*.egg-info/` and `coverage-*.xml` artifacts present in the working tree
  are ignored as build output.
- **The predecessor repository `C:\dev\masterplan-infiniteweave-monday-node-2025-0411` is
  *included as history evidence* but *excluded from the size and cost base*.** Its `AINDY/`
  directory is the same 55,388 lines that arrived in this repository's commit 1 — counting it
  again would double-count. Its `apps/`, `tests/` and `alembic/` trees went elsewhere or were
  abandoned, and enter this report only as *rework* in §6.3, never as delivered artifact.

### 3.4 Working-tree state at audit time

The audit was performed against `HEAD` = `e9efcf7` plus five *uncommitted documentation
modifications* and one untracked file. These are documentation-only and do not affect any code
measurement:

```
 M CLAUDE.md                                 (   52 lines changed)
 M TECH_DEBT.md                              ( 2020 lines changed)
 M docs/runtime/ECOSYSTEM_CAPABILITY_GAPS.md (   46 lines changed)
 M docs/runtime/RUNTIME_MODULE_MAP.md        (   33 lines changed)
 M docs/runtime/WHAT_THE_RUNTIME_IS.md       (  107 lines changed)
?? docs/runtime/COMPARATIVE_RESEARCH_INDEX.md
```

Documentation line counts in §4.1 reflect the working tree (i.e. include these). Committed
`TECH_DEBT.md` is ~8,750 lines; the working-tree version is 10,770.

### 3.5 Verification attempted, and its limits

The `runtime_only` CI subset (what the required `Runtime Contracts` check runs) was launched
locally against SQLite on this Windows host. **It was stopped at ~57% completion after roughly 80
minutes and did not finish.** Up to that point: **exactly 1 failure and 2 skips across roughly
1,250 executed tests**, the failure occurring within the first ~150.

That failure is **not treated as evidence of a broken suite**: this host lacks Docker, Redis and
PostgreSQL, and `CLAUDE.md` documents that several suites behave differently off Linux/PostgreSQL.
It is reported because omitting it would be dishonest, and because the report elsewhere relies on
CI as the evidence of a green suite. **No green-suite claim is made from local execution, and none
of the maturity ratings in §5 depend on this run** — they rest on tracing behaviour into specific
named tests, which is the method §3.5's closing paragraph requires.

**CI evidence used instead:** `main` is protected with `enforce_admins: true` and **ten required
checks** (§5, row 15), `strict: true`, and 426 of 704 commits reference a merged PR number — so
every merged change passed those gates.

**Reader caution required by this repository's own rules:** a green check is not proof of coverage.
`CLAUDE.md` catalogues **nine** distinct ways this codebase has previously shipped something that
looked covered and was not. Maturity ratings in §5 therefore rest on tracing behaviour into tests,
not on job names.

---

## 4. Repository scale and history

### 4.1 Scale (measured 2026-08-19)

| Artifact bucket | Files | Physical lines | Logical SLOC |
|---|---:|---:|---:|
| AINDY runtime source (`.py`) | 328 | 82,556 | **60,749** |
| Documentation (`.md`) | 134 | 49,607 | 31,534 |
| Tests (`.py`) | 214 | 45,189 | 28,715 |
| Platform SPA (`.tsx/.jsx/.ts/.js/.css`) | 32 | 7,041 | 6,247 |
| CI / compose / monitoring (YAML) | 16 | 2,388 | 1,731 |
| Packaging & infra config (`toml/ini/conf/sql/…`) | 13 | 1,439 | 452 |
| Vendored shims (`AINDY/apscheduler`, `AINDY/nodus`) | 17 | 621 | 389 |
| Scripts (`.py`) | 3 | 420 | 296 |
| Alembic migrations (`.py`) | 17 | 1,239 | 286 |
| Other `.py` (root) | 1 | 431 | 270 |
| Native accelerator (Rust + C++) | 5 | 356 | 231 |
| Nodus scripts (`.nodus/.nd`) | 5 | 129 | 78 |
| **TOTAL** | **785** | **191,416** | **130,978** |

Derived ratios:

- **Test-to-source ratio: 0.47 logical SLOC of test per SLOC of runtime source** (28,715 / 60,749),
  or **0.55 by physical lines**. For infrastructure code this is a healthy, not padded, ratio.
- **Documentation-to-source ratio: 0.60 by physical lines** (49,607 / 82,556). Unusually high, and
  the documents are dense — contracts, invariant maps, decision records and measured findings, not
  API boilerplate.
- **Collected tests: 2,150 unit tests** (`pytest tests/unit --collect-only` → *"2150 tests collected
  in 33.52s"*), plus 13 integration modules, 6 sandbox-escape modules (17 tests), and 2 API modules.
  180 unit test files.

Other structural counts:

| Dimension | Count | Evidence |
|---|---:|---|
| Database tables | 36 | `__tablename__` across `AINDY/db/models/` + `AINDY/memory/memory_persistence.py` |
| Alembic revisions | 16 | `alembic/versions/0001…0016` |
| HTTP routes | 147 | `@router.<verb>` across `AINDY/routes/` |
| Scope-gated route dependencies | 41 call sites | `enforce_api_key_scope` in `AINDY/routes/` |
| Registered syscalls | 24 | live `SYSCALL_REGISTRY` enumeration |
| Extension registration hooks | 40 | `^def register_` in `platform_layer/registry.py` |
| Prometheus metric objects | 52 | `= Counter\|Histogram\|Gauge(` in `platform_layer/metrics.py` |
| Distinct `aindy_*` metric names | 72 | repo-wide grep |
| System event types | 46 | `AINDY/core/system_event_types.py` |
| Startup phases | 15 | `AINDY/startup.py` (1,890 lines) |
| `AINDY_*` env vars referenced in code | 173 | repo-wide grep |
| Pinned runtime dependencies | 93 | `pyproject.toml` |
| CI workflows | 9 | `.github/workflows/` (1,930 lines) |
| Deployment profiles | 4 | `platform_layer/deployment_contract.py` |
| Compose services | 8 | `docker-compose.yml` (postgres, api, redis, worker, mongo, prometheus, grafana, nginx) |
| **`TODO` / `FIXME` / `XXX` / `HACK` in `AINDY/`** | **0** | repo-wide grep — see note below |

> **The zero-TODO count is a genuine and unusual signal, and it is not cosmetic.** This repository
> does not carry inline debt markers because it carries them *externally*, in a 10,770-line
> `TECH_DEBT.md` with ~58 numbered entries, each recording diagnosis, measurement, corrections and
> reasoning. That is a deliberate and expensive discipline, and §10 argues it is the single most
> under-priced artifact in the repository.

### 4.2 Git history — this repository, and the predecessor it came from

| Fact | Value |
|---|---|
| First commit | `0d5d382`, **2026-05-17**, *"Initial runtime repo extraction"* |
| Latest commit audited | `e9efcf7`, 2026-08-17 |
| Total commits | **704** |
| Commits referencing a merged PR | **426** |
| Human author | Masterplanner25 (Shawn Knight) — 813 commits across all refs |
| Bot authors | `dependabot[bot]` 136, `github-actions[bot]` 1 |
| Calendar span | **92 days** |
| **Distinct days with a commit** | **61** |
| Cumulative churn | **+218,888 / −22,881 lines** |
| Release tags | **27**, `v1.0.0` (2026-06-06) → `v2.4.0` (2026-08-17) |

Commits by month: 2026-05 → 82; 2026-06 → 296; 2026-07 → 165; 2026-08 → 161.
Peak week: **2026-W23 (173 commits)**.

**This repository's first commit is not the project's first commit.** It is an **extraction**, and
it landed **357 files and 63,084 insertions at once**. Measured at that commit:

| At `0d5d382` (2026-05-17) | Lines |
|---|---:|
| `AINDY/**/*.py` | **55,388** |
| `tests/**/*.py` | 1,081 (13 files) |
| `*.md` | 4,968 (22 files) |

The sibling repository `C:\dev\aindy-apps-monolith` **begins on the same day** with
`7565ce9` *"Initial apps monolith repo extraction"*, carrying a further 48,808 Python lines.

### 4.2.1 The predecessor repository — located and measured

**The predecessor is `C:\dev\masterplan-infiniteweave-monday-node-2025-0411`, and it is a live git
repository.** It was supplied during this audit and inspected directly. This closes what would
otherwise have been the largest single uncertainty in the report.

| Fact | Value |
|---|---|
| First commit | `e741a52`, **2025-04-11**, *"Initial commit"* |
| Last commit | `328e4ef`, 2026-05-20 |
| Total commits | **549** |
| Sole author | Masterplanner25 (551 across all refs) |
| Calendar span | **405 days** |
| **Distinct active days** | **57** |
| Release tags | 0 |
| `AINDY/` at HEAD | **55,388 py lines / 291 files — byte-identical in size to this repo's commit 1** |

That last row is the decisive verification: **the runtime source now in `aindy-runtime` was
developed in the predecessor and moved across intact.** The extraction was a repository split, not
a rewrite.

**Predecessor content at HEAD** (excluding two `aindy-runtime/` and `aindy-apps-monolith/`
directories that are split-validation copies, to avoid double-counting):

| Path | Files | Python lines |
|---|---:|---:|
| `AINDY/` — the runtime | 291 | **55,388** |
| `tests/` | 288 | **60,441** |
| `apps/` | 313 | 37,533 |
| `alembic/` | 138 | 8,709 |
| `aindy-examples/` | 10 | 1,408 |
| **Total (deduplicated)** | **1,057** | **165,578** |
| Markdown | 178 files | 39,487 lines |

### 4.2.2 The real timeline: 16 months calendar, 5.4 months active

Measuring source growth in the predecessor at fixed dates (Python lines, split-copies excluded):

| Date | Python files | Python lines | Markdown files | Phase |
|---|---:|---:|---:|---|
| 2025-04-30 | 26 | 1,565 | 4 | Concept — RippleTrace MVP, memory events, *"The Day I Named the Agent"* |
| 2025-06-30 | 26 | 1,565 | 4 | *(dormant)* |
| 2025-10-22 | 122 | 8,456 | 11 | First real backend — *"unified A.I.N.D.Y. backend with research-engine integration, bridge v0.1"*, tagged `v0.9-pre` |
| 2025-11-30 | 143 | 9,998 | 12 | *"Full project update: backend fixes, folder restructuring"* |
| 2026-02-28 | 169 | 11,421 | 12 | *(largely dormant since November)* |
| 2026-03-20 | 228 | 26,942 | 42 | **Build begins** — Memory Bridge, ARM, security phases, C++ semantic engine |
| 2026-03-31 | 457 | 68,256 | 60 | |
| 2026-04-15 | 776 | 119,509 | 91 | Peak file count, pre-restructure |
| 2026-05-20 | 1,057 | 165,578 | 178 | Post test-extraction restructure; split complete |

**Dormancy analysis across all three repositories** (predecessor + runtime + monolith, distinct
days unioned):

```
Total calendar span   2025-04-11 → 2026-08-18 = 494 days (16.2 months)
Distinct active days  (any of the three repos)  =  121
Dormant stretches (>14 days with zero commits):
    53d  2025-04-17 → 2025-06-09
    20d  2025-06-09 → 2025-06-29
   111d  2025-06-29 → 2025-10-18
    33d  2025-10-21 → 2025-11-23
    86d  2025-11-23 → 2026-02-17
    28d  2026-02-17 → 2026-03-17
   ----
   331 dormant days
Non-dormant calendar span = 494 − 331 = 163 days ≈ 5.4 months
```

**Consequences for this audit, stated plainly:**

1. **Confidence in the original-creation estimate rises** from moderate-low to **moderate-high**.
   The pre-history is no longer inferred; it is measured.
2. **Calendar duration must not be read as effort.** The project spans 16.2 months, of which
   **331 days are dormant**. Actual engineering occupied **~5.4 months of non-dormant calendar
   across 121 distinct active days**, in three bursts: October–November 2025 (~5 weeks),
   March–May 2026 (~11 weeks), and May–August 2026 in this repository (~13 weeks).
   **★ "Dormant" describes the repositories, not the project** — §4.2.3 dates a public, published
   body of written work running through those gaps, including the design thesis and the system's
   public naming. No effort figure here is derived from calendar span, so this changes the
   interpretation and no number.
3. **The runtime source was built in roughly eleven weeks.** `AINDY/` went from 11,421 Python lines
   (2026-02-28) to 55,388 (2026-05-17). This is the phase the governance changelog's sprint
   headings describe, and it is now corroborated by commit-level measurement rather than by the
   document alone.
4. **A prior full backend was superseded.** The October 2025 `v0.9-pre` *"Research Engine
   Integration Build"* (8,456 lines) was largely replaced by the 2026 build. That is a discarded
   architecture, not an increment.
5. **★ 88% of the predecessor's test suite was abandoned at the split — the single largest measured
   instance of discarded work in the project.** The predecessor held **60,441 lines of tests across
   288 files**. Comparing file basenames: of **264** distinct predecessor test/fixture basenames,
   only **32 survive in either successor** (23 in `aindy-runtime`, of which 5 are fixtures rather
   than tests; 15 in `aindy-apps-monolith`). **232 have no successor at all.** Both extractions
   dropped the suite and rebuilt from near-zero — this repository carried across 1,081 lines and
   has since written 44,129.

> **This corrects a characterization in an earlier draft of this report.** The imported code was
> *not* "thinly tested" — it had been tested, extensively, in the predecessor. What happened is
> worse from a cost standpoint and better from a quality one: a working 60k-line suite was
> **abandoned**, and a new 45k-line suite was written against the extracted boundary. The
> engineering effort is real in both places; only one of the two survives as an artifact. §6.3
> prices this as rework.

### 4.2.3 The conceptual pre-history — a public, dated record

§4.2.2 measures the *code* history and reports 331 dormant days. **Those days are dormant in the
repositories, not in the project.** A public, timestamped body of written work runs through them,
and it is relevant to this audit for three reasons: it dates the design thesis, it names the system
before the system existed, and it is itself an executed strategy with a product implementation.

**Sources and their limits.** Two Medium RSS feeds (`medium.com/feed/masterplan-infinite-weave` and
`medium.com/feed/@masterplaninfiniteweave`) plus search-engine result summaries. **Medium returns
HTTP 403 to article fetches**, so roughly fifteen recent articles were read substantively and the
remainder only through third-party summaries; the ~80-article archive was not read. Article counts
in the bylines vary — "80+", "80–100+", and "38 articles in 9 days" (the last appears to describe
the opening burst, not the total). Reported as a range, not a figure.

#### The two series

| | |
|---|---|
| **2025 ChatGPT Case Study Series** | 80+ articles, *Masterplan Infinite Weave* publication on Medium, cross-posted to Substack, LinkedIn, Dev.to, X and Facebook. Stated origin: a response to a *New Scientist* piece arguing that overreliance on ChatGPT weakens thinking. Running thesis — ChatGPT as **cognitive multiplier**: iterative dialogue rather than one-off commands. Observed span March → November 2025 |
| **2025 ChatGPT/AI The Duality of Progress Series** | The strategic follow-up, with a *Master Index & Strategic Manifesto* (July 2025). Thesis: **"You do it alone. But never just for yourself."** Frames AI's paradoxes through implementation — strategy vs. presence, framework vs. freestyle, execution vs. expression |

#### Why it matters to the cost model

**1. The design thesis is dated, and it precedes the build.** Two Duality articles from late December
2025 — before the February–May 2026 phase that produced the runtime source — state the production
model that §17.3 independently re-derived from commit evidence eight months later:

> **"Why You Can't Just 'Prompt It'"** (2025-12-26) — the escalation *prompts → patterns →
> architecture → ecosystem*, and: *"true mastery isn't about writing clever prompts — it's about
> designing systems."*
>
> **"Trust Me, You Don't Know What I'm Talking About"** (2025-12-27) — *"prompts do not fix
> architectural behavior."*

**This matters because it rules out a post-hoc rationalisation.** §17.3 argues the production
function is `invent → specify → direct → verify` rather than prompt-and-review; that argument would
be considerably weaker if it were reconstructed only from a repository after the fact. It was stated
publicly, in advance, by the author.

**2. The system was named and framed publicly before most of it was written.** *"How Vibe Coding
Becomes an AI Teaching Framework (A.I.N.D.Y.)"* — LinkedIn **2025-12-22**, Medium repost 2026-03-05
— sits between the October–November 2025 `v0.9-pre` backend and the February 2026 build. Contemporary
descriptions of A.I.N.D.Y. as an MVP to *"run their lives like high-efficiency corporations,"*
powered by *"The Infinity Algorithm,"* match the loop this runtime implements as primitives
(`AINDY/core/system_event_types.py`: `RECALL_USED`, `SCORE_COMPUTED`, `NEXT_ACTION_CHOSEN`,
`NEXT_ACTION_DISPATCHED` — see `WHAT_THE_RUNTIME_IS.md` §1).

**3. Revised reading of the dormancy figure.** §4.2.2's *"331 dormant days"* is a statement about
five git repositories. It should be read as **the periods in which the work was conceptual and
written rather than committed**, not as idle time. This does not change any effort figure — none was
derived from calendar span — but it corrects the interpretation.

#### AI Search Optimization — framework, practice, and product

**The series are not only writing about the method; they are an execution of it.** The author's term
is **AI Search Optimization (AISO)**, framed as distinct from SEO: optimising to be *cited by a
model* rather than *ranked for a human*. Traditional SEO targets a keyword-matching crawler serving a
ranked list; AISO targets a synthesising system deciding what to reproduce. That implies different
tactics, and the series exhibits them:

- **Consistent entity naming** — nearly every title carries the series name as a literal prefix
  (`2025 ChatGPT Case Study Series: …`), which makes the series a retrievable entity rather than a
  loose set of posts, alongside a distinctive, collision-free brand string
- **Volume in bursts** — 38 articles in 9 days
- **Multi-surface publication** — five-plus platforms for the same corpus
- **A named framework per article** rather than undifferentiated commentary

**Claimed and partially corroborated outcome:** *"How AI Search Spreads Your Work"* documents
citations in financial outlets and academic papers **without direct outreach**, and a separate piece
(*"The AI Invest Article Mention"*) records one. *"The Day OpenAI's GM Tagged Me"* (2025-11-16)
records a mention by OpenAI's GM of Education. These are the author's accounts; this audit did not
independently verify the citing publications.

**★ What this audit can attest, because it happened during it:** every fact in this subsection was
located from a **single web query**, and the search engine's own summary spontaneously returned the
series' structure, article counts, cross-posting surfaces, origin story and thesis line — without
being asked for them. **The research step of this audit is itself an instance of the effect AISO
describes.** That is a narrow observation, not a measurement of reach, but it is first-hand.

**AISO also exists as shipped code, and it is among the oldest ideas in the project:**

| Layer | Artifact | Evidence |
|---|---|---|
| **Framework** | AISO as a stated method, distinct from SEO | *"AI Search Framework"* and *"How AI Search Spreads Your Work"* |
| **Practice** | The two series — 80+ articles as the deployment | §4.2.3 above |
| **Product** | `apps/rippletrace` — **5,952 lines**: drop points, `causal_engine`, `influence_graph`, `delta_engine`, `content_fetch`, ripple edges — i.e. instrumentation for how content propagates. Plus `apps/search` — **4,156 lines**: `seo_routes`, content analysis, meta generation, improvement suggestion, durable search, leadgen | `aindy-apps-monolith/apps/rippletrace/`, `apps/search/` |

**RippleTrace is commit 11 of the entire project.** `eb2a875` *"Create RippleTraceMVP"* and `f352947`
*"Create Rippletracerequirements"*, both **2025-04-15** — four days after the first commit, and
ahead of the runtime, the agent framework, the memory system and the language. The measurement layer
for AISO was among the first things conceived, and it still ships.

**How this is treated in the cost model.** `apps/rippletrace` and `apps/search` are already inside
Scope B's application line (§16.4) at implementation rates. The *method* — AISO as a framework, and
the 80+ article corpus that deploys it — is **excluded from every figure in this report**: it is
neither runtime engineering nor product code, and pricing content marketing as software would be a
category error. It is recorded here because it dates the thinking, and because §6.3b's invention term
would otherwise appear to arise from nowhere.


### 4.3 What the visible 92 days actually bought

Diffing `0d5d382 → HEAD` by area shows the visible window was **not** primarily feature
construction. It was verification, hardening and documentation of an imported codebase:

| Area | Insertions since extraction | Deletions | Interpretation |
|---|---:|---:|---|
| `tests/` | **+44,129** | −21 | The test suite is **essentially 100% post-extraction work.** At extraction there were 13 test files / 1,081 lines against 55,388 lines of source. |
| `docs/` + root `*.md` | **+44,343** | −582 | ~90% of the docset is post-extraction. |
| `AINDY/platform_layer/` | **+15,610** | −217 | The isolation / extension / certification / deployment-contract layer more than doubled (≈9k → 24.7k lines). |
| `AINDY/runtime/` | +3,481 | −855 | Nodus integration, flow engine split, warm pool. |
| `AINDY/core/` | +2,498 | −26 | Durable execution, continuation, rehydration, effect boundary. |
| `AINDY/routes/` | +2,285 | −333 | Scope gating, platform routers, auth surface. |
| `AINDY/agents/` | +2,244 | −103 | Capability tokens, coordinator, simulation, undo. |
| `AINDY/kernel/` | +2,233 | −417 | Idempotency gate, versioning, condition codes, scheduler split. |
| `AINDY/db/` | +1,530 | −194 | 15 of 16 migrations, schema contract, bootstrap CLI. |
| `AINDY/memory/` | +1,350 | −222 | Provider abstraction, MAS, cascade cleanup, native bridge loader. |
| `AINDY/worker/` | +105 | −33 | Largely unchanged since extraction. |

Commits touching each area (visible history): `tests/` 220, `docs/` 178, `platform_layer/` 70,
`.github/` 57, `runtime/` 54, `platform/` (SPA) 49, `kernel/` 48, `routes/` 45, `agents/` 40,
`core/` 37, `db/` 35, `memory/` 27, `alembic/` 18, `worker/` 7.

**This is a materially different cost profile from "someone wrote 130k lines in three months."**
The visible window is a *hardening, proving and re-testing* programme applied to an imported
system whose own test suite had been left behind at the split (§4.2.2, finding 5). That work is
expensive in a conventional organization — it is exactly the work that gets cut first and costs
most later — and it is priced accordingly in §6.

### 4.4 Development cadence — and why it matters for §8.5

In this repository: 704 commits over **61 active days** = **11.5 commits per active day**, with
**+218,888 insertions over 61 active days ≈ 3,590 inserted lines per active day, by one human**,
sustained for three months, while maintaining a 10-gate CI, a 27-release cadence, and a
10,000-line decision register.

Across the whole project (§4.2.2): **~165,000 Python lines plus 39,000 documentation lines built in
the predecessor over 57 active days**, then a further ~134,000 lines of source, tests and docs added
here over 61 active days — **121 distinct active days in total, all three repositories combined.**

**Observed fact, not inference:** that throughput is not achievable by an unassisted individual.
Combined with the repository's own tooling surface — `CLAUDE.md` (990 lines of agent instructions),
`CODEX.md`, `AINDY/llms.txt` + `llms-full.txt` shipped in the wheel, `examples/openclaw/AGENTS.md`,
`docs/platform/governance/AGENT_WORKING_RULES.md`, and a comparative-research programme against 17
external systems — the repository is unambiguous evidence of **heavy AI-assisted development**.
§8.5 models that counterfactual explicitly. It does not change the principal estimate, which per
the brief answers what a *conventional organization would have paid*.

---

## 5. Architectural and subsystem inventory

Maturity legend:
**PD** Production-demonstrated · **PO** Production-oriented, substantially implemented ·
**IIV** Implemented but incompletely validated · **EXP** Experimental · **STUB** Stubbed ·
**DOC** Documentation-only · **DEAD** Dead/unused

Person-months are **expected-case clean-room reproduction** (low/high in §6). They exclude
coordination overhead, which is applied once in §6.2.

| # | Subsystem | Verified capabilities | Key evidence | Maturity | Complexity drivers | PM |
|---|---|---|---|---|---|---:|
| 1 | **Kernel / syscall contract** | 24 syscalls; capability enforcement; tenant isolation; schema in+out validation; idempotency gate; uniform error envelope; OTel span per dispatch; syscall versioning (`v1`/`v2` coexisting); condition codes; circuit breaker; resource quotas (4 dims); cross-instance wait registry | `kernel/syscall_dispatcher.py` (975), `syscall_registry.py` (2,028), `resource_manager.py` (995), `event_bus.py` (623), `syscall_versioning.py` (250), `condition_codes.py` (288), `scheduler/` (~1,000). Tests: `test_syscall_dispatch_contract.py` (22), `test_syscall_contract.py` (15), `test_syscall_execution_guarantee.py` (10), `test_security_isolation.py` (15) | **PO** | Single chokepoint for all authority; every invariant is a security invariant; `SyscallContractViolation` must escape a deliberately broad handler; envelope shape is a public compatibility surface | **7.0** |
| 2 | **Execution pipeline & core services** | `ExecutionUnit` claim/release with 6-state machine; execution gate; distributed Redis queue w/ DLQ + delayed jobs + stale requeue + saturation + circuit breaker + in-memory fallback; retry policy; route execution guard; rehydration; next-action dispatch; verifier | `core/distributed_queue.py` (1,236), `execution_dispatcher.py` (584), `execution_gate.py` (572), `execution_unit_service.py` (478), `flow_run_rehydration.py` (404), `execution_pipeline/pipeline.py` (371), `retry_policy.py` (265). Tests: `test_eu_lifecycle_invariants.py`, `test_rehydration_paths.py`, `test_retry_policy_contract.py`, `tests/integration/test_redis_queue.py` | **PO** | Distributed-systems correctness; crash-recovery paths; a fallback that silently degrades durability class (`QUEUE-DURABILITY-CLASS-1`); shared-session transaction hazards documented at measured cost (`RT-MEMTXN-LEAK-1`) | **9.0** |
| 3 | **Sandbox & extension isolation** | Docker-backed sandbox runner; JSON-RPC process runner; tier selection; launch attestation; OCI runtime identity classification; platform capability matrix (3 OS families); certification profile; extension signing + provenance; **17 escape tests, 6 vectors, all PASS**; append-only audit log (17 dated entries) | `platform_layer/sandbox_runner.py` (2,437), `sandbox_certification.py` (920), `extension_worker.py` (1,299), `plugin_host.py` (1,185), `extension_provenance.py` (503), `extension_abi.py` (288). `tests/sandbox/` (17 tests), `sandbox_escape_results.json`, `docs/runtime/SANDBOX_ESCAPE_AUDIT.md`; workflows `sandbox-escape-linux.yml`, `macos-sandbox.yml` | **PD** (Linux) / **IIV** (non-Linux) | Adversarial security work; cross-OS behaviour differences; attestation semantics; **C3 open**: both supported-platform tuples are `(PLATFORM_LINUX,)`, so non-Linux reaches container- but not strong-sandbox certification | **8.0** |
| 4 | **Plugin registry / extension ABI** | 40 `register_*` hooks; registry contracts; node registry; subprocess callback host + worker with a boundary sanitizer; stateful in-process exemptions; per-callback timeout budget; capability-definition providers with per-provider caching | `platform_layer/registry.py` (2,117), `registry_contracts.py` (540), `node_registry.py` (812), `runtime_callback_host.py`, `runtime_callback_worker.py`. Tests: `test_extension_abi.py`, `test_extension_boundary_contract.py`, `test_extension_ownership.py`, `test_runtime_callback_budget.py`, `test_capability_provider_cache.py` | **PO** | Running third-party code across a process boundary; cwd resolves to read-only site-packages in a wheel install (documented hazard); silent-failure collapse paths | **5.0** |
| 5 | **Flow engine + Nodus DSL integration** | DAG executor with node-level `FlowHistory` commit before snapshot advance; WAIT/RESUME propagation from guest script to host flow; deferred memory writes; execution budget; warm worker pool; scheduled Nodus jobs with per-job misfire policy (Alembic `0013`); plan compiler; flow registry | `runtime/nodus_execution_service.py` (1,516), `nodus_adapter.py` (1,053), `flow_engine/` (1,760 across 11 files), `nodus_worker.py` (583), `nodus_worker_pool.py` (383), `nodus_schedule_service.py` (593), `agent_plan_compiler.py` (362). Tests: `test_nodus_*` (11 files), `test_flow_continuation.py`, `tests/integration/test_ecogap6_flow_continuation.py` | **PO** | Embedding a foreign VM; exception-as-control-flow (`WorkerWaitSignal`); guest confinement (`test_guest_confinement.py`, 7 tests, mutation-tested); shim/namespace shadowing hazards | **8.0** |
| 6 | **Agent runtime** | goal → plan → approval → execute; atomic CAS on approve; HMAC capability tokens w/ key ring, refresh, ceiling; tool registry + policy; multi-agent coordinator + message bus; planner backends; autonomous controller + window; stuck-run watchdog; dead-letter; `sys.v1.agent.simulate` (virtual tools, zero side effects); `sys.v1.agent.undo` (compensating reversal) | `agents/capability_service.py` (790), `agent_coordinator.py` (632), `stuck_run_service.py` (435), `agent_runtime/execution.py` (424), `tool_registry.py` (413), `capability_policy.py` (260), `agent_runtime/approvals.py` (149). Tests: `test_capability_token_integrity.py` (8), `test_agent_simulate.py`, `test_capability_governance.py`, `test_delegation_hardening.py`, `test_agent_approve_watchdog.py` | **PO** | Authority modelling; a CAS-then-background-thread window requiring a 10-minute orphan watchdog; token lifetime bound to clock not run lifecycle (`AUTHORITY-LIFETIME-1`, open) | **7.0** |
| 7 | **Memory subsystem** | Addressable MAS with tenant path validation; hybrid recall (vector + tags + path); scoring (impact/usage/causal depth); capture engine with rules; async ingest queue + worker; pluggable embedding providers + dimension config + re-embed CLI; cascade cleanup; memory traces + link syscall; **Rust cdylib + C++ semantic kernel** with parity-pinned Python fallback | `memory/` (4,144 across 16 files), `db/dao/memory_node_dao.py` (1,844), `memory/native/memory_bridge_rs/` (Rust 265 + C++ 70). Tests: `test_memory_native_scorer.py` (58), `test_memory_address_space.py`, `test_memory_delete_syscall.py`, `test_memory_link_syscall.py`, `test_cascade_cleanup.py`, `test_embedding_providers.py`. CI: `Native Crate Build (Rust)` required check | **PO**; native path **IIV** (built in CI, no published benchmark) | pgvector + FFI + cross-language parity; N+1 and connection-holding hazards fixed at measured cost (login 43.6s → 0.3s); `expand()`'s semantic-neighbour half is **DEAD** (`MEM-EXPAND-DEAD-1`) | **8.0** |
| 8 | **Persistence, schema contract & migrations** | 36 tables; 16 idempotent Alembic revisions with blank-DB table-existence guards; separate `alembic_version_runtime`; packaged head constant + `bootstrap-schema` CLI with branchable exit codes (3/4/5); content-hashed schema contract + baseline; runtime/app table-ownership split; DAO layer | `db/models/` (30 modules, 1,810 lines), `alembic/versions/0001…0016` (1,239 lines), `db/schema_contract.py` (674), `db/alembic_head.py`, `runtime_only.py:_bootstrap_schema`. Tests: `test_runtime_schema_contract.py` (18), `test_runtime_alembic_head.py`, `test_bootstrap_schema_exit_codes.py`, `tests/integration/test_schema_contract.py`. CI: `Upgrade Path Guard` **with a negative control** | **PO**; upgrade-against-existing-DB **IIV** (`FR-14` open) | Two-repo table ownership; the wheel cannot read the scripts dir; ordering constraints between guard/DDL/DML on blank databases | **5.0** |
| 9 | **API layer, auth & authorization** | 147 routes / 24 routers; JWT + platform API keys + service keys; signing key ring w/ rotation; bcrypt; email verification + password reset over signed single-use tokens (Alembic `0014`); scope enforcement at **41 call sites**; admin bootstrap (grant-only; first-user-admin explicitly forbidden); owner-scoped agents (Alembic `0016`); SPA static mount with asset-404 discrimination | `routes/` (6,955), `services/auth_service.py` (874), `routing.py`. Tests: `test_jwt_scope_enforcement.py`, `test_key_scope_escalation.py`, `test_password_reset_flow.py`, `test_email_verification.py`, `test_user_owned_agents.py` (30), `test_coordination_agent_scopes.py`, `test_route_guard_unmanaged_routes.py` | **PO** | Two privilege-escalation classes found and fixed (`KEY-SCOPE-ESCALATION-1`, #463/#465); scope coverage went 7 → 91 gated routes; a registration-enumeration oracle remains open **by decision**, blocked on FR-6 | **6.0** |
| 10 | **Scheduler, leadership, recovery & workers** | Priority-lane scheduler with dedicated executors; lease-based leadership election; orphan-approved-run recovery; wait-firing on its own job + executor; stale-log and effect-record TTL cleanup; worker processes (memory ingest, metric writer) + health server | `platform_layer/scheduler_service.py` (942), `leadership.py` (358), `recovery_jobs.py` (284), `kernel/scheduler/` (~1,000), `worker/worker_loop.py` (897), `worker/health_server.py` (221). Tests: `test_scheduler_executor_lanes.py` (10), `test_scheduler_wait_decoupling.py` (8), `test_background_leadership.py`, `test_worker_loop.py` (20) | **PO** | ~33 jobs on a default pool of 10 (fixed by isolation, not capacity); the lease has **no fencing token** (`LEASE-FENCE-1`, open); a vendored APScheduler shim silently shadows the real package under `pytest` — now guarded by `test_apscheduler_shim_parity.py` | **5.0** |
| 11 | **Observability & eventing** | 52 Prometheus metric objects / 72 metric names; OTel spans + OTLP exporter; 46 typed system events with a causal `event_edges` graph; event trace service; 11 health/readiness endpoints incl. `/health/deep`, `/health/domains`, `/health/sandbox`; degraded-mode matrix; request-metric writer; Prometheus + Grafana in compose | `platform_layer/metrics.py` (383), `otel.py` (98), `event_service.py` (712), `event_trace_service.py` (291), `core/system_event_service.py` (628), `system_event_types.py`, `routes/health_router.py` (659), `routes/observability_router.py` (565), `monitoring/prometheus.yml`. Tests: `test_system_event_contract.py` (frozen-hash baseline), `test_event_bus.py`, `tests/integration/test_event_bus_wire.py` | **PO** | A frozen-hash contract baseline on event types; the pub/sub wire had never been exercised end-to-end until `EVENTBUS-COVERAGE-1`; naming aligns with no GenAI semantic convention (`OTEL-GENAI-SEMCONV-1`, open) | **4.5** |
| 12 | **Platform SPA (operator console)** | 12 operator consoles (agent console, approval inbox, agent registry, execution console, flow engine console, health dashboard, observability dashboard, ripple-trace viewer, admin users, login, not-admin, shell); route guard; error boundaries; toast/empty/loading primitives; Vite + Tailwind build emitting into `AINDY/platform/dist` and riding in the wheel as package data | `platform/src/` (32 files, 7,041 lines), `platform/vite.config.ts`, `PlatformApp.tsx`. CI: `Platform UI Build` required check; `platform-lockfile.yml` resolver workflow | **PO** | Guard invariants that produce redirect loops if violated; a Windows-generated lockfile that cannot satisfy Linux `npm ci` (`LOCKFILE-PLATFORM-1`, open, has a dedicated workflow); UI reaches containers only via a release + Dockerfile pin bump | **3.5** |
| 13 | **Connectors, egress, secrets, LLM providers, MCP** | `register_connector` + `authorized_external_call`; egress guard (socket-level allow-list); secret broker with Env/File/Vault/Chain backends + capability-scoped resolution + fail-closed; OpenAI/Anthropic/Azure/DeepSeek clients with a fallback chain; MCP client (call external tools) + stdio MCP server (expose syscalls) | `external_call_service.py` (257), `egress_guard.py`, `secret_broker.py` (325), `openai_client.py` (319), `llm_client.py` (275), `mcp_client.py` (178), `mcp_server.py` (261). Tests: `test_secret_broker.py` (20), `test_egress_guard.py`, `test_connector_registry.py`, `test_llm_provider_fallback.py`, `test_mcp_client.py`, `test_mcp_client_live.py`, `test_mcp_server.py`, `test_outbound_http.py` | **PO**; egress guard **IIV** (off by default; its docstring names its own two bypasses) | Monkey-patching sockets at process level; fail-closed semantics; an MCP SDK version wall (`MCP-SDK-2X-1`, open) that currently blocks a first-party dependency upgrade | **5.0** |
| 14 | **Test & quality infrastructure** | 2,150 collected unit tests across 180 files; 13 integration modules on live PG+Redis; 17 sandbox escape tests; frozen contract baselines; fixtures; an auto-marker conftest so a new unit file cannot run in no job; mutation-checked suites with **liveness controls**; a test that fails when the debt register contradicts itself | `tests/` (214 files, 45,189 lines), `tests/unit/conftest.py`, `tests/baselines/`, `test_ci_marker_default.py`, `test_debt_registry_accuracy.py`, `test_apscheduler_shim_parity.py`, `test_dependency_pin_agreement.py`, `test_packaging_contents.py` | **PO** | Essentially all built post-extraction against 55k lines of untested imported code; the auto-marker and liveness-control mechanisms are second-order quality engineering, not test-writing | **11.0** |
| 15 | **CI/CD, packaging & deployment** | 9 workflows; **10 required checks with `strict: true` and `enforce_admins: true`**; SHA-pinned actions; OIDC trusted publishing to TestPyPI + PyPI with a manual production gate; wheel + sdist with package-data traps closed; install smoke; boot smoke against real PostgreSQL from the published wheel; upgrade-path guard **with a negative control**; 2-stage Dockerfile; 3 compose overlays; nginx plain + TLS; Prometheus/Grafana | `.github/workflows/` (9 files, 1,930 lines), `Dockerfile` (90), `docker-compose{,.prod,.test}.yml`, `nginx/*.conf`, `MANIFEST.in`, `pyproject.toml`, `scripts/` | **PD** | The release protocol spans three pin sites that must move together; a `paths:` filter on a required check blocks merges forever; a `push`-triggered new workflow does not run on its own PR | **5.0** |
| 16 | **Documentation & governance** | 134 markdown files / 49,607 lines: architecture, execution invariants, security matrix, idempotency contract, connector contract, SDK/UI contracts, extension trust model, stability index, deployment profiles, degraded-mode matrix, incident classification, operator runbook, release gates + checklist, 5 versioned app handoffs, 3 tutorials, a 3,884-line CHANGELOG, a 10,770-line debt register, an append-only escape audit, a comparative-research index over 17 external systems | `docs/` (30,962 lines) + root (18,591 lines). CI: `Runtime Docs Validation` enforces 5-key frontmatter **and** a real `last_verified >= 2026-05-17` | **PO** | Enforced-in-CI frontmatter; documents that record *corrections to themselves* with dates; a docs-coverage claim failure (`DOCS-COVERAGE-CLAIM-1`: 6 docs cited 8 test files that never existed) found and closed | **7.0** |
| 18 | **Architectural invention & comparative R&D** *(creation only — a reproducing team inherits this)* | The three-way fusion of OS syscall semantics, workflow durability and agent authority at one chokepoint; a **19-system** source-verified comparative survey with per-system accuracy checks | `docs/runtime/COMPARATIVE_RESEARCH_INDEX.md` (19 systems, each pinned to a commit, each with an `ACCURACY_CHECK_vs_aindy-runtime_2.4.0.md`); the mechanisms themselves in rows 1, 2 and 6 | **PO** | Priced at 50 PM in §6.3b. Scored across six concerns the survey holds this runtime at **4 of 6, with no comparand better than 2**; Temporal — the only pure-substrate peer — at **55–65%** coverage. **Novelty rests on a first-party survey whose method is auditable, not an independent market review** | **50.0** |
| 17 | **Cross-cutting architecture & technical leadership** | The syscall contract shape; the tiered isolation model; the mediated-effect-boundary program; the durable-execution program; the runtime/app ownership split; the stability index; the recorded-and-declined decisions | `docs/runtime/FOUNDATIONAL_PATTERN.md`, `ISOLATION_MODEL_PLAN.md`, `MEDIATED_EFFECT_BOUNDARY_PROGRAM.md`, `DURABLE_EXECUTION_PROGRAM.md`, `RUNTIME_BOUNDARY.md`, `RUNTIME_STABILITY_INDEX.md`, `TECH_DEBT.md` §*Recorded decisions — considered and declined* | **PO** | Not reducible to lines; §10 argues this is the least reproducible part of the artifact | **4.0** |

### 5.1 Explicitly *not* credited

Traced and found to be less than the documentation implies. These are **subtracted** from the
maturity-adjusted value in §11 — and note that they are all findings the repository itself records,
which is separately evidence of the discipline being priced:

| Item | State | Evidence |
|---|---|---|
| `expand()` semantic-neighbour recall | **DEAD** — returns `[]` on every call and always has | `MEM-EXPAND-DEAD-1`; pgvector 0.4.2 returns `ndarray`, the guard tests `isinstance(list)` |
| Boot-time route AST proof | **DEAD** — exists, has no call site in the app; by its own test it would raise on a route that works today | `ROUTE-AST-UNWIRED-1` |
| Async heavy-execution routing (`{flow, agent, nodus, job}` × priority) | **Dead code by default** — all 8 combinations return `INLINE` with the flag unset | `FR-15`; `AINDY_ASYNC_HEAVY_EXECUTION` default false |
| `EXACTLY_ONCE` idempotency gate | **Built, shipped disabled** — 7 syscalls declare it; the flag is off | `IDEM-11`; `AINDY_SYSCALL_IDEMPOTENCY` |
| ECOGAP-4 G4a policy guards | **Built but INERT** — every guard vacuous until a policy is registered | `ECOGAP-*` |
| `sys.v1.memory.delete` | **No consumer** — nothing calls it | `MEM-DELETE-1` |
| `undo_run_effects` double-reversal | **Latent** — not live only because **zero compensators are registered** | `IDEM-12` |
| Non-Linux strong sandbox | **Not implemented** — both supported-platform tuples are `(PLATFORM_LINUX,)` | `C3` |
| Cloud / k8s deployment | **Absent** — **zero** k8s/Helm/Terraform files in the repository | measured; `DEPLOY-TARGET-1` |
| Load / performance testing | **Absent** — **zero** latency assertions; no locust/k6/benchmark harness anywhere in `tests/` or `.github/` | measured; `PERF-BASELINE-1` |
| Token / cost metering | **Absent** — `prompt_tokens\|completion_tokens\|total_tokens` hits only a context sizer; an LLM runtime with no token meter | `COST-GOVERNOR-1` |
| Backup / restore / DR procedure | **Absent** — the 422-line operator runbook is triage-only; no data-recovery drill | measured |
| `mypy` baseline | **Absent** | `PACK-DEBT-3` |
| Tool-seam isolation | **Open P0** — `tool_registry.py:366` runs foreign code in-process with the live DB session and ambient authority | `TOOL-SEAM-ISOLATION-1` |
| Default-off capability flags | **20 `=false` defaults** in `AINDY/.env.example`; ≥8 registry items have "soak then flip" as their only remaining work | measured |
| First-party substrate consumer | **None** — the flagship app integrates in 334 lines, mostly HTTP, and never touches `execute_tool` / `EffectRecord` / `execution_token` | `SUBSTRATE-WITNESS-1` |

### 5.2 Explicitly credited despite small line count

Per the brief, low LOC is not a discount when reasoning, integration and verification dominate:

- **`AINDY/kernel/tenant_context.py` (178 lines)** — a frozen dataclass whose shallow-freeze bug
  (`TENANT-FROZEN-SHALLOW-1`) meant `capability_scope` was mutable across a security boundary. The
  fix is one line; finding it is not.
- **`AINDY/db/alembic_head.py` (58 lines)** — a single constant, CI-enforced, that exists because
  the `alembic/` scripts directory is not shipped in the wheel. Pure integration reasoning.
- **`AINDY/kernel/clock.py` (46 lines)** — an injectable clock, without which none of the
  wait/resume/lease/misfire tests are deterministic.
- **`AINDY/memory/native_bridge.py` (127 lines)** — a single search policy for the native crate.
  Trivial-looking; `NATIVE-DISCOVERY-1` records that `sys.path.insert` in priority order puts the
  lowest-priority path first, which let a stale debug build shadow a fresh release one.
- **The 3-kwarg guest-confinement fix (`GUEST-CONFINE-1`)** — three keyword arguments. Before it, a
  guest script created a file on the host, read the real `PATH`, and did real DNS — *demonstrated,
  not inferred*. Priced at the cost of finding and proving it, not at 3 lines.

---

## 6. Engineering effort estimate (bottom-up)

### 6.1 Subsystem person-months, three scenarios

Each estimate covers the full engineering lifecycle for that subsystem: architecture and design,
implementation, integration, tests and debugging, documentation, security and reliability work,
operationalization, and in-team review. **Cross-subsystem coordination, program management and
release overhead are deliberately excluded here** and applied once in §6.2.

Scenario definitions per the brief:
- **Low** — unusually strong, cohesive senior team; architecture already understood; minimal friction.
- **Expected** — competent professional team under normal conditions.
- **High** — enterprise environment with onboarding, formal review, security sign-off, coordination
  and integration overhead *inside* each work item.

| # | Subsystem | Low | Expected | High |
|---|---|---:|---:|---:|
| 1 | Kernel / syscall contract | 4.5 | 7.0 | 11.0 |
| 2 | Execution pipeline & core services | 6.0 | 9.0 | 14.0 |
| 3 | Sandbox & extension isolation | 5.0 | 8.0 | 13.0 |
| 4 | Plugin registry / extension ABI | 3.0 | 5.0 | 8.0 |
| 5 | Flow engine + Nodus integration | 5.0 | 8.0 | 12.0 |
| 6 | Agent runtime | 4.5 | 7.0 | 11.0 |
| 7 | Memory subsystem (incl. native crate) | 5.0 | 8.0 | 12.0 |
| 8 | Persistence, schema contract & migrations | 3.0 | 5.0 | 8.0 |
| 9 | API layer, auth & authorization | 4.0 | 6.0 | 9.0 |
| 10 | Scheduler, leadership, recovery & workers | 3.0 | 5.0 | 8.0 |
| 11 | Observability & eventing | 3.0 | 4.5 | 7.0 |
| 12 | Platform SPA | 2.0 | 3.5 | 6.0 |
| 13 | Connectors / egress / secrets / LLM / MCP | 3.0 | 5.0 | 8.0 |
| 14 | Test & quality infrastructure | 7.0 | 11.0 | 17.0 |
| 15 | CI/CD, packaging & deployment | 3.0 | 5.0 | 8.0 |
| 16 | Documentation & governance | 4.0 | 7.0 | 11.0 |
| 17 | Cross-cutting architecture & tech leadership | 2.5 | 4.0 | 7.0 |
| | **Subtotal (engineering)** | **67.5** | **108.0** | **170.0** |

> **Item 18 of §5 — architectural invention & comparative R&D — is deliberately absent from this
> table.** A clean-room team is handed the design; pricing its derivation here would charge
> reproduction for something reproduction inherits free. It enters in §6.3b as an additive term in
> the creation estimate only.

### 6.2 Coordination, integration and program overhead — applied once

| Scenario | Rate | Rationale | Overhead PM | Total PM |
|---|---:|---|---:|---:|
| Low | +10% | 5–6 colocated seniors, no formal process tax | 6.8 | **74.3** |
| Expected | +20% | ~10-person cross-functional org; standups, design review, release management, cross-team dependencies | 21.6 | **129.6** |
| High | +35% | Enterprise: onboarding, architecture review board, security sign-off, compliance, multi-team integration | 59.5 | **229.5** |

**Bottom-up clean-room reproduction: 74 / 130 / 230 PM.**

Two of the three independent cross-checks in §9 land *above* the bottom-up expected. Rather than
ignore that, the reported expected figure is adjusted upward to the reconciled value:

**Reported clean-room reproduction: 78 / 140 / 240 PM = 6.5 / 11.7 / 20.0 PY** (reconciliation
arithmetic in §9.4).

### 6.3 From reproduction to original creation — rework and invention, priced separately

Clean-room reproduction assumes the hard questions are answered. Original creation must pay for two
distinct things, and an earlier revision of this report collapsed them into a single opaque
multiplier:

1. **Rework** — the cost of being wrong: dead ends, built-and-not-wired mechanisms, post-ship
   security findings, abandoned work.
2. **Invention** — the cost of arriving at a design that did not previously exist.

**These are not the same expenditure and they do not scale together.** The multiplier used
previously (×1.95) was justified in its own write-up entirely by rework evidence; it never priced
invention at all. §6.1 item 17 gave *"cross-cutting architecture & technical leadership"* 4.0 PM —
which prices the *coordination* of a known design, not the derivation of an unknown one. The
composition below corrects that. The formula is now explicit:

```
original creation = (reproduction × rework-uplift) + invention
```

#### 6.3a Rework — documented, and largely measured

Each of these is an entry in `TECH_DEBT.md`, the governance changelog, or §4.2.2's measurement of
the predecessor:

| Class | Instances |
|---|---|
| Built and shipped **not wired** | `ROUTE-AST-UNWIRED-1` (route proof never called), `FR-15` (async routing dead by default), `IDEM-10` (gate dead in production), `ECOGAP-4 G4a` (guards inert) |
| **Green but not covered** — nine catalogued variants | `DOCS-COVERAGE-CLAIM-1` (6 docs cited 8 nonexistent test files), `CI-MARKER-1` (268 tests in 24 files ran in no job), `NATIVE-CI-1` (native suite skipped; nothing built the crate), `EVENTBUS-COVERAGE-1` (pub/sub wire never exercised; first-draft suite mutation-scored **4/7**), `ROUTE-GUARD-1` (500 instead of 409 for a full day), branch protection (6 checks non-blocking) |
| **Security holes found after shipping** | `GUEST-CONFINE-1` (guest VM unconfined — host file created, real `PATH` read, real DNS performed), `KEY-SCOPE-ESCALATION-1` (a `flow.read`-only key could mint itself `platform.admin` **and** rotate the platform signing key), `HTTP-SCOPE-GAP-1` (7 of 126 routes gated → 91) |
| **Performance defects found by measurement** | `RT-MEMTXN-LEAK-1` (login 43.6s → 0.3s; 60 held connections → 0), `CAPABILITY-PROVIDER-TIMEOUT-1` (10 lookups = 10 subprocess spawns / 56.4s → 1 / 11.4s), `MEM-RECALL-N1-1` |
| **Dead code / duplication found** | `MEM-EXPAND-DEAD-1`, `MAS-FLATTEN-1`, `KERNEL-INIT-DUPLICATE-1` (byte-identical duplicate module → two `TenantContext` classes, `isinstance` silently `False`) |
| **Abandoned in the predecessor** *(measured, §4.2.2)* | **60,441 lines of tests across 288 files, of which 232 of 264 basenames (88%) have no successor in either repo.** Plus the October 2025 `v0.9-pre` *"Research Engine Integration Build"* backend (8,456 lines), superseded by the 2026 rebuild. Plus the `cxx` crate, dropped for direct `extern "C"` FFI |
| **Investigation waste** | `FLAKY-1` produced **three** wrong diagnoses before the fourth run refuted each |

| Scenario | Rework uplift | Basis |
|---|---:|---|
| Low | ×1.35 | Aggressive senior team; spec converges fast |
| Expected | ×1.65 | Matches the documented density of first-attempt-wrong findings and the measured abandonment |
| High | ×1.85 | Enterprise friction compounding with discovery |

*The expected multiplier is lower than the ×1.95 used previously because the portion it was
implicitly carrying for invention is now an explicit additive term below — not because the rework
evidence weakened.*

#### 6.3b Invention — the synthesis that did not previously exist

**This is a new line item.** It was absent from earlier revisions of this report, which is a defect:
it priced the execution of a design and left the derivation of that design at 4 PM.

**The claim.** `aindy-runtime` fuses three normally-separate traditions at a single chokepoint:

| Tradition | What it contributes here | Verified in source |
|---|---|---|
| **Operating systems** | A syscall namespace (`sys.v1.domain.action`) with a dispatcher enforcing capabilities, tenant isolation, resource quotas and condition codes; an `ExecutionUnit` as a first-class, quota-bounded unit of work | `kernel/syscall_dispatcher.py`, `syscall_registry.py`, `resource_manager.py` (4 quota dimensions), `condition_codes.py`, `tenant_context.py`, `db/models/execution_unit.py` |
| **Workflow / durable execution** | Durable suspend-and-resume as a first-class lifecycle state, cross-instance rehydration, an effect ledger with an at-most-once gate, history fold | `kernel/scheduler/waits.py`, `core/flow_run_rehydration.py`, `kernel/effect_ledger.py`, `db/models/effect_record.py`, Alembic `0003`/`0008`/`0011` |
| **Agent systems** | Approval that *mints* an HMAC capability token bound to run, user and plan, with a ceiling and narrowing-only delegation | `agents/capability_service.py:mint_token`, `capability_policy.py` |

**The synthesis is the artifact, not the parts.** A capability token (agent) is checked at a syscall
dispatcher (OS), which runs an idempotency gate against an effect ledger (workflow), inside an
execution unit under quota (OS), which can suspend durably and resume on another instance
(workflow). No one of those three traditions produces that chokepoint on its own.

**Evidence that it is unprecedented — and the limits of that evidence.**
`docs/runtime/COMPARATIVE_RESEARCH_INDEX.md` records a systematic survey of **nineteen** external
systems, each pinned to a commit and each carrying an `ACCURACY_CHECK_vs_aindy-runtime_2.4.0.md`
recording which of its claims survived verification against source. Its headline result:

> *"Every serious agent system grows, buys, or hand-rolls part of a runtime. None of them assembles
> the whole set — and they each grow a **different** part, chosen by whichever hurt first."*

Scored across six concerns — durability, scheduling, authority, isolation, effects, cost governance
— **this runtime holds four; no comparand scores better than two.** Codex built ~72,000 LOC of
three-OS sandboxing and has no durable execution. LangGraph built durability and has no authority or
tenancy. MAF *bought* durability from Azure Durable Task. Temporal — the only comparand that is
purely substrate, and therefore the one coverage number not inflated by app-hosted content — sits at
**55–65%**.

**What this audit verified, and what it did not.** I verified that the mechanisms exist in source
(§5), and that the survey's method is disciplined: it separates `[Observed]` from `[Inferred]`, it
marks findings that were later corrected, and it explicitly refuses the adoption inference —
*"Licensed: the category is real… Not licensed: and therefore they will adopt ours."* **I did not
independently re-run nineteen comparisons against the wild.** So novelty here rests on a
first-party survey whose method is auditable, not on an independent market review. That is
materially weaker than source verification and materially stronger than assertion.

**Pricing.** In a conventional organization this is an applied-research function, not a delivery
function: a small group of principals exploring, prototyping and discarding over months, plus the
comparative survey itself (nineteen systems read at source level and written up).

| Component | Low | Expected | High |
|---|---:|---:|---:|
| Architectural synthesis — deriving the three-way fusion and its chokepoint contract | 12.0 | 34.0 | 65.0 |
| Comparative R&D — 19 systems, source-verified, with accuracy checks | 6.0 | 16.0 | 30.0 |
| **Invention subtotal (creation only — excluded from reproduction)** | **18.0** | **50.0** | **95.0** |

**★ What this term covers — and what it does not.** The 50 PM prices **one** invention: the
syscall-chokepoint synthesis described above. At least two others in this estate were classed as
implementation, and a reader should see the boundary rather than infer it:

| Candidate | How this report priced it | Status |
|---|---|---|
| **The runtime as an execution substrate** — OS + workflow + agent fused at one chokepoint | The 34 PM synthesis term above | **Counted** |
| **Nodus built as a language rather than a library** | §16.5 gives 6/12/22 PM for *"syntax, semantics, the gating model"* | **Under-counted.** That prices designing a language once the decision is made; it does not price the decision. And the decision has consequences: guest confinement works because the deny-flags sit at a *language runtime* boundary (`allow_subprocess/network/env=False`, `GATED_BUILTINS` exposed in 5.0.1, and `NodusRuntime.__init__` carrying no `**kwargs` so a renamed flag raises rather than silently unconfining). A Python library cannot confine Python from inside Python. `GUEST-CONFINE-1` is closeable because a VM boundary exists to close it at |
| **The memory-bridge architecture** — memory as *continuity and authorship* rather than storage: addressable (`/memory/{tenant}/{namespace}/{type}/{id}`), scored on impact/usage/causal depth, and joined to execution via `memory_context_ids` / `output_memory_ids` so a single row answers *what did this run know going in and produce going out* | §6.1 item 7, as **implementation** (pgvector, scoring, DAO) | **Not counted.** The repository itself separates the framing from the implementation — `CONTRIBUTORS.md` credits Cherokee Schill for the former and claims neither the latter nor the capability system. This report collapsed a distinction the source already drew |

**Where the design thesis came from, and when.** §4.2.3 records a public, dated body of written
work running from March 2025, including a *Master Index & Strategic Manifesto* (July 2025) and the
system's public naming as A.I.N.D.Y. on **2025-12-22** — between the `v0.9-pre` backend and the
February 2026 build. The invention term above is not priced from that corpus (it is content, not
engineering, and is excluded from every figure in this report), but the corpus establishes that the
architectural position **predates the code that expresses it**, which is the ordering the term
assumes.

**Why this is a limit of the instrument, not an oversight to be patched.** Effort models price
activities with durations. Invention is not one — it is a discontinuity. What *can* be priced is the
search surrounding it: prototyping, comparing, discarding, and recording why an approach was
declined (the four-way replay taxonomy, `HOOK-PRECEDENCE-1`, `DISPATCH-ADMISSION-1`). That search is
what the 50 PM buys. The arrival is not in it and cannot be.

**The residual therefore has a known sign.** Every invention classed here as implementation makes
the estimate *low*, never high. A second guessed invention term would be larger than the first
without being better evidenced, so none is added — but the reader should treat 50 PM as a **floor on
one line item**, not as coverage of the estate's invention.

**This term is deliberately absent from clean-room reproduction.** A reproducing team is handed the
answer; that is the entire premise of §10. Invention is precisely the delta, and stating it as an
additive term rather than folding it into a multiplier is what makes that delta auditable.

**It is also the term conventional cost models cannot see.** COCOMO's `PREC` factor prices
*unfamiliarity with a system type*, not the research to discover one; productivity bands price lines
produced, and invention produces none. Both cross-checks in §9 are structurally blind here, which is
a reason to trust the bottom-up over them on this line specifically.

#### 6.3c Result

```
Low       78 × 1.35 + 18 = 105.3 + 18 = 123 PM
Expected 140 × 1.65 + 50 = 231.0 + 50 = 281 PM
High     240 × 1.85 + 95 = 444.0 + 95 = 539 PM
```

**Original creation: 123 / 281 / 539 PM = 10.3 / 23.4 / 44.9 PY.**

*Net effect of the restructure: the expected figure moved from 270 to 281 PM (+4%). The composition
changed far more than the total — which is the point. An estimate whose headline is stable under a
methodology correction is more trustworthy than one that swings, but only if the correction is
visible, which is why both the old and new forms are shown.*

### 6.4 Production gap work (used in §11)

| Gap (evidence in §5.1) | Low | Expected | High |
|---|---:|---:|---:|
| Load/performance harness + published baselines (`PERF-BASELINE-1`) | 2.0 | 3.0 | 5.0 |
| Soak programme + flag flips for ≥8 default-off capabilities, with rollback | 2.5 | 4.0 | 7.0 |
| Upgrade path exercised against a real existing database (`FR-14` remainder) | 1.0 | 1.5 | 2.5 |
| Backup / restore / DR procedure + a rehearsed drill | 1.5 | 2.5 | 4.0 |
| Tool-seam isolation (`TOOL-SEAM-ISOLATION-1`, open **P0**) | 2.0 | 3.0 | 5.0 |
| Token/cost meter + governor (`COST-GOVERNOR-1`) | 1.5 | 2.5 | 4.0 |
| Cloud deployment manifests (k8s/Helm) — currently **zero files** | 2.0 | 3.0 | 5.0 |
| Non-Linux strong sandbox (`C3`) | 1.5 | 2.5 | 4.5 |
| External security review / pen test cycle + remediation | 1.5 | 2.5 | 4.0 |
| `mypy` baseline + type debt paydown | 0.5 | 1.0 | 2.0 |
| External developer usability (public docs, quickstart hardening, support process) | 1.0 | 2.0 | 3.5 |
| **Total gap work** | **17.0** | **27.5** | **46.5** |

*Deliberately excluded from this table* (documented as decisions, not gaps): multi-tenant SaaS
readiness (`DEPLOY-TARGET-2`, triggered by a first multi-tenant operator), billing
(`BILLING-1..5`, deferred to commercial launch), and the registration-enumeration oracle (open by
decision as a dependent of FR-6). Including them would price a product roadmap, not a replacement.

---

## 7. Conventional team reconstruction

### 7.1 Model A — small senior team

**Composition (5.5 FTE steady-state):**

| Role | FTE | When needed |
|---|---:|---|
| Principal architect / tech lead | 1.0 | Throughout. Owns the syscall contract, isolation model and ownership split. Not substitutable. |
| Senior runtime / distributed-systems engineer | 1.5 | Throughout. Kernel, execution pipeline, queue, scheduler, leadership, rehydration. |
| AI/agent-systems engineer | 1.0 | From month 3. Agent runtime, capability tokens, planner backends, memory scoring, LLM providers, MCP. |
| Security / platform engineer | 0.75 | Month 4 onward, peaking during the sandbox programme. |
| DevOps / SRE | 0.5 | Month 2 onward; heavier at each release. |
| Frontend engineer | 0.5 | Months 6–14 only. |
| Technical writer / DX | 0.25 | Month 8 onward. |

**Role overlap is assumed and is why this is 5.5 and not 11:** the architect writes kernel code; the
distributed-systems engineers own their own tests (there is no separate QA role); the DevOps
engineer owns packaging and release; the writer edits rather than authors, with engineers drafting.
**There is no dedicated database engineer** — the persistence work here is schema-contract and
migration discipline, not query optimization, and it is absorbed by the runtime engineers.
**There is no product manager** — the specification is the repository.

| | Value |
|---|---|
| Effort (original creation, expected) | 281 PM |
| Calendar | **281 / 5.5 ≈ 51 months** |
| Coordination overhead | ~10% (already in §6.2 Low) |
| **Major bottlenecks** | (1) The architect is a hard serialization point — the syscall contract, isolation model and ownership split must exist before four other subsystems can start. (2) Only ~2.5 people can work on the kernel/execution core at once without conflicting. (3) The security programme cannot be parallelized meaningfully; escape testing is inherently sequential against one sandbox design. |
| **Verdict** | Feasible and cheapest per unit of output, but **51 months is commercially unacceptable** for most organizations. This model is realistic for *reproduction* (140 PM / 5.5 ≈ 25 months), not for original creation. |

### 7.2 Model B — conventional cross-functional organization

**Composition (10.5 FTE at peak; ramping):**

| Role | FTE | When needed |
|---|---:|---|
| Principal architect / tech lead | 1.0 | Month 1 → end |
| Senior runtime engineer | 2.0 | Month 1 → end |
| Distributed-systems engineer | 1.0 | Month 2 → end (queue, leases, cross-instance resume, rehydration) |
| AI/agent-systems engineer | 1.0 | Month 3 → end |
| Security / platform engineer | 1.0 | Month 4 → end |
| Database engineer | 0.5 | Months 2–10 (schema contract, migration discipline, pgvector, ownership split) |
| DevOps / SRE | 1.0 | Month 2 → end |
| Frontend engineer | 1.0 | Months 6–18 |
| Test / quality engineer | 1.0 | Month 4 → end |
| Technical writer / DX | 0.5 | Month 6 → end |
| Product / program manager | 0.5 | Month 1 → end |

| | Value |
|---|---|
| Effort (original creation, expected) | 281 PM |
| Average staffing over the project | ~9.5 FTE (ramp-adjusted from a 10.5 peak) |
| Calendar | **281 / 9.5 ≈ 30 months** |
| Coordination overhead | ~20%, already applied in §6.2 Expected |
| **Major bottlenecks** | (1) **Sequential dependency chain:** syscall contract → execution pipeline → flow engine → agent runtime. Four subsystems cannot start in month 1 regardless of headcount. (2) **The isolation programme gates the extension platform** and cannot be compressed by adding people. (3) **The test infrastructure is on the critical path for every release gate**, not a trailing activity — 220 of 704 commits touch `tests/`. (4) Brooks's-law exposure: adding engineers past ~11 raises coordination faster than throughput on a codebase this invariant-dense. |
| **Verdict** | **This is the realistic conventional model.** ~10 people, ~30 months, ~$6.6M. It sets the headline. |

### 7.3 Why not a larger team

Attempting this with 20 engineers would not halve the calendar. The kernel is a single chokepoint
that every other subsystem calls through; `AINDY/db/database.py` has a fan-in of 88 modules and
`config.py` 40 (`docs/runtime/ARCHITECTURE_RISK.md`). That document identifies five modules where
"mistakes affect extension execution isolation claims" or "silence liveness." Parallelism there buys
rework, not throughput.

---

## 8. Cost calculation

### 8.1 Compensation assumptions — stated explicitly

**These are labeled assumptions, not market data.** No live compensation source was consulted.
They reflect US-market senior infrastructure engineering in 2026.

**Fully loaded cost** = base salary + bonus/equity amortization + employer payroll tax + benefits +
allocated management + recruiting/onboarding amortization + equipment + software licenses +
allocated facilities. The multiplier applied is **1.6× base**, which is conventional for US
software organizations.

| Role | Assumed base | Fully loaded (×1.6) |
|---|---:|---:|
| Principal architect / tech lead | $212k | **$340k** |
| Senior runtime / distributed-systems engineer | $181k | **$290k** |
| Security / platform engineer | $175k | **$280k** |
| AI / agent-systems engineer | $169k | **$270k** |
| Senior backend engineer | $156k | **$250k** |
| Database engineer | $156k | **$250k** |
| DevOps / SRE | $156k | **$250k** |
| Product / program manager | $144k | **$230k** |
| Frontend engineer | $138k | **$220k** |
| Test / quality engineer | $125k | **$200k** |
| Technical writer / DX | $113k | **$180k** |

**Blended rate derivation — Model B (the principal model):**

```
1.0×340 + 2.0×290 + 1.0×290 + 1.0×270 + 1.0×280 + 0.5×250
      + 1.0×250 + 1.0×220 + 1.0×200 + 0.5×180 + 0.5×230
  = 340 + 580 + 290 + 270 + 280 + 125 + 250 + 220 + 200 + 90 + 115
  = $2,760k per year across 10.5 FTE
  = $262.9k per engineer-year
```

Model A blends to $279k/engineer-year (fewer junior roles to dilute). **The audit uses $265,000
per engineer-year** as the blended fully loaded rate, which sits between the two models.

**Note on role-appropriate pricing:** this mix is deliberately senior-weighted. Pricing every role
at a single junior-inclusive rate would be wrong for this artifact — the kernel, isolation and
capability work is principal-level, and the evidence (a 2,437-line sandbox runner with attestation
semantics; an HMAC capability system with a key ring; a distributed queue with a durability-class
fallback) does not admit a cheaper role mix.

### 8.2 Non-labor costs

Kept separate from labor, and separate from recurring operations (§12). These are **build-phase**
costs only.

| Item | Low | Expected | High |
|---|---:|---:|---:|
| CI compute (9 workflows, 10 required checks, Docker-based escape suite, multi-OS) | $15k | $40k | $90k |
| Development & staging infra (PostgreSQL + Redis + Mongo + Prometheus/Grafana, multiple environments) | $20k | $55k | $130k |
| LLM / embedding API spend during development and testing | $10k | $35k | $90k |
| Tooling & licenses (IDEs, security scanning, artifact hosting) | $10k | $25k | $60k |
| Security review / pen test (production-equivalent scenario only) | — | $80k | $180k |
| **Build-phase non-labor (reproduction)** | **$55k** | **$155k** | **$370k** |
| **Build-phase non-labor (production-equivalent)** | **$120k** | **$300k** | **$650k** |

For original creation the non-labor figures scale with the longer calendar (a 28-month build burns
more CI and infrastructure than a 17-month one): **$105k / $420k / $1,050k**.

### 8.3 The arithmetic, shown

Formula:

```
estimated cost = (person-months ÷ 12) × blended fully loaded annual cost + non-labor
```

**1. Clean-room reproduction** — 78 / 140 / 240 PM

```
Low       78 ÷ 12 =  6.50 PY × $265,000 = $ 1,722,500 + $   55,000 = $ 1,777,500  →  $1.8M
Expected 140 ÷ 12 = 11.67 PY × $265,000 = $ 3,091,667 + $  155,000 = $ 3,246,667  →  $3.2M
High     240 ÷ 12 = 20.00 PY × $265,000 = $ 5,300,000 + $  370,000 = $ 5,670,000  →  $5.5M
```

**2. Original creation** — 123 / 281 / 539 PM *(= reproduction × rework-uplift + invention, §6.3c)*

```
Low      123 ÷ 12 = 10.25 PY × $265,000 = $ 2,716,250 + $  105,000 = $ 2,821,250  →  $2.8M
Expected 281 ÷ 12 = 23.42 PY × $265,000 = $ 6,205,417 + $  420,000 = $ 6,625,417  →  $6.6M
High     539 ÷ 12 = 44.92 PY × $265,000 = $11,902,917 + $1,050,000 = $12,952,917  → $13.0M
```

**3. Production-equivalent replacement** — reproduction plus gaps: (78+17) / (140+27.5) / (240+46.5)
→ 95 / 168 / 287 PM. Rounded **up** to **105 / 190 / 320 PM** to absorb the cost of landing gap work
into an existing codebase rather than building it fresh.

```
Low      105 ÷ 12 =  8.75 PY × $265,000 = $ 2,318,750 + $  120,000 = $ 2,438,750  →  $2.4M
Expected 190 ÷ 12 = 15.83 PY × $265,000 = $ 4,195,833 + $  300,000 = $ 4,495,833  →  $4.5M
High     320 ÷ 12 = 26.67 PY × $265,000 = $ 7,066,667 + $  650,000 = $ 7,716,667  →  $7.7M
```

**4. Current artifact replacement value** — 90 / 160 / 275 PM (derivation in §10.3)

```
Low       90 ÷ 12 =  7.50 PY × $265,000 = $ 1,987,500 + $   60,000 = $ 2,047,500  →  $2.0M
Expected 160 ÷ 12 = 13.33 PY × $265,000 = $ 3,533,333 + $  170,000 = $ 3,703,333  →  $3.6M
High     275 ÷ 12 = 22.92 PY × $265,000 = $ 6,072,917 + $  400,000 = $ 6,472,917  →  $6.3M
```

Figures are rounded to two significant figures; the point estimates carry no more precision than
that.

### 8.4 Sensitivity across fully loaded annual rates

Labor cost only (non-labor excluded, so the rate effect is visible undiluted):

| Estimate (expected PY) | @$150k | @$200k | @$250k | **@$265k (used)** | @$300k |
|---|---:|---:|---:|---:|---:|
| Clean-room reproduction — 11.7 PY | $1.75M | $2.33M | $2.92M | **$3.09M** | $3.50M |
| Original creation — 23.4 PY | $3.51M | $4.68M | $5.85M | **$6.21M** | $7.03M |
| Production-equivalent — 15.8 PY | $2.38M | $3.17M | $3.96M | **$4.20M** | $4.75M |
| Artifact replacement value — 13.3 PY | $2.00M | $2.67M | $3.33M | **$3.53M** | $4.00M |

Full grid for the headline figure (original creation, labor only):

| | @$150k | @$200k | @$250k | @$265k | @$300k |
|---|---:|---:|---:|---:|---:|
| Low (10.3 PY) | $1.54M | $2.05M | $2.56M | $2.72M | $3.08M |
| **Expected (23.4 PY)** | $3.51M | $4.68M | $5.85M | **$6.21M** | $7.03M |
| High (44.9 PY) | $6.74M | $8.98M | $11.23M | $11.90M | $13.47M |

**Reading this table honestly:** the rate assumption moves the answer by a factor of two across the
plausible range. A reader who believes $200k fully loaded is right for their geography should read
the headline as **$4.7M**, not $6.6M. The *effort* estimate (23.4 person-years) is the durable
finding; the dollar figure is a function of it and of a labeled assumption.

### 8.5 The AI-assisted counterfactual

The brief requires two counterfactuals. §8.3 gives the first (conventional, no coding agents) and it
remains the **principal estimate**, because the question asked is what a conventional organization
would have paid.

**Counterfactual 2 — an AI-assisted professional team using modern coding agents.**

AI-generated code is not free. The costs that remain:

- **Human architectural direction.** The syscall contract shape, the isolation tiers, the ownership
  split, the decision to *decline* kernel deterministic replay — none of these are agent output.
  This is the 4.0 PM of §6.1 item 17 and much of items 1–3, and it is essentially unaccelerated.
- **Review and validation.** This repository's own record is the strongest available evidence that
  the review burden is real: a first-draft test suite that mutation-scored **4/7**; six documents
  citing eight test files that never existed; a route AST proof with no call site. Every one of
  those is a plausible-looking artifact that passed casual inspection.
- **Debugging and integration**, which agents do not reliably shorten in a codebase with a fan-in of
  88 on a single module.
- **Agent supervision** — a real, non-zero human cost per unit of agent output.
- **Model / API expenditure.**

**Where acceleration is genuine:** test authoring, documentation, CRUD routes, frontend consoles,
config and CI scaffolding — collectively ~35% of the effort, plausibly 2–3× faster.
**Where it is not:** kernel invariants, distributed correctness, security isolation, cross-language
parity — ~65% of the effort, realistically 1.0–1.3× and occasionally *negative* (recent studies of
experienced engineers working in mature codebases have measured slowdowns).

```
blended throughput factor = 1 ÷ (0.35/2.5 + 0.65/1.15)
                          = 1 ÷ (0.140 + 0.565)
                          = 1 ÷ 0.705
                          = 1.42×
```

| | Conventional | AI-assisted (×0.70 effort) |
|---|---:|---:|
| Original creation, expected | 281 PM / 23.4 PY | **197 PM / 16.4 PY** |
| Labor @ $265k | $6.21M | **$4.35M** |
| Model / API spend (team of ~6, ~2 years, heavy agent use) | — | **$150k–$300k** |
| Non-labor (other) | $420k | $350k |
| **Total** | **$6.6M** | **≈ $4.9M** |
| Plausible range (0.55×–0.85× effort factor) | — | **$3.4M – $5.5M** |

**No claim is made about actual historical token expenditure.** The repository contains no billing
evidence and none is inferred. The ×0.70 factor is a model, labeled as such.

---

## 9. Independent cross-check

### 9.1 COCOMO II Post-Architecture

**Size input.** Product source only, excluding tests (COCOMO's nominal rates already include test
effort), documentation, and vendored shims:

```
AINDY runtime source   60,749
platform SPA            6,247
alembic                   286
scripts                   296
native (Rust/C++)         231
other .py                 270
                      -------
                       68,079 logical SLOC = 68.1 KSLOC
```

**Effort equation:** `PM = A × Size^E × ∏EM`, with `A = 2.94`, `E = 0.91 + 0.01 × ΣSF`.

**Scale factors** (original-creation flavor, conventional team):

| SF | Rating | Value | Justification from the repository |
|---|---|---:|---|
| PREC — precedentedness | Low | 4.96 | An agent execution substrate with a syscall contract and mediated effect boundary is not a precedented system type; the comparative-research programme over 17 external systems exists precisely because no template was available |
| FLEX — development flexibility | High | 2.03 | Single owner, no external contract or fixed requirement set |
| RESL — architecture / risk resolution | High | 2.83 | `ARCHITECTURE_RISK.md`, `RELEASE_GATES.md`, `EXECUTION_INVARIANTS.md`, `ISOLATION_MODEL_PLAN.md` all exist and are maintained |
| TEAM — team cohesion | High | 2.19 | Rated for the *conventional team* counterfactual, not the actual single author (who would be Extra High = 0.00) |
| PMAT — process maturity | Nominal (≈CMMI L2) | 4.68 | Strong CI gates, contracts and checklists, but no formal process certification. Conservative; High (3.12) is arguable |
| **ΣSF** | | **16.69** | `E = 0.91 + 0.1669 = 1.0769` |

**Effort multipliers:**

| EM | Rating | Value | Justification |
|---|---|---:|---|
| RELY | High | 1.10 | Durable execution, idempotency, capability enforcement |
| DATA | Nominal | 1.00 | 36 tables; moderate DB-to-code ratio |
| CPLX | Very High | 1.34 | Kernel dispatch, distributed coordination, container sandboxing, FFI, embedded DSL |
| RUSE | High | 1.07 | Published stability index, plugin ABI, SDK contract, PyPI distribution (Very High = 1.15 arguable; conservative choice taken) |
| DOCU | Very High | 1.13 | 49,607 lines of documentation is far above lifecycle-nominal |
| TIME / STOR | Nominal | 1.00 / 1.00 | No hard real-time or memory constraint |
| PVOL — platform volatility | High | 1.15 | 93 pinned deps, 136 dependabot commits in 92 days, first-party `nodus-lang` major bumps requiring three-site coordination |
| ACAP | High | 0.85 | |
| PCAP | High | 0.88 | |
| PCON | Nominal | 1.00 | Rated for a conventional team with normal attrition |
| APEX | Nominal | 1.00 | |
| PLEX | High | 0.91 | |
| LTEX | High | 0.91 | |
| TOOL | High | 0.90 | |
| SITE | High | 0.93 | Colocated or well-integrated remote |
| SCED | Nominal | 1.00 | No schedule compression |
| **∏EM** | | **≈ 1.06** | |

**Result:**

```
Size^E = 68.1^1.0769 ;  ln(68.1) = 4.2210 ;  × 1.0769 = 4.5461 ;  e^4.5461 = 94.28
PM     = 2.94 × 94.28 × 1.06
       = 293.8 person-months  ≈  24.5 person-years
```

**COCOMO II, original creation: ~294 PM.** Bottom-up expected: **281 PM**. **Agreement within 5%.**

**COCOMO II, clean-room reproduction** (PREC → High 2.48, RESL → Very High 1.41; ΣSF = 12.79,
E = 1.0379):

```
Size^E = e^(4.2210 × 1.0379) = e^4.3810 = 79.90
PM     = 2.94 × 79.90 × 1.06 = 249.0 person-months
```

**COCOMO II, reproduction: ~249 PM.** Bottom-up: **130 PM**. **COCOMO is 1.9× higher.**

### 9.2 Why COCOMO fits the creation estimate and overstates the reproduction estimate

- **It fits creation** because creation is what COCOMO was calibrated against: build a novel system
  from requirements, with documentation, under review. The 11% agreement is genuine corroboration.
- **It overstates reproduction** for three specific reasons:
  1. **COCOMO has no input for "a complete, executable behavioral specification already exists."**
     PREC and RESL are proxies for *precedentedness* and *risk resolution*, not for *having the
     answer*. A team handed 134 design documents, 2,150 tests and a working reference implementation
     is in a position the model does not represent.
  2. **Calibration era.** The COCOMO II dataset predates the modern package ecosystem. Roughly 93
     pinned dependencies do work a 1990s project would have built. COCOMO counts only new code —
     correctly — but its *productivity constant* was fitted in an era where less could be assembled.
  3. **DOCU = 1.13 prices documentation as pure effort.** For this repository a meaningful fraction
     of the docset is generated-then-edited (§8.5), which COCOMO cannot express.
- **What COCOMO gets right that the bottom-up nearly missed:** the diseconomy of scale. With
  `E = 1.077 > 1`, effort grows faster than size. The bottom-up decomposition is implicitly linear
  within each subsystem and would under-price a system this large. This is the main reason the
  reported expected reproduction figure was adjusted upward from 130 to 140 PM.

### 9.3 Productivity-band cross-check

Total delivered artifacts (physical lines): 82,556 source + 45,189 tests + 7,041 frontend +
4,066 config/CI/migrations + 49,607 documentation = **188,459 lines**.

| Band | Lines/PM | Implied PM | Fits? |
|---|---:|---:|---|
| Slow enterprise infrastructure | 900 | 209 | Plausible for the High scenario |
| **Typical professional platform team** | **1,200** | **157** | **Best fit for reproduction** |
| Fast senior team, spec known | 1,800 | 105 | Plausible for the Low scenario |

**Productivity band, reproduction: ~105 / 157 / 209 PM.**

### 9.4 Reconciliation

| Method | Reproduction (PM) | Creation (PM) |
|---|---:|---:|
| Bottom-up decomposition (§6) | 130 | 281 |
| Productivity band (§9.3) | 157 | — |
| COCOMO II (§9.1) | 249 | 294 |
| **Weighted** (bottom-up 0.50, band 0.30, COCOMO 0.20) | **142** | **284** |

Weights favor the bottom-up because, per the brief, a LOC-based model must not override stronger
architectural evidence without justification — and the justification here runs the other way: the
one thing COCOMO contributes that the bottom-up lacks is the super-linear scale exponent, which is
worth a 20% weight and not more.

**Reported figures round the weighted results:** reproduction **140 PM**, creation **281 PM**. The
low and high bounds are the bottom-up scenario bounds, widened on the high side to accommodate
COCOMO's 249/294.

---

## 10. Creation versus reproduction

Reproduction is **50% of creation cost** (140 / 281). Half the original cost bought
information rather than code. That information is now in the repository, and this section identifies
where.

### 10.1 What a reproducing team gets for free

| Resolved question | Where it now lives | What it cost to answer |
|---|---|---|
| Where the authority chokepoint goes and what it must enforce | `kernel/syscall_dispatcher.py` + `SYSCALL_REFERENCE.md` + `EXECUTION_INVARIANTS.md` | The whole `IDEM-10` arc: a gate that was **dead in production** while every test was green |
| What "durable" means here — and that it is *not* deterministic replay | `DURABLE_EXECUTION_PROGRAM.md`; the `ECOGAP-1` Phase 3 reframe; the three-way replay taxonomy in `TECH_DEBT.md` | Six external audits cited "replay" meaning three different things. The taxonomy exists because that confusion was paid for |
| That approval must *mint* authority rather than merely permit | `capability_service.py:mint_token`; `RTR-4` | The `AUTHORITY-VALUE-1` finding: the chokepoint reads a value the calling frame supplied, so it is not an independent second gate |
| That the guest VM must be confined with explicit kwargs | `GUEST-CONFINE-1`, `test_guest_confinement.py` | A **demonstrated escape** — host file created, real `PATH` read, real DNS performed |
| Which hook semantics to use — and which to refuse | `HOOK-PRECEDENCE-1` (declined), `DISPATCH-ADMISSION-1` (declined) | Full design cycles that produced **zero code** and are therefore invisible to any LOC-based estimate |
| That a `paths:` filter on a required check blocks merges forever | `NATIVE-CI-1` | Learned the expensive way |
| That `pytest.mark.integration` silently skips Docker-only tests | `CLAUDE.md` §*pytest.mark.integration* | All 17 escape tests silently skipped on first run |
| That the schema baseline is a **content hash** of the model file | `db/schema_contract.py` + `scripts/check_schema_version.py` | Repeated CI failures until the protocol was written down |
| **Nine distinct ways a green check can lie** | `CLAUDE.md` §*Trusting a green check* | Nine separate incidents, catalogued with the entry that recorded each |

### 10.2 The accumulated-decisions artifact

`TECH_DEBT.md` is **10,770 lines** with ~58 numbered entries carrying diagnosis, measurement,
correction and reasoning. The `CLAUDE.md` prefix registry is an index over it. Together with
`DECISION_LOG.md`, `OPEN_QUESTIONS.md`, the append-only `SANDBOX_ESCAPE_AUDIT.md`, the 3,884-line
`CHANGELOG.md`, and the comparative-research index over 17 external systems, this is a
**decision-provenance corpus**.

Two properties make it disproportionately valuable, and both are verifiable:

1. **It records corrections to itself, with dates.** `CLAUDE.md` contains lines like *"Corrected
   2026-08-14: this said the chain ended at `0010` — four migrations stale, in the section you read
   before writing a new one."* A document that records its own drift is a document you can trust
   about the parts it has not corrected.
2. **It records what was measured, not what was assumed.** *"10 lookups = 10 spawns / 56.4s before,
   1 / 11.4s after."* *"Login 43.6s → 0.3s, 60 held connections → 0."* *"A first-draft wire suite
   scored 4/7."* Numbers, with the method attached.

**This corpus cannot be reconstructed after the fact.** The repository states the reason itself, and
it is measurably true: reasoning is not in a commit subject. *"unique per owner"* survives a squash;
*"a plain `UNIQUE (owner_user_id, name)` would not be equivalent, because SQL treats NULLs as
distinct"* does not. A clean-room team reading this repository inherits the corpus; a team
*replacing* the artifact must produce an equivalent one, and can only do so by re-running the
experiments.

That is the entire basis for artifact replacement value exceeding clean-room reproduction cost.

### 10.3 Deriving artifact replacement value

```
Clean-room reproduction (§6.2)                                 78 / 140 / 240 PM
+ re-earning the decision corpus that the spec conveys
  but does not derive (§10.2)                                 +14 /  24 /  42 PM
− maturity haircut on inert/dead surface (§5.1: ~5% of
  source is dead, inert, or has no consumer)                   −2 /  −4 /  −7 PM
                                                              --------------------
Current artifact replacement value                             90 / 160 / 275 PM
```

The haircut is deliberately small. The dead surface is real (`expand()` always returns `[]`; the
route AST proof has no call site; ≥8 registry items are default-off pending a soak that
`SUBSTRATE-WITNESS-1` says cannot currently be run) — but it is **~5% of source**, and an
organization replacing this artifact would still build most of it, because the flags are off for
soak reasons rather than because the code is wrong.

---

## 11. Production-readiness adjustment

### 11.1 Capability maturity classification

| Class | Capabilities | Share of source (est.) |
|---|---|---:|
| **Production-demonstrated** | Linux extension sandbox (17 escape tests, 6 vectors, all PASS, 17 dated release-gate audit entries); packaging + release pipeline (27 tags, OIDC trusted publishing, boot smoke against real PostgreSQL from the published wheel) | ~12% |
| **Production-oriented, substantially implemented** | Syscall contract; execution pipeline + `ExecutionUnit`; flow engine + WAIT/RESUME + rehydration; agent runtime + capability tokens; memory + MAS + pgvector; persistence + schema contract + 16 migrations; auth/authz (91 gated routes); scheduler + leadership + recovery; observability; plugin registry; platform SPA | ~68% |
| **Implemented but incompletely validated** | Idempotency gate (`EXACTLY_ONCE`, flag off); durable continuation (flag off); async heavy execution (flag off); child-context clamp (flag off); delegation-scoped memory (flag off); Nodus warm pool (flag off); egress guard (off, self-documented bypasses); native scorer (built in CI, **no published benchmark against the Python path**); upgrade-against-existing-DB path | ~13% |
| **Experimental** | MCP client + stdio server (opt-in `[mcp]` extra); local embedding provider (`embeddings-local` extra); `sys.v2.memory.read` versioning demonstration | ~3% |
| **Stubbed / thin by design** | `AINDY/watcher/` (38 lines — constants only; the client lives in `aindy-sdk`); vendored `apscheduler` shim (215 lines, guarded by a source-derived parity test) | ~0.5% |
| **Documentation-only** | Cloud deployment targets (`DEPLOYMENT_TARGETS.md`, **zero** k8s/Helm/Terraform files); billing architecture (`MONETIZATION_AUDIT.md`, `BILLING-1..5` deferred); C3 non-Linux strong sandbox (plan document only) | ~0% of source |
| **Dead / apparently unused** | `expand()` semantic-neighbour half (returns `[]`, always has); boot-time route AST proof (no call site); async heavy-execution routing (dead with the default flag); `sys.v1.memory.delete` (no consumer); `undo_run_effects` double-reversal path (latent: zero compensators registered) | ~3.5% |

### 11.2 The honest framing

**This is not a prototype.** Prototypes do not have 27 releases, 10 required merge checks with
`enforce_admins: true`, an append-only security audit log with one entry per release gate, a
1,046-line predecessor changelog, or a schema-contract protocol enforced in CI.

**Nor is it a supported production system.** The evidence against that is specific and internal:

1. **`SUBSTRATE-WITNESS-1`** — no first-party consumer exercises the substrate claim. The flagship
   application integrates in **334 lines across 3 files**, all optional, mostly HTTP, and
   `execute_tool` / `EffectRecord` / `execution_token` appear **zero** times in its own source.
2. **`PERF-BASELINE-1`** — **zero latency assertions** across `tests/`. Every `duration_ms`
   reference is a type or shape check.
3. **The soak deadlock.** Eight registry items have "soak then flip" as their only remaining work.
   Soak requires production traffic through the path being flipped. Finding 1 says no such traffic
   exists. **The flag backlog is blocked on the absence of a consumer, not on courage or effort** —
   and no amount of engineering inside this repository resolves it.
4. **No disaster recovery.** The 422-line operator runbook is triage-only. No backup, restore or
   recovery-drill procedure exists.
5. **`TOOL-SEAM-ISOLATION-1` is open at P0.** With `GUEST-CONFINE-1` closed, `tool_registry.py:366`
   is the last seam where foreign code runs unconfined, in-process, with the live DB session and
   ambient authority.

**The correct characterization: a production-*oriented* substrate with production-*demonstrated*
packaging and isolation, awaiting its first real workload.**

### 11.3 Cost of closing the gap

From §6.4: **17 / 27.5 / 46.5 PM** of demonstrable gap work, rounded to **27 / 50 / 80 PM** after
adding the integration cost of landing that work in an existing codebase rather than greenfield —
which is what produces the production-equivalent figures in §8.3 (**$2.4M / $4.5M / $7.7M**).

One caveat the reader should weigh: **the soak programme cannot be completed by this repository
alone.** It requires a consumer. That is a dependency on work outside the audited boundary, and it
is why the production-equivalent *high* bound is wide.

---

## 12. Recurring operating cost

**Kept strictly separate from build cost.** This is what it costs per year to keep the artifact
alive, current and supportable — not to extend it.

**Evidence that maintenance load is non-trivial:**

- **136 dependabot commits in 92 days** — a run rate of ~540/year against 93 pinned dependencies.
- **27 releases in 92 days.** The release protocol spans three pin sites that must move together
  (`pyproject.toml`, `AINDY/requirements.txt`, and the CI MCP step), and the repository records that
  forgetting one has shipped a broken extra.
- **`MCP-SDK-2X-1`** — a first-party dependency cap currently blocks an upgrade, and the fix
  requires a coordinated three-repository release train.
- **`LOCKFILE-PLATFORM-1`** — every future rolldown/oxide bump needs manual treatment through a
  dedicated dispatch-only workflow.
- **`Runtime Docs Validation`** enforces a `last_verified` floor, so 134 documents require periodic
  re-verification or CI fails.

| Line item | Low | Expected | High |
|---|---:|---:|---:|
| Maintenance engineering (deps, releases, bug fixes, doc re-verification) | 1.0 FTE | 2.0 FTE | 3.0 FTE |
| SRE / on-call | 0.25 FTE | 0.75 FTE | 1.25 FTE |
| Security patching & CVE response (`pip-audit` workflow exists and gates) | 0.1 FTE | 0.25 FTE | 0.5 FTE |
| Documentation / DX upkeep | 0.15 FTE | 0.4 FTE | 0.75 FTE |
| **Total FTE** | **1.5** | **3.4** | **5.5** |
| Labor @ $265k | $398k | $901k | $1,458k |
| CI compute | $10k | $30k | $70k |
| Staging & test infrastructure (PG/Redis/Mongo/Prometheus) | $18k | $45k | $110k |
| LLM / embedding API (test, staging, soak) | $8k | $25k | $70k |
| Monitoring, artifact hosting, tooling | $6k | $18k | $40k |
| **Total annual operating cost** | **≈ $440k** | **≈ $1.02M** | **≈ $1.75M** |

Rounded: **$450k / $1.05M / $1.7M per year.**

*Excludes* continued feature development. A team that intends to close the open P0/P1 registry items
(`TOOL-SEAM-ISOLATION-1`, `EXEC-ENV-BIND-1`, `FLOW-PARALLEL-1`, `COST-GOVERNOR-1`,
`FLOW-GRAPH-SIGNATURE-1`, `AUTHORITY-NEGOTIATION-1`, `FS-SCOPE-1`, `EFFECT-PARTIAL-1`) should budget
an additional **1.5–3.0 FTE** on top.

---

## 13. What this estimate does not mean

Stated explicitly, because replacement-cost figures are routinely misread.

**This report does not establish, imply, or support any of the following:**

1. **It is not a market valuation or market capitalization.** Cost to build and value in a market
   are unrelated quantities. Software that costs $6M to build is routinely worth $0.
2. **It is not an acquisition price.** Acquirers pay for revenue, users, team, distribution,
   defensibility and strategic fit. None of those were assessed and none are present in this
   repository as evidence.
3. **It is not a revenue projection.** No revenue, customers, users or contracts were found. The
   repository's own `MONETIZATION_AUDIT.md` defers all five billing items to a commercial launch
   that has not occurred.
4. **It is not evidence of product-market fit.** `SUBSTRATE-WITNESS-1` establishes the opposite of
   demonstrated adoption: the flagship first-party application does not exercise the substrate's
   distinguishing paths at all.
5. **It is not an amount anyone owes anyone.** Replacement cost is a counterfactual accounting
   construct. No obligation of any kind follows from it.
6. **It is not a claim that every line was written by hand.** §4.4 and §8.5 state plainly that the
   evidence indicates heavy AI-assisted development. The principal estimate deliberately answers a
   *different* question — what a conventional organization would have paid — because that is what
   the brief asks.
7. **It is not a claim that calendar time equals person-time.** The project spans 494 calendar
   days of which **331 are dormant**; actual engineering occupied ~5.4 months of non-dormant
   calendar across **121 distinct active days** by one person (§4.2.2). None of that is the same
   quantity as the ~22.5 person-years a conventional team would need.
8. **It is not a code-quality certification.** Effort spent is not correctness. The repository
   itself catalogues nine occasions on which it shipped something that looked verified and was not.
9. **It is not a statement about what the original creator personally spent** in time, money, or
   anything else. That question was not asked and is not answered.

---

## 14. Uncertainty and confidence

### 14.1 Confidence by finding

| Finding | Confidence | Why |
|---|---|---|
| Repository scale (785 files, 191,416 lines, 130,978 SLOC) | **High** | Directly measured with a tokenizer; reproducible |
| Test inventory (2,150 collected unit tests; 17 escape tests all PASS) | **High** | `pytest --collect-only`; machine-readable results file |
| Subsystem inventory and maturity classification | **High** | Traced into source and tests individually |
| Total calendar 16.2 months, of which 5.4 months non-dormant / 121 active days | **High** | Distinct-day union across all three repositories; dormancy gaps >14d enumerated (§4.2.2) |
| ~67% of source predates commit 1; predecessor located and measured | **High** | `git show 0d5d382 --numstat`; predecessor repo inspected directly — 549 commits, `AINDY/` at 55,388 lines matching commit 1 exactly (§4.2.1) |
| Clean-room reproduction effort | **Moderate** | Three independent methods spanning 130–249 PM |
| Original creation effort | **Moderate-high** | Pre-history now measured, not inferred; COCOMO agrees within 9%; the estimate moved only 2% when the hidden history became visible |
| Production gap inventory | **Moderate-high** | Each gap verified by measurement (zero k6/locust; zero k8s files; no backup procedure) |
| Dollar figures | **Low-moderate** | Entirely dependent on a labeled, unverified compensation assumption |

### 14.2 The five facts that move the number most

1. **The blended fully loaded rate (§8.1).** Now the dominant uncertainty. Moves the headline
   between $3.4M and $6.8M across $150k–$300k — a factor of two, larger than any remaining
   engineering uncertainty in this report.
2. **~~The pre-extraction history~~ — RESOLVED (§4.2.1).** The predecessor repository was located
   and measured: 549 commits, 2025-04-11 → 2026-05-20, 57 active days, `AINDY/` at 55,388 lines
   matching this repository's commit 1 exactly. The expected estimate moved 2% (265 → 270 PM) once
   the unknown was bounded — and 270 → 281 PM again in §6.3's later restructure, a total drift of 6%
   across two methodology corrections.
3. **The AI-assistance factor (§8.5).** If the intended reading is "what would it cost *today*, with
   agents," the expected figure drops from $6.6M to roughly $4.9M. **§17 argues that model describes
   an AI-assisted *team* and does not describe how this artifact was actually produced.**
4. **Whether the ~13% of implemented-but-unvalidated surface counts at full price.** Eight
   capabilities are complete but shipped behind default-off flags awaiting a soak that cannot
   currently be run.
5. **The COCOMO reproduction discrepancy (§9.2).** COCOMO says 249 PM where the bottom-up says 130.
   If COCOMO is right, reproduction is ~$5.5M rather than $3.2M.

### 14.3 What additional evidence would narrow the range

| Evidence | Would resolve |
|---|---|
| ~~The predecessor repository's full git history~~ — **OBTAINED during this audit** (§4.2.1) | Resolved. Confidence in original-creation effort moved from moderate-low to moderate-high; the high bound narrowed by 40 PM |
| Session/token logs from the AI-assisted development | Would let §8.5's ×0.70 factor be measured rather than modeled |
| A CI timing and coverage report over the full suite | Would confirm the maturity classification empirically rather than by tracing |
| One benchmark of the native scorer versus the Python path | Would move the native accelerator from IIV to PO — the repository's own 2.4.0 changelog notes **no such comparison exists** |
| A first-party consumer exercising `execute_tool` / `EffectRecord` | Would resolve `SUBSTRATE-WITNESS-1` and unblock the entire soak backlog |
| Geography-specific compensation data | Would replace §8.1's labeled assumption with data |

### 14.4 Challenging the estimate in both directions

**Arguments that it is OVERSTATED:**

- **Framework leverage.** 93 pinned dependencies do enormous work. FastAPI supplies routing,
  validation and OpenAPI; SQLAlchemy + Alembic supply the ORM and migration engine; APScheduler
  supplies scheduling; `pgvector` supplies vector search; `prometheus-fastapi-instrumentator`
  supplies most metrics plumbing. The *integration* is counted here — reasonably — but a skeptic
  could argue the integration is thinner than 22 person-years implies.
- **~3.5% of source is dead or inert**, and ~13% is behind default-off flags. A hard-nosed reviewer
  could deduct all of it.
- **Documentation volume may be inflated relative to effort.** 49,607 lines is a lot, and §8.5
  concedes that a meaningful fraction is agent-generated-then-edited. Pricing it at the same rate as
  hand-authored specification would overstate.
- **Some subsystems are thinner than their line counts suggest.** `sandbox_runner.py` is 2,437 lines
  but a substantial portion is platform capability matrices and posture reporting — structured data
  in code form, not algorithmic complexity.
- **No production operation has ever occurred.** A reviewer could argue that untested-in-anger
  infrastructure should be discounted regardless of how well-tested it is in CI.

**Arguments that it is UNDERSTATED:**

- **Abandoned work is priced only through a multiplier, not line by line.** The predecessor's
  **60,441-line test suite is 88% dead** (232 of 264 basenames have no successor), and the October
  2025 `v0.9-pre` backend was superseded. Together that is well over 60,000 lines of professionally
  written, working code that produced no surviving artifact. §6.3a absorbs it into a ×1.65 uplift; a
  reviewer who priced it directly — at even the fast-team rate of 1,800 lines/PM — would add
  **~35 PM** and push the expected figure past $7M.
- **Declined designs cost real money and produce zero artifacts.** Kernel deterministic replay,
  `HOOK-PRECEDENCE-1` and `DISPATCH-ADMISSION-1` each consumed a design cycle. LOC-based and even
  bottom-up estimates are structurally blind to them.
- **The comparative-research programme is excluded entirely.** `docs/runtime/COMPARATIVE_RESEARCH_INDEX.md`
  indexes audits of **17 external systems** (Aider, MAF, CrewAI, LangGraph, Temporal, OpenHands,
  MetaGPT, SWE-agent, GPT Engineer, ADK, Codex, Claude Code, Devika, Hermes, Linux, Open Interpreter,
  Autogen). Each produced an `ACCURACY_CHECK_vs_aindy-runtime_*.md`. That is a substantial
  competitive-analysis programme whose *conclusions* shape this codebase and whose *cost* sits
  outside the audited boundary.
- **The decision corpus is priced at a 24 PM re-earning cost, which is probably low.** Reconstructing
  *"the first-draft wire suite scored 4/7 because a test asserting an absence passes when the wire is
  broken"* requires re-running the experiment. Ten thousand lines of that is not 24 person-months of
  writing; it is 24 person-months of writing **on top of** the experiments that produced it — and
  those experiments are counted in the subsystem lines only where they happened to produce code.
- **Security work is systematically under-priced by all effort models.** Two privilege escalations
  found and fixed, one demonstrated sandbox escape, an egress boundary, a secret broker, extension
  signing and provenance, and a 6-vector escape suite with 17 dated audit entries. The 8.0 PM
  assigned to the isolation subsystem is a floor, not a midpoint.
- **The 46-type causal event graph** with an `event_edges` table and a frozen-hash contract baseline
  is a genuine design artifact that most agent runtimes simply lack, and it is priced inside a 4.5 PM
  observability line.

**Net judgment:** the arguments for understatement remain more specific and better evidenced than
the arguments for overstatement — now that the pre-history is measured rather than inferred, the
strongest of them is the **88%-dead 60,441-line predecessor test suite**, which is a counted fact
and not an interpretation. The expected figures in §1 should be read as **conservative**, though
less so than before the predecessor was inspected: the largest previously-open-ended allowance has
been closed, and it closed *downward* on the high bound.

---

## 15. Final verdict

> **If a technically competent company had commissioned the current `aindy-runtime` from scratch,
> what team, time, and budget would it most likely have required?**

**A cross-functional team of about ten people, working for roughly two and a half years, at a fully
loaded cost of approximately $6.6 million.**

Specifically:

| | |
|---|---|
| **Team** | 1 principal architect · 3 senior runtime/distributed-systems engineers · 1 AI/agent-systems engineer · 1 security/platform engineer · 1 DevOps/SRE · 1 frontend engineer · 1 test/quality engineer · 0.5 database engineer · 0.5 technical writer · 0.5 program manager — **~10.5 FTE at peak, ~9.5 average** |
| **Calendar** | **~30 months**, gated by a serialized dependency chain (syscall contract → execution pipeline → flow engine → agent runtime) that additional headcount cannot compress |
| **Effort** | **~281 person-months ≈ 23.4 person-years** — of which **50 PM is architectural invention** (§6.3b), the term conventional cost models cannot see |
| **Budget** | **≈ $6.6M** fully loaded (**$2.8M** aggressive floor, **$13.0M** enterprise ceiling) |
| **Plus, to operate it** | **≈ $1.05M per year** thereafter |

**And if the question is the whole estate rather than the runtime (§16):**

| If you mean… | Team | Calendar | Cost |
|---|---:|---:|---:|
| the runtime substrate (Scope A) | ~10 | ~30 mo | **$6.6M** |
| the working product — + 16 applications, a product SPA, SDK, UI-kit (Scope B) | ~15 | ~35–38 mo | **$12.2M** |
| **everything built — + the `nodus-lang` language it runs on (Scope C)** | **~19** | **~38–44 mo** | **$16.3M** |

None of these layers is separable in practice. The applications cannot be built against a syscall
contract that does not exist; the runtime pins `nodus-lang==5.0.1` and embeds its VM as an
execution path. The runtime's critical path is the product's critical path, and the language's
critical path precedes both.

**Three qualifications a skeptical reviewer should carry away:**

1. **Handed this repository as a specification, the same company could rebuild it clean-room for
   about half — ~$3.2M over ~18 months with ~6 engineers.** The difference is not code. It is the
   ~$3M worth of answered questions, recorded failures and declined designs now sitting in
   `TECH_DEBT.md`, `CLAUDE.md`, the 27-release changelog and the escape audit log.

2. **The artifact is production-*oriented*, not production-*proven*, and the gap is not primarily
   engineering.** Closing it costs another ~$1.3M — but the largest single item, soaking and
   flipping the default-off capability flags, is blocked on the absence of a real workload
   (`SUBSTRATE-WITNESS-1`), not on effort. That is a business dependency, not a backlog item.

3. **The estimate survived contact with the missing evidence.** The predecessor repository was
   located mid-audit and measured directly (§4.2.1): 549 commits, 2025-04-11 → 2026-05-20, with
   `AINDY/` at exactly the 55,388 lines that became this repository's commit 1. Pricing that period
   from evidence rather than inference moved the expected figure by **2%** (265 → 270 PM); §6.3's
   later separation of rework from invention moved it to 281 PM. What the evidence did add is the project's largest
   measured instance of discarded work — **a 60,441-line test suite that is 88% dead** — which is
   why the expected figure ticked up rather than down.

---

---

## 16. Whole-project scope — addendum

**Why this section exists.** §1–§15 price `aindy-runtime` alone, because that is what the audit
brief named. But the runtime is one half of a deliberate repository split (§4.2), it does not ship
as a usable product on its own, and its own README says so. Pricing only the half understates the
project. This section prices the whole thing, in four concentric scopes, **without revising any
figure above** — Scope A remains the answer to the original question.

### 16.1 The four scopes

| Scope | Contents | Rationale |
|---|---|---|
| **A — the runtime** | `aindy-runtime` | The audit brief's question. §1–§15. |
| **B — the shipped product** | + `aindy-apps-monolith`, `aindy-sdk`, `aindy-ui-kit` | The two halves of the split plus the two published client libraries the runtime's contracts depend on. |
| **C — the full engineering estate** | + **`nodus-lang`** (`C:\dev\Coding Language`) | The language the runtime *pins as a dependency* — lexer, compiler, VM, LSP, DAP, stdlib, package manager, 33 releases. **Same author, same window. This is the honest answer to "the entire project."** §16.5. |
| **D — the satellites** | + 36 `nodus-*` adapter repos and `claw` | Byproducts of C. **Recommended for exclusion** — §16.5.1. |

### 16.2 Scope B measured

| Repository | Files | Physical lines | Logical SLOC | Commits | Active days |
|---|---:|---:|---:|---:|---:|
| `aindy-runtime` | 785 | 191,416 | 130,978 | 704 | 61 |
| `aindy-apps-monolith` | 930 | 137,921 | 98,956 | 264 | 40 |
| `aindy-sdk` | 26 | 4,175 | 2,531 | 12 | 6 |
| `aindy-ui-kit` | 28 | 2,225 | 1,842 | 32 | 10 |
| **Scope B total** | **1,769** | **335,737** | **234,307** | **1,012** | **66 distinct** |
| *(predecessor — history evidence only, not counted)* | *1,057* | *—* | *165,578 py* | *549* | *57* |
| **Whole project, distinct active days** | | | | **1,561** | **121** |

`aindy-apps-monolith` breakdown (`scratchpad/`, 2,536 lines, excluded as working notes):

| Bucket | Files | Physical | SLOC |
|---|---:|---:|---:|
| `apps/` — 16 applications | 368 | 51,400 | 38,471 |
| `client/` — React product SPA | 181 | 24,905 | 21,457 |
| Documentation | 74 | 24,478 | 14,745 |
| Tests | 106 | 17,736 | 11,697 |
| Alembic migrations (**155 files**) | 155 | 9,975 | 6,446 |
| Scripts / ops | 26 | 7,410 | 4,952 |
| CI / compose / config | 20 | 2,017 | 1,188 |
| **Total** | **930** | **137,921** | **98,956** |

The 16 applications: `analytics` (73 files), `search` (39), `masterplan` (36), `rippletrace` (34),
`automation` (28), `tasks` (25), `arm` (25), `freelance` (24), `social` (18), `agent` (18),
`identity` (15), `authorship` (12), `network_bridge` (6), `memory` (5), `dashboard` (5),
`autonomy` (5). 264 commits, **221 through merged PRs**, 40 active days, 2026-05-17 → 2026-08-18.

Note the second frontend: `client/` is **21,457 SLOC across 181 files** — a full product UI, three
times the size of the runtime's operator console, and a distinct piece of work.

### 16.3 Third-party donated labor — a correction to the brief's premise

The brief asks what it would cost **"without relying on the original creator's uncompensated
labor."** Inspecting the monolith surfaces something the runtime-only audit could not: **the
creator's is not the only uncompensated labor in this project.**

`aindy-apps-monolith/CONTRIBUTORS.md` (added 2026-08-18, `d01055a`, *"credit Drew Brown and
Jonathan Rapsiarda for the work they authored"*) records two code contributions given directly and
used with permission:

| Contributor | Location | Size | Retained |
|---|---|---:|---|
| **Drew Brown** | `apps/authorship` — the Epistemic Reclaimer, originally Step 6 of his Scribalicious Pipeline | 323 py lines / 12 files | The `epistemic_reclaim` concept and name, the `INVISIBLE_WATERMARK` zero-width sequence, the entropy-disruption approach, Unicode fingerprint embedding, the visible signature block. His original guide is kept in-repo. |
| **Jonathan Rapsiarda** | `apps/arm/services/deepseek` — the DeepSeek Analyzer, origin of ARM's analysis package | 1,277 py lines / 5 files | Module names `deepseek_code_analyzer`, `security_deepseek`, `file_processor_deepseek`, `config_manager_deepseek` mark the original structure. The repository states the analyzer is substantially rewritten and the support modules less so. |

A third contribution is **architectural rather than code**: `CONTRIBUTORS.md` in *this* repository
credits **Cherokee Schill** for the framing behind `AINDY/memory/bridge.py` — memory as *continuity
and authorship* rather than storage — and is careful to state what is *not* claimed (none of her
work is the ancestor of the capability system).

**Three observations for a cost audit:**

1. **~1,600 lines of donated code is small in volume** (0.5% of Scope B) and does not warrant a
   separate line item in the cost model.
2. **It is not small in what it says about provenance.** An organization reproducing this artifact
   could not obtain those contributions by donation; it would commission or license them.
3. **The attribution discipline is itself evidence relevant to §10.2.** Both `CONTRIBUTORS.md`
   files state that vague credit is worse than none, require recording *what was retained and what
   was changed*, and demand agreement across three places — the file, the module docstring, and any
   retained original documentation. One of them was written specifically to correct an omission.
   That is the same self-correcting posture the decision corpus shows, applied to authorship.

### 16.4 Scope B effort and cost

Bottom-up, same method and scenario definitions as §6.1. The runtime's line is carried over
unchanged; the monolith is decomposed fresh.

| Component | Low | Expected | High |
|---|---:|---:|---:|
| `aindy-runtime` (§6.1 subtotal) | 67.5 | 108.0 | 170.0 |
| Monolith — 16 applications (38,471 SLOC) | 19.0 | 30.0 | 46.0 |
| Monolith — `client/` React product SPA (21,457 SLOC, 181 files) | 8.0 | 13.0 | 20.0 |
| Monolith — persistence + **155** migrations | 4.0 | 6.0 | 9.0 |
| Monolith — tests (11,697 SLOC) | 4.0 | 6.0 | 9.0 |
| Monolith — scripts / ops (4,952 SLOC + 65 shell) | 2.0 | 3.0 | 5.0 |
| Monolith — documentation (14,745 SLOC, 74 files) | 2.5 | 4.0 | 6.0 |
| Monolith — CI / deployment | 1.5 | 2.5 | 4.0 |
| Monolith — cross-app architecture + runtime integration | 3.0 | 5.0 | 8.0 |
| `aindy-sdk` + `aindy-ui-kit` (published packages with contracts) | 1.5 | 2.5 | 4.0 |
| **Engineering subtotal** | **113.0** | **180.0** | **281.0** |

**Coordination overhead is higher for Scope B than for Scope A**, and the evidence says so: a
four-repository product with `CROSS_REPO_COMPATIBILITY.md`, a published compatibility policy, five
versioned app-handoff documents, and a release protocol spanning three pin sites carries genuine
cross-repo tax that a single repository does not.

| Scenario | Overhead | Total PM |
|---|---:|---:|
| Low | +12% | **127** |
| Expected | +22% | **220** |
| High | +36% | **382** |

**Cross-checks** (same three methods as §9, applied to Scope B):

- **Productivity band** over 332,780 physical artifact lines: 185 PM @1,800 · **277 PM @1,200** ·
  370 PM @900.
- **COCOMO II, computed component-wise** rather than on the aggregate — deliberately, because the
  repositories are separated by a real contract boundary, which is precisely the structure that
  avoids a whole-system diseconomy of scale. Runtime component (68.1 KSLOC, CPLX Very High):
  **249 PM**. Monolith + SDK + UI-kit component (75.8 KSLOC, CPLX Nominal, RUSE Nominal, DOCU High
  → ∏EM ≈ 0.73): **192 PM**. Sum: **441 PM**.

| Method | Scope B reproduction (PM) |
|---|---:|
| Bottom-up (above) | 220 |
| Productivity band | 277 |
| COCOMO II, component-wise | 441 |
| **Weighted** (0.50 / 0.30 / 0.20) | **281** |

**Reported Scope B clean-room reproduction: 130 / 280 / 450 PM.** Applying §6.3's composition —
rework uplift ×1.35/×1.65/×1.85 plus an invention term of **21 / 58 / 110 PM** (Scope A's 18/50/95
plus a modest 3/8/15 for the monolith, whose applications are domain logic rather than novel
substrate) — gives **Scope B original creation: 196 / 520 / 942 PM.**

*This is lower than the 546 PM an earlier revision reported, because the single ×1.95 multiplier it
used applied kernel-grade rework rates to application code. The restructure in §6.3 corrects that.*

Cost at $265,000 per engineer-year:

```
SCOPE B — CLEAN-ROOM REPRODUCTION
Low      130 / 12 = 10.83 PY x $265,000 = $ 2,870,833 + $ 90,000 = $ 2,960,833  ->  $3.0M
Expected 280 / 12 = 23.33 PY x $265,000 = $ 6,183,333 + $260,000 = $ 6,443,333  ->  $6.4M
High     450 / 12 = 37.50 PY x $265,000 = $ 9,937,500 + $600,000 = $10,537,500  -> $10.5M

SCOPE B — ORIGINAL CREATION
Low      196 / 12 = 16.33 PY x $265,000 = $ 4,328,333 + $  180,000 = $ 4,508,333  ->  $4.5M
Expected 520 / 12 = 43.33 PY x $265,000 = $11,483,333 + $  700,000 = $12,183,333  -> $12.2M
High     942 / 12 = 78.50 PY x $265,000 = $20,802,500 + $1,700,000 = $22,502,500  -> $22.5M
```

**Scope B team and calendar (expected case):** the monolith adds application engineers and a
product frontend team to Model B of §7.2 — roughly **+3 application engineers, +1.5 frontend,
+0.5 QA**, giving **~15 FTE**. At 520 PM that is **~35–38 months**. The sequencing constraint
tightens rather than relaxes: the applications cannot be built against a syscall contract that does
not exist yet, so the runtime's critical path becomes the product's critical path.

### 16.5 Scope C — `nodus-lang`, the language

> **★ Correction.** An earlier revision of this addendum dismissed the whole Nodus tier as
> "generated scaffolding" on the strength of the 36 `nodus-*` satellite repositories, and recorded
> that *"`nodus-lang` itself is not here — it is a pinned PyPI dependency."* **That was wrong, and
> it was wrong in the way this report warns against elsewhere: a conclusion drawn from what was
> easy to enumerate rather than from what was material.** The language is at
> `C:\dev\Coding Language`; its `pyproject.toml` reads `name = "nodus-lang"`, `version = "5.0.4"` —
> the same package `aindy-runtime` pins at `nodus-lang==5.0.1`. It is the largest single engineering
> artifact in the entire project, and the satellites are downstream of it. §16.6 is restructured
> accordingly.

| Fact | Value |
|---|---|
| Repository | `C:\dev\Coding Language` (published as `nodus-lang`) |
| First commit | `babbf9a`, **2026-03-12**, *"Initial commit"*; `da88df9` next day, *"Initial Nodus language runtime"* |
| Latest commit | `b633aef`, 2026-08-18 |
| Commits | **503** (634 across all refs) |
| **Release tags** | **33**, reaching **v5.0.4** — five major versions in five months |
| Active days | **49** |
| Sole author | Masterplanner25 — the same person, in the same window as the AINDY work |
| **Days where both Nodus and Scope B were committed to** | **33** |

**Measured scale:**

| Bucket | Files | Physical | SLOC |
|---|---:|---:|---:|
| Documentation (`.md`) | 247 | 73,588 | 50,438 |
| `src/` — language implementation | 129 | 37,431 | 29,982 |
| Tests | 177 | 32,908 | 20,074 |
| `packages/` — stdlib & bundled packages | 58 | 4,648 | 3,882 |
| Examples & demos | 38 | 2,482 | 1,730 |
| Nodus source (`.nd`/`.tl`) elsewhere | 53 | 2,121 | 1,707 |
| `tools/` | 13 | 2,060 | 1,428 |
| Config / manifests | 13 | 499 | 458 |
| Root-level `.py` (CLI, package manager) | 7 | 281 | 174 |
| **TOTAL** | **735** | **156,018** | **109,873** |

**This is a complete language toolchain, not a DSL parser.** The structure is the evidence:

| Component | File | Lines |
|---|---|---:|
| Virtual machine | `src/nodus/vm/vm.py` | **3,495** |
| CLI | `src/nodus/cli/cli.py` | 2,486 |
| Language service / server | `src/nodus/services/server.py` | 1,622 |
| Orchestration task graph | `src/nodus/orchestration/task_graph.py` | 1,417 |
| Tooling runner | `src/nodus/tooling/runner.py` | 1,374 |
| **Compiler** | `src/nodus/compiler/compiler.py` | 1,321 |
| **Parser** | `src/nodus/frontend/parser.py` | 1,235 |
| Module loader | `src/nodus/runtime/module_loader.py` | 1,184 |
| Durable workflow store | `src/nodus_lang_workflow/store.py` | 1,103 |
| Embedding runtime | `src/nodus/runtime/embedding.py` | 1,061 |
| Workflow runner | `src/nodus_lang_workflow/runner.py` | 1,053 |
| **LSP server** (editor integration) | `src/nodus/lsp/server.py` | 1,032 |
| **DAP server** (debugger) | `src/nodus/dap/server.py` | 637 |
| Workflow lowering | `src/nodus/orchestration/workflow_lowering.py` | 657 |
| Formatter · diagnostics · REPL | `src/nodus/tooling/*` | 607 · 576 · 457 |
| **Lexer** | `src/nodus/frontend/lexer.py` | 570 |
| AST nodes | `src/nodus/frontend/ast/ast_nodes.py` | 476 |
| Builtins | `http` 750 · `time` 712 · `subprocess` 648 · `test` 521 · `tool` 476 | |

`src/nodus/` subpackages: `frontend`, `compiler`, `vm`, `runtime`, `orchestration`, `builtins`,
`stdlib`, `tooling`, `tools`, `cli`, `lsp`, `dap`, `services`, `testing`, `support`, `main`.

**Lexer → parser → AST → compiler → VM**, plus a module system, a package manager, a stdlib,
an LSP server, a DAP debugger, a REPL, a formatter, a diagnostics engine, and **2,319 test
functions** across 177 files. Thirty-three tagged releases. That is a language implementation by
any reasonable definition.

**It is also directly coupled to this repository's engineering, not merely adjacent to it.**
`src/nodus_lang_workflow/` (store 1,103 + runner 1,053 lines) is the component `aindy-runtime`'s own
debt register calls out under `ORCHESTRATOR-SPLIT-1` as *"a SQLite `LocalWorkflowStore` that
independently reimplements this runtime's whole durability vocabulary — claims, waits-with-expiry,
retry scheduling, rehydration, terminal classification."* The two codebases converged on the same
durability problem from opposite sides. That is coupled design work, and the register treats it as
such.

#### Nodus effort and cost

| Component | Low | Expected | High |
|---|---:|---:|---:|
| Frontend — lexer, parser, AST | 3.0 | 5.0 | 8.0 |
| Compiler + workflow lowering | 3.0 | 5.0 | 8.0 |
| Virtual machine + runtime | 6.0 | 9.0 | 14.0 |
| Module system + package manager | 2.5 | 4.0 | 6.5 |
| Builtins + stdlib (`http`, `time`, `subprocess`, `test`, `tool`) | 3.0 | 5.0 | 8.0 |
| Orchestration + durable workflow store/runner | 4.0 | 6.0 | 9.0 |
| Tooling — CLI, REPL, formatter, diagnostics, runner | 4.0 | 6.0 | 9.0 |
| LSP server + DAP debugger | 2.5 | 4.0 | 6.0 |
| Language service / server | 1.5 | 2.0 | 3.5 |
| Tests (2,319 functions, 20,074 SLOC) | 5.0 | 8.0 | 12.0 |
| Documentation (247 files, 50,438 SLOC) | 4.0 | 7.0 | 11.0 |
| Release engineering (33 tags, PyPI) | 1.5 | 2.0 | 3.5 |
| Language *implementation* coordination | 2.5 | 4.0 | 7.0 |
| **Engineering subtotal (reproduction)** | **42.5** | **67.0** | **105.5** |

**Language design proper — syntax, semantics, the gating and confinement model — is an invention
term, not an implementation one**, and follows §6.3b's treatment: **6 / 12 / 22 PM**, added to
creation only. A reproducing team is handed the grammar and the semantics.
| Coordination overhead | +10% | +20% | +32% |
| **Total** | **47** | **80** | **139** |

**Cross-checks:**

- **Productivity band** over 156,018 physical lines: 87 PM @1,800 · **130 PM @1,200** · 173 PM @900.
- **COCOMO II** on 35.5 KSLOC of product source (`src` + `packages` + `tools` + root), with
  CPLX Very High (1.34 — a compiler and VM qualify), RUSE Very High (1.15 — a published language
  with a stdlib and 33 releases), DOCU Very High (1.13), PVOL Nominal (1.00 — few external
  dependencies) → ∏EM ≈ 0.99. Reproduction (E = 1.0379): **118 PM**. Creation (E = 1.0769):
  **136 PM**.

| Method | Nodus reproduction (PM) |
|---|---:|
| Bottom-up | 80 |
| Productivity band | 130 |
| COCOMO II | 118 |
| **Weighted** (0.50 / 0.30 / 0.20) | **103** |

**Reported: Nodus clean-room reproduction 50 / 100 / 160 PM.** Applying §6.3's composition — rework
uplift ×1.35/×1.65/×1.85 plus the 6 / 12 / 22 PM language-design invention term — gives **Nodus
original creation: 74 / 177 / 318 PM.**

The ×1.65 expected rework uplift is well supported here: **five major versions in five months**
(v1 → v5.0.4, 33 tags) is heavy iteration, and the v5.0.0 break was disruptive enough downstream that this
repository logged it twice — `MCP-SDK-2X-1` (a cap in `nodus-mcp` made
`pip install "nodus-lang==5.0.0" "nodus-mcp>=0.1.2"` a `ResolutionImpossible`, which would have
shipped an uninstallable extra) and `NODUS-UPGRADE-1` (four confinement tests went red, none a real
regression).

```
NODUS — CLEAN-ROOM REPRODUCTION
Low       50 / 12 =  4.17 PY x $265,000 = $ 1,104,167 + $ 30,000 = $ 1,134,167  ->  $1.1M
Expected 100 / 12 =  8.33 PY x $265,000 = $ 2,208,333 + $ 80,000 = $ 2,288,333  ->  $2.3M
High     160 / 12 = 13.33 PY x $265,000 = $ 3,533,333 + $180,000 = $ 3,713,333  ->  $3.7M

NODUS — ORIGINAL CREATION
Low       74 / 12 =  6.17 PY x $265,000 = $ 1,634,167 + $ 50,000 = $ 1,684,167  ->  $1.7M
Expected 177 / 12 = 14.75 PY x $265,000 = $ 3,908,750 + $180,000 = $ 4,088,750  ->  $4.1M
High     318 / 12 = 26.50 PY x $265,000 = $ 7,022,500 + $420,000 = $ 7,442,500  ->  $7.4M
```

**A language needs role specialists the other scopes do not.** A compiler/VM engineer and a
language-tooling engineer (LSP, DAP, formatter, REPL) are distinct hires from a runtime engineer,
and they are typically priced at or above the principal rate. The $265k blend is retained for
comparability, but for Nodus alone it is more likely a floor than a midpoint.

### 16.5.1 Scope D — the 36 satellite repositories and `claw`

The original Scope C argument survives, but it applies **only to the satellites**, not to the
language they wrap:

| | Value |
|---|---|
| Repositories | 37 (36 `nodus-*` + `claw`) |
| Code lines | **59,442** (40,626 excluding `claw`) |
| Total commits | **261** |
| Largest | `claw` 18,816 lines / 37 commits / **4 active days**; `nodus-mcp` 10,084 / 34 / 8; `nodus-extension` 2,328 / 8 / 5 |
| Typical repo | ~700–1,100 lines, **3–5 commits, 2–4 active days** |

**Recommendation: exclude Scope D from headline figures.** Thirty of the repositories average ~4
commits over ~3 active days — no iteration, review, defect discovery or hardening, which is where
most of the cost in §6 lives. `claw` is the specific counter-evidence: the largest at 18,816 lines,
and the same application `SUBSTRATE-WITNESS-1` measured as integrating with the runtime in 334
optional lines that never touch `execute_tool`, `EffectRecord` or `execution_token`.
`nodus-mcp` (10,084 lines, 34 commits) is the one plausible ex### 16.6 Summary — all scopes

| Scope | Physical lines | Reproduction (expected) | Original creation (expected) | Team | Calendar |
|---|---:|---:|---:|---:|---:|
| **A — `aindy-runtime`** | 191,416 | **$3.2M** / 140 PM | **$6.6M** / 281 PM | ~10 | ~30 mo |
| **B — shipped product** (+ monolith, SDK, UI-kit) | 335,737 | **$6.4M** / 280 PM | **$12.2M** / 520 PM | ~15 | ~35–38 mo |
| **C — full estate** (+ `nodus-lang`) | **491,755** | **$8.7M** / 380 PM | **$16.3M** / 697 PM | **~19** | **~38–44 mo** |
| *D — + 36 satellites and `claw`* | ~551,000 | *excluded* | *+$0.6M–$1.3M allowance only* | — | — |

**Scope C arithmetic** (Scope B + Nodus, summed component-wise — the repositories are separated by
a published package boundary, which is what makes summation rather than a whole-system scale
exponent the right treatment):

```
SCOPE C — CLEAN-ROOM REPRODUCTION      (B 130/280/450 + Nodus 50/100/160)
Low      180 / 12 = 15.00 PY x $265,000 = $ 3,975,000 + $  120,000 = $ 4,095,000  ->  $4.1M
Expected 380 / 12 = 31.67 PY x $265,000 = $ 8,391,667 + $  340,000 = $ 8,731,667  ->  $8.7M
High     610 / 12 = 50.83 PY x $265,000 = $13,470,833 + $  780,000 = $14,250,833  -> $14.3M

SCOPE C — ORIGINAL CREATION            (B 196/520/942 + Nodus 74/177/318)
Low      270 / 12 = 22.50 PY x $265,000 = $ 5,962,500 + $  230,000 = $ 6,192,500  ->  $6.2M
Expected 697 / 12 = 58.08 PY x $265,000 = $15,392,083 + $  880,000 = $16,272,083  -> $16.3M
High    1260 / 12 =105.00 PY x $265,000 = $27,825,000 + $2,100,000 = $29,925,000  -> $29.9M
```

*Scope C's expected figure fell from $17.2M in an earlier revision. The cause is §6.3's
restructure, not a reassessment of the work: the single ×1.95 multiplier previously applied
kernel-grade rework rates uniformly to application and language code. Invention is now an explicit
additive term (§6.3b), which raises Scope A and lowers B and C — a redistribution, not a markdown.*

**Scope C team (expected):** Model B of §7.2, plus the monolith's application and product-frontend
engineers (§16.4), plus **a compiler/VM engineer and a language-tooling engineer** (LSP, DAP,
formatter, REPL) — roughly **19 FTE**. At 697 PM that is **~38–44 months**. Language work is the
one part that genuinely *cannot* be compressed by headcount: a VM, a type/gating model and a
debugger protocol are each single-owner designs before they are implementations.

**Scope C timeline check.** All of it — concept to `aindy-runtime` v2.4.0 and `nodus-lang` v5.0.4 —
spans **2025-04-11 → 2026-08-18 = 494 calendar days**, of which **326 are dormant**. Actual
engineering occupied **~5.5 months of non-dormant calendar across 132 distinct active days**, by one
person, with **33 days on which both Nodus and the AINDY product received commits**. §8.5's
AI-assistance counterfactual is the only model in this report that goes any distance toward
explaining that throughput, and it does not fully explain it.

**Which number to use:**

- **A ($6.6M)** — what a buyer acquiring only the runtime substrate is displacing.
- **B ($12.2M)** — what an organization reproducing the working product pays.
- **C ($16.3M)** — **the honest answer to "what did this whole thing cost to create?"** The runtime
  cannot be built without a language it pins; the language was built by the same person in the same
  window; and neither is separable from the other in practice.

**The three qualifications in §15 apply to all scopes**, and one gains force at C: the
production-readiness assessment (§11) covered the runtime only. Neither the monolith nor Nodus was
audited to the depth of §5 for load testing, disaster recovery or cloud deployment, and nothing
observed suggests those exist there either. **A Scope C production-equivalent figure would exceed
the ×1.13 uplift §8.3 applies to Scope A** — it is deliberately not computed, because doing so
honestly requires an audit this addendum does not perform.

---

1.13 uplift §8.3 applies to Scope A** — it is deliberately not computed here, because
doing so honestly would require auditing the monolith to the depth of §5, which this addendum does
not do.

---

---

## 17. The observed production function

**Why this section exists.** Every figure in §1–§16 prices a counterfactual: what a conventional
organization would have paid. The artifact was not produced that way. §8.5 gestured at the gap and
called AI assistance an incomplete explanation, which is a hand-wave in a document that elsewhere
refuses them. **This is the single most attackable claim in the report**, and it deserves the
arithmetic rather than a shrug.

### 17.1 The gap, stated numerically

| | Value |
|---|---:|
| Scope C conventional estimate (§16.6) | **697 PM** |
| Observed: 132 distinct active days across all repositories (§16.6) | |
| — at 8h/day | ~6 PM |
| — at 12h/day | ~9 PM |
| — at 16h/day | ~12 PM |
| **Implied ratio** | **58× – 116×** |

§8.5's model claims a ×1.42 throughput factor from AI assistance. **That is not within two orders of
magnitude of explaining this**, and no adjustment to that factor rescues it. Something structural is
different, not something parametric.

### 17.2 What actually closes the gap

Decomposed against this report's own numbers, in order of size:

| Effect | Mechanism | Residual |
|---|---|---:|
| Start | Scope C conventional | 697 PM |
| **Coordination overhead does not shrink — it vanishes** | §6.2 and §16.4 add +22–36% for a team. A solo author pays none of it: no standups, no design review cycle, no handoff loss, no onboarding, no cross-repo negotiation | ~570 PM |
| **Conceptual integrity is free rather than bought** | §7.3 states the kernel is a chokepoint with a fan-in of 88 on one module, and that adding engineers past ~11 buys rework rather than throughput. A large share of a 19-person budget goes to *achieving* the alignment one designer has by default | ~420 PM |
| **The rework uplift is lower for the person who holds the design** | §6.3a's ×1.65 prices a team re-deriving answers it does not have. The author had them | ~300 PM |
| **Residual attributable to generation** | ~300 PM against 6–12 PM observed | **~25× – 50×** |

A 25–50× multiplier on *volume* is plausible for directed generation on greenfield work, and the
composition of the artifact supports it: **~243,000 of the ~492,000 Scope C lines are documentation
and tests** — the two categories that accelerate most and that dominate line count without
dominating design difficulty.

### 17.3 The production function is not "typing → reviewing"

An earlier revision of this report described the bound as moving *"from typing to reviewing."* That
is wrong, and it understates the human role in a way that matters for costing.

**The author writes no code at all.** "Reviewing" implies someone else made the design decisions and
a human checked them. Here the design decisions are the human's and the *implementation* is
delegated. The observed shape is:

```
invent → specify → direct → verify
   ^                            |
   +----------- correct --------+
```

The human occupies **both ends**: the architecture at the front, the acceptance decision at the
back. In conventional role terms (§7.2), what is retained is **principal architect + technical lead
+ product owner**; what is displaced is the implementation tier — senior backend, application,
frontend and test engineers.

**★ This is corroborated in advance, not reconstructed after the fact.** §4.2.3 dates two
published articles from December 2025 — before the phase that produced most of the runtime source —
stating the same model in the author's own words: the escalation *prompts → patterns → architecture
→ ecosystem*, and *"prompts do not fix architectural behavior."* An argument of this kind derived
only from a repository after the fact would be considerably weaker; this one has a public timestamp
ahead of the build.

That reframing has a direct costing consequence: **it is not one person doing nineteen people's
jobs.** It is one person doing three roles that a conventional org would also assign to one or two
people, with the other sixteen roles' *output* generated and the human's *review bandwidth* becoming
the binding constraint.

### 17.4 Evidence that the binding constraint was review bandwidth — and was known

This is not inference. The repository's failure record is precisely the signature of high-volume
generation under finite review, and its governance apparatus is precisely what one builds to
compensate.

**The signature** — every one of these is a plausible-looking artifact that passed casual
inspection:

- Nine catalogued ways a green check lied (`CLAUDE.md` §*Trusting a green check*)
- Six documents citing eight test files that never existed (`DOCS-COVERAGE-CLAIM-1`)
- A boot-time route proof with no call site (`ROUTE-AST-UNWIRED-1`)
- A first-draft wire suite that mutation-scored **4/7** (`EVENTBUS-COVERAGE-1`)
- 268 tests in 24 files that ran in no CI job (`CI-MARKER-1`)

**The compensation** — mechanisms whose only purpose is to catch what review would otherwise miss:

- A **10,770-line decision register** with ~58 numbered entries carrying diagnosis, measurement and
  correction
- **Mutation testing with liveness controls**, adopted after a suite scored 4/7 — the explicit rule
  that *"a test asserting an absence needs a liveness control or it is vacuous by construction"*
- `tests/unit/conftest.py` auto-marking, so a new test file **cannot** silently run in no job
- `test_debt_registry_accuracy.py` — a test that **fails when the debt index contradicts its own
  entries**
- `test_apscheduler_shim_parity.py` — a source-derived guard that fails when a vendored shim cannot
  express a call the runtime makes
- `Runtime Docs Validation` enforcing a real `last_verified` floor across 134 documents

**That apparatus is the visible cost of holding the bound.** It is also what distinguishes this
artifact from high-volume generated output generally: the volume is not unusual, and the machinery
built to keep it honest is.

### 17.5 What the mode systematically loses

The production function predicts its own gaps, and §11's gap list is not a random assortment —
**every item on it is something this mode structurally cannot produce**, regardless of skill or
effort:

| §11 gap | Why the mode cannot close it |
|---|---|
| **No first-party consumer** (`SUBSTRATE-WITNESS-1`) | Requires someone else's product to depend on it |
| **No soak** — ≥8 capabilities default-off awaiting it | Requires production traffic, which requires users |
| **Zero latency assertions** (`PERF-BASELINE-1`) | Requires load, and a reason to care about a number |
| **No disaster-recovery drill** | Requires an operational history and an on-call rotation |
| **No external security review** | Requires an independent adversary; the escape suite is self-designed |
| **No cloud deployment manifests** | Requires a deployment target someone is paying for |
| **`TOOL-SEAM-ISOLATION-1` open at P0** | Requires the pressure of untrusted third-party code actually arriving |

**The pattern is exact.** What one designer plus generation produces quickly: code, tests,
documentation, design coherence, and a decision record. What it cannot produce at all: **traffic, an
operational history, an independent adversary, and a second opinion.** Those four are the entire
production-readiness gap priced at $1.3M in §11.3.

This is the honest counterweight to §17.2, and it should be read alongside it. The throughput is
real; so is what it skipped, and the two have the same cause.

### 17.6 What this does and does not change

**Does not change:** any figure in §1–§16. Those price a conventional counterfactual and that
counterfactual is unaffected by how the artifact was in fact produced. A replacement cost is what
*a replacer* would spend.

**Does change:** how the §8.5 AI-assisted counterfactual should be read. That model (×0.70 effort,
≈$4.7M for Scope A) describes **an AI-assisted professional team** — a team retaining its
coordination overhead, its review cycle and its role structure, and using agents within it. It is
not a model of what happened here, and it should not be quoted as one. The solo mode is a different
production function, not a faster version of the same one, and this report does not claim to have
calibrated it.

**Stated limits.** The ratio in §17.1 rests on **commit days**, the only quantity actually
observable. Hours worked, token expenditure, prompt volume and the number of discarded generations
are all unmeasurable from the repository, and none is inferred. **A reader who believes the days
were 4 hours or 16 hours should recompute; the report does not know.** What is not in doubt is the
denominator: 132 distinct days with at least one commit, across five repositories, 2025-04-11 →
2026-08-18, one author.

---

*Prepared 2026-08-19 against `aindy-runtime` HEAD `e9efcf7`. All quantitative claims are reproducible
from the repository with the commands and file references cited inline. No repository file was
modified in the course of this audit other than the creation of this report.*
