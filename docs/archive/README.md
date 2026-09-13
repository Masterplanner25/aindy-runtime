# Archive

Point-in-time documents kept for audit trail. **Nothing here is maintained**, and nothing
here should be treated as describing the current system — each entry records what was true on
the date it was written. For current state, start at [`../../README.md`](../../README.md),
[`../../CLAUDE.md`](../../CLAUDE.md), and [`../../TECH_DEBT.md`](../../TECH_DEBT.md).

Archived 2026-08-05 from the repository root and from `docs/runtime/`. Each had **zero inbound references** from any
tracked file — read by nothing, maintained by no one, sitting alongside living documents.

Two of them (`AINDY_ORIENTATION.md`, `RUNTIME_SIGNOFF.md`) were previously **gitignored** and
existed only on the maintainer's machine. They are committed here rather than left out, so the
audit trail is complete for everyone rather than for one workstation.

| Document | Written | What it was | Superseded by |
|---|---|---|---|
| [`AINDY audit 6_6_26_237pm.md`](<AINDY audit 6_6_26_237pm.md>) | 2026-06-06 | A system-classification audit scoring the runtime across maturity axes with evidence. The timestamp in the filename is the giveaway: a snapshot, never intended as a living document. | `TECH_DEBT.md` for open items; `docs/runtime/ARCHITECTURE_RISK.md` for complexity and blast-radius assessment |
| [`AINDY_RUNTIME_MATURITY.md`](AINDY_RUNTIME_MATURITY.md) | 2026-06-04 | A maturity rubric evaluating the runtime as an OS-like platform. Linked *out* to the 90-day checklist; nothing linked *in*. | `AINDY_RUNTIME_90_DAY_CHECKLIST.md` (still at root, still maintained) |
| [`AINDY_ORIENTATION.md`](AINDY_ORIENTATION.md) | 2026-05-23 | A full re-orientation pass over the runtime and the apps boundary, written just after the repo split. Was gitignored until 2026-08-05; nothing tracked ever cited it. | `README.md` and `docs/runtime/RUNTIME_MODULE_MAP.md` for current structure |
| [`RUNTIME_SIGNOFF.md`](RUNTIME_SIGNOFF.md) | 2026-05-17 | The extraction sign-off for the runtime/apps split — records the smoke check passing at 17 tests. Cited only by `AINDY_ORIENTATION.md`, which was itself uncited. | superseded by CI; `Runtime Contracts` is the live equivalent |

## From `docs/runtime/` — archived 2026-08-05

Nine documents, each with **zero inbound references** from any tracked file. Seven share a
single date, 2026-06-06: one audit session whose output was written into the reference
docset rather than alongside it. They diluted 93 living documents down to 84.

| Document | Written | What it was |
|---|---|---|
| [`AUTH_SYSTEM_AUDIT.md`](AUTH_SYSTEM_AUDIT.md) | 2026-06-06 | Point-in-time audit of the auth surface — predates the 2.0.0 auth rework entirely (purpose claim, 202 register, recovery routes) |
| [`PLATFORM_PURITY_AUDIT.md`](PLATFORM_PURITY_AUDIT.md) | 2026-06-06 | Runtime/app boundary purity check |
| [`PLATFORM_READINESS_AUDIT.md`](PLATFORM_READINESS_AUDIT.md) | 2026-06-06 | Readiness assessment |
| [`REAL_USER_REALITY_AUDIT.md`](REAL_USER_REALITY_AUDIT.md) | 2026-06-06 | End-user reality check |
| [`SYSTEM_CAPABILITY_AUDIT.md`](SYSTEM_CAPABILITY_AUDIT.md) | 2026-06-06 | Capability inventory |
| [`SYSTEM_INTEGRITY_AUDIT.md`](SYSTEM_INTEGRITY_AUDIT.md) | 2026-06-06 | Integrity assessment |
| [`SYSTEM_LIMIT_LEVERAGE_AUDIT.md`](SYSTEM_LIMIT_LEVERAGE_AUDIT.md) | 2026-06-06 | Limits and leverage analysis |
| [`RUNTIME_DOCSET_STATUS_AUDIT.md`](RUNTIME_DOCSET_STATUS_AUDIT.md) | 2026-05-31 | Status audit of the docset itself |
| [`FR6_PASSWORD_RECOVERY_SCOPE.md`](FR6_PASSWORD_RECOVERY_SCOPE.md) | 2026-08-02 | Build scope for FR-6 password recovery — **the work shipped in 2.0.0**, so this is a completed plan |

**On the FR-6 scope doc specifically.** It carried two real design findings, so it was
checked rather than assumed disposable — both survive outside it:

- *A reset token would otherwise be a valid access token.* The mitigation is
  domain-separated signing keys, not merely a `purpose` claim. Recorded in `CHANGELOG.md`,
  in `docs/platform/governance/INVARIANTS.md` §(21), and load-bearing in the source itself
  (`PASSWORD_RESET_DOMAIN`, `_derive_domain_key` in `auth_service.py`).
- *The duplicate-registration timing side channel.* Recorded in `TECH_DEBT.md` and
  `CLAUDE.md`.

Nothing unique was lost. For current behaviour read the source and `INVARIANTS.md`, not this.

## Second `docs/runtime/` pass — archived 2026-08-06

Four more, found by a sharper test than the first pass used: **inbound references that are
not index listings**. An index cites everything, so a citation from one is not evidence a
document is used.

| Document | Written | Why |
|---|---|---|
| [`EXECUTION_AUDIT.md`](EXECUTION_AUDIT.md) | 2026-05-17 | The oldest document in the docset. Its **only** referrer was `RUNTIME_DOCSET_STATUS_AUDIT.md` — which is itself archived, so it was cited only by something nobody reads. |
| [`USER_WALKTHROUGH_LOG.md`](USER_WALKTHROUGH_LOG.md) | 2026-06-12 | An operator onboarding issue *log* — point-in-time by nature. Its only citation is the CHANGELOG entry announcing its creation, not a live pointer. |
| [`KERNEL_CAPABILITY_AUDIT.md`](KERNEL_CAPABILITY_AUDIT.md) | 2026-06-12 | Same shape: a CHANGELOG announcement plus an index listing, nothing substantive. |
| [`APP_HANDOFF_v1.11.0.md`](APP_HANDOFF_v1.11.0.md) | 2026-08-01 | Superseded — referenced only by `APP_HANDOFF_v2.0.0.md`. Versioned handoffs are audit trail; the current one stays in `docs/runtime/`. |

### Two that were checked and deliberately kept

- **`C3_NON_LINUX_STRONG_SANDBOX_PLAN.md`** — nearly archived on the strength of a CLAUDE.md
  line claiming C3 was closed. It is not: `TECH_DEBT.md` records an open remaining gap
  (strong-sandbox is Linux-only) and calls this document live preparation *"so either track
  can start the day a trigger lands"*. That CLAUDE.md line was wrong and is corrected in the
  same change.
- **`QUICKSTART.md`** — inbound references are the wrong signal for an entry-point document;
  people find it by name, not by link. Checking it surfaced a genuine problem in the other
  direction: nothing linked to it at all. Now linked from `README.md` and the doc index.

**Method note.** The first pass asked "does anything reference this?". The second asked "does
anything *with an opinion* reference this?" — excluding indexes, and treating a CHANGELOG
entry that merely announces a file's creation as historical rather than a live pointer. That
distinction is what separated these four from the 80 that stayed.

## Why these four and not the others

Three documents of the same vintage stayed at the repository root, because something still
points at them:

- `IDEMPOTENCY_AUDIT.md` and `ISOLATION_MODEL_PLAN.md` are cited from **source code** —
  `AINDY/db/models/effect_record.py`, `alembic/versions/0002_*` and `0003_*`, and
  `AINDY/platform_layer/sandbox_runner.py` all name them, by bare filename. Moving them
  would break live code comments. *(Superseded for the audit on 2026-09-13 — see below:
  those citations are provenance, not dependency, and provenance is what an archive holds.)*
- `C2_SANDBOX_AUDIT.md` is cited from `TECH_DEBT.md` and `ISOLATION_MODEL_PLAN.md`.

All three were **gitignored until 2026-08-05** — so those citations pointed at files no clone
contained. They are committed now, at the root paths the citations already assume.
- `AINDY_RUNTIME_90_DAY_CHECKLIST.md` is linked from `CLAUDE.md`,
  `docs/runtime/DECISION_LOG.md`, and the runtime doc index.

Age is not the signal. **Inbound references are** — check them before archiving anything
else:

```bash
git grep -l -F "FILENAME.md" -- '*.py' '*.md' '*.yml' | grep -v '^docs/archive/'
```

## Repository root — archived 2026-09-13

One document, and the reason is different from every entry above: `RTR.md` was **not**
unreferenced. It was listed in `CLAUDE.md`'s key-file table as *"Roadmap reading aid (digest
of `TECH_DEBT.md`; NOT the source of truth)"*. It was archived because the thing it digested
now has its own reading aid, and a second one that is not maintained is a liability.

| Document | Written | What it was | Superseded by |
|---|---|---|---|
| [`RTR.md`](RTR.md) | 2026-07-11, tracked 2026-08-01 (#319), last verified 2026-08-13 | A one-page digest of open runtime work — "genuinely open, most remaining work first", the soak-then-flip flag list, and a release note for v1.11.0. | `CLAUDE.md` → *TECH_DEBT.md — prefix registry*, which is one line per item, CI-enforced against regrowth and against closed entries sitting under `### Open`. `TECH_DEBT.md` remains the source. |

**Why now.** It self-described as a digest that *"goes stale fast"* and was right. Its own
history shows the shape: it was frozen at 2026-07-11 with six shipped items marked open when
it was first tracked (#319), and two more wrong claims (NATIVE-CI-1 open, vite as the UI
blocker) were found and corrected on 2026-08-13 (#398). One month on, at 2026-09-13:

- **The headline is wrong.** *"C3's non-Linux strong sandbox is the only genuine big rock
  left"* — the registry now carries `FR-15` at P0 and `FLOW-PARALLEL-1`,
  `AUTHORITY-NEGOTIATION-1`, `FS-SCOPE-1`, `SUBSTRATE-WITNESS-1`, `PERF-BASELINE-1` at P1,
  none of which it names.
- **Closed items are listed as open.** `MEM-RECALL-N1-1` (closed 2026-08-16) and
  `DEP-UPGRADE-DEFERRED-1`'s OTel + UI cluster (both closed) are in its "Dependency / CI
  debt" section.
- **The release section describes v1.11.0 as being prepared.** The current release is
  2.13.0, twelve releases later.
- **The one section that mostly survives** — the soak-then-flip flag list — is stale in one
  entry (`AINDY_NODUS_WARM_POOL` has defaulted **on** since 2026-08-19, not "off in
  production") and is otherwise covered by `CLAUDE.md`'s *SOAK HAPPENS HERE* paragraph,
  which also corrects the premise: those flags were waiting on app-side traffic that was
  never going to arrive, and the soak apparatus now lives in this repo.

**What was not done, deliberately.** The document was not corrected a third time. Each prior
correction was accurate the day it was written and decayed identically — the same class
`CLAUDE.md`'s release-state paragraph removed its version numbers for. A digest maintained
by hand beside a CI-enforced index is the "hand-copied list … slower, wrong second copy" that
the Alembic-head note in `CLAUDE.md` describes; the fix is one fewer copy, not a fresher one.

The `TECH_DEBT.md` mention of `RTR.md` (in the closed native-bridge doc-verification entry)
is historical narrative and stays as written.

## Repository root — archived 2026-09-13 (second)

| Document | Written | What it was | Superseded by |
|---|---|---|---|
| [`IDEMPOTENCY_AUDIT.md`](IDEMPOTENCY_AUDIT.md) | 2026-05-23, last merged 2026-05-24 | The idempotency and invariants audit: 8 findings (`IDEM-1..8`) against the schema-bootstrap, syscall-registry, scheduler and DB-constraint surfaces, plus 5 "new findings" (`NF-1..5`) that proposed the effect-level layer — a persistent `EffectRecord`, a deterministic `action_id`, a declared execution guarantee, and a gate at the syscall boundary. | `docs/runtime/IDEMPOTENCY_CONTRACT.md` for what the runtime guarantees now; `TECH_DEBT.md` `IDEM-*` entries for the findings' lifecycle. Every `IDEM` and `NF` item is closed (Alembic `0002`/`0003`, the MEB program, `IDEM-11`'s default-on gate). |

**Why it was kept on 2026-08-06 and archived now.** The earlier pass kept it because three
source files cite it by bare filename — `effect_record.py`, `alembic/versions/0002_*` and
`0003_*` — and reasoned that moving it *"would break live code comments"*. That conflated two
things. Those citations say *"this migration closes IDEM-5 from the audit"* and *"this table
closes NF-1"*: they record **where the code came from**, not something the code needs to be
current. A migration frozen at 2026-05-23 citing an audit frozen at 2026-05-23 is provenance,
and provenance is precisely what this directory holds. Source code citing a months-old audit
is not a reason to keep the audit live; it is a reason to keep the audit *at all*.

**What was updated, and what deliberately was not.** The two Alembic docstrings now cite
`docs/archive/IDEMPOTENCY_AUDIT.md`. The `effect_record.py` docstring keeps the bare
filename: that file is under `AINDY/db/models/`, whose raw bytes are content-hashed by the
schema contract, so a one-word docstring edit would cost a `SCHEMA_CONTRACT_VERSION` bump,
a baseline regen and two test-assertion edits for a change with no DDL — the trap
`AGENT-EVENT-VOCAB-1` records. `git grep IDEMPOTENCY_AUDIT` still resolves the bare name to
this directory. The `TECH_DEBT.md` mention (in the closed `IDEM-9` entry) is historical and
stays as written.

## Repository root — archived 2026-09-13 (third)

| Document | Written | What it was | Superseded by |
|---|---|---|---|
| [`ISOLATION_MODEL_PLAN.md`](ISOLATION_MODEL_PLAN.md) | 2026-05-23; status corrected 2026-08-16 (`ISOLATION-DOC-STATUS-1`) | The plan behind the **Tiered Isolation Contract** (Tier 1 trusted-operator kernel-resident / Tier 2 externalized): seven gaps, a work plan of seven items (A1–A3 docs, B1–B2 code+tests, C1–C2 deferred), and the rationale for choosing a two-tier model over a third "capability-confined in-process" class. | `docs/runtime/EXTENSION_TRUST_MODEL.md` (the contract), `AINDY/platform_layer/extension_execution_model.py` (the two classes, published), `docs/runtime/C3_NON_LINUX_STRONG_SANDBOX_PLAN.md` (the live remainder), `docs/runtime/SANDBOX_ESCAPE_AUDIT.md` (the evidence, per release). |

**Is the plan complete?** Yes — verified per item against source on 2026-09-13, not read
from the plan's own status notes (which contradicted each other once already):

- **A1, A2, B1, B2 — done and still true.** Zero occurrences of the retired "exception"
  vocabulary; both tiers named in the three target docs; `extension_execution_model.py`
  publishes exactly two classes; `test_runtime_public_contract.py` passes with no reference
  to the removed third class.
- **A3 — done, then its target section was deleted.** The Tier 1 attestation-exclusion
  paragraph was written into `EXTENSION_TRUST_MODEL.md` §Assurance Reporting, and the
  2026-05-31 docset reconciliation (`99c4b90`) removed that whole section. The substance
  survives in §Tier 1 Trusted Kernel Code (*"trusted because the operator controls and
  deploys it, not because it is sandboxed at execution time"*). **Side finding, not this
  plan's:** the glossary for *assurance class / attestation / certification tier* went with
  that section and was not re-homed — for three and a half months the terms were defined
  only as constants in `sandbox_runner.py` and used across four live docs without a prose
  definition. **Restored 2026-09-13**, rewritten against the current constants rather than
  pasted back from the diff (the 05-31 version predates the assurance ceiling and the
  kernel-observable verification method).
- **C1 — deferred by decision, residual disclosed.** Scope B1 shipped (unprivileged `/proc`
  evidence; `verification_method: kernel-observable`, ceiling `kernel-observable-verified` on
  Linux). Scope B2 — a privileged launcher — has a stated reopen condition and, by the plan's
  own text, *"no current condition to reopen"*. It was never filed as an issue, correctly.
- **C2 — closed 2026-05-24.** Its Linux-only remainder is `C3`, tracked in the registry.

**Live citation retained on purpose.** `sandbox_runner.py`'s `ceiling_note` — a
runtime-emitted string in the sandbox posture report, not a docstring — says *"See Gap C1 in
ISOLATION_MODEL_PLAN.md"* when kernel-observable evidence has not yet been collected. That
pointer is doing real work for an operator reading the posture output, so it was updated to the
archive path rather than removed; no test pins the string. `C2_SANDBOX_AUDIT.md` (still at the
root) cites this plan by bare name twice and is left as-is. `TECH_DEBT.md`, `CHANGELOG.md` and
`AINDY_RUNTIME_REPLACEMENT_COST_AUDIT.md` mentions are historical.

## Repository root — archived 2026-09-13 (fourth)

| Document | Written | What it was | Superseded by |
|---|---|---|---|
| [`C2_SANDBOX_AUDIT.md`](C2_SANDBOX_AUDIT.md) | 2026-05-24 | The audit that closed `C2` — eight findings (`NF-1..8`) on why `container-sandbox-certified` was unreachable off Linux when the container backend delivered Linux semantics anyway, four open operational questions, and a verification strategy. Its "What This Audit Does NOT Cover" section is where `C3` was born. | `TECH_DEBT.md` `C2` (closed 2026-05-24, live-verified on Windows + Docker Desktop) and `C3` (the tracked remainder); `docs/runtime/EXTENSION_TRUST_MODEL.md` §Available Platform Sandbox Mechanism Matrix and §Container-Backed Third-Party Plugin Isolation Semantics; `docs/runtime/C3_NON_LINUX_STRONG_SANDBOX_PLAN.md`. |

**Verified per item on 2026-09-13.** `NF-1..7` are in `sandbox_runner.py` —
`_detect_linux_container_backend()` (`docker info` → `OSType`), the
`linux_container_backend_available` parameter on `_platform_matrix_entry`, and a dynamic
`production_safe_third_party_supported_host_platforms` key beside the static Linux-only
constant, which was deliberately left narrow. `NF-5`'s non-Linux certification tests are in
`test_sandbox_runner.py`. `NF-2` and `NF-8`'s doc sections exist under the names the
2026-05-31 reconciliation gave them (*Container-Backed … Semantics*, *Available Platform
Sandbox Mechanism Matrix*).

**The four open questions were answered by the implementation rather than in writing:**
Q1 — re-detect on every matrix call, no cache; Q2 — fail closed with an `operator_note`, and
**no** `ASSUME_LINUX_BACKEND` escape hatch was added; Q3 — no `backend_host_platform` field
by that name, but `detection_method`, `os_type` and `current_wsl2_detection` expose the same
distinction; Q4 — `_detect_wsl2()` (C3 phase 1) separates *running inside WSL2* from
*Windows talking to a WSL2 backend*, and `MACOS_CONTAINER_POLICY.md` covers the third case.

**One recommendation was never actioned, and is recorded here rather than filed:** the
audit's *After This Audit* section proposed `docs/runtime/SANDBOX_CONTRACT.md` — a peer to
`EXECUTION_CONTRACT.md` and `IDEMPOTENCY_CONTRACT.md` with the sandbox guarantees as
numbered invariants. For three and a half months it did not exist and those guarantees
were prose spread across `EXTENSION_TRUST_MODEL.md`, `SECURITY_MATRIX.md`,
`OS_ISOLATION_LAYER.md` and the threat model in `SANDBOX_ESCAPE_AUDIT.md`. **Written
2026-09-13**, the same day this audit was archived — sixteen invariants, each with its
enforcement point and pin, and a §7 that states what is *not* guaranteed so it cannot be
inferred from silence.

The two `Source:` pointer lines in `TECH_DEBT.md` (`C2`, `C3`) now cite the archive path;
the narrative mention in `ISOLATION-DOC-STATUS-1` stays as written.
