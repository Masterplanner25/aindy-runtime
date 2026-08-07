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
  would break live code comments.
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
