# Archive

Point-in-time documents kept for audit trail. **Nothing here is maintained**, and nothing
here should be treated as describing the current system — each entry records what was true on
the date it was written. For current state, start at [`../../README.md`](../../README.md),
[`../../CLAUDE.md`](../../CLAUDE.md), and [`../../TECH_DEBT.md`](../../TECH_DEBT.md).

Archived 2026-08-05 from the repository root. Each had **zero inbound references** from any
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
