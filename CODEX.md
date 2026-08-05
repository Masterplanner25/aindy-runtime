# aindy-runtime — Codex Instructions

**Read [`CLAUDE.md`](CLAUDE.md).** It is the authoritative agent-instruction surface for this
repository, and everything that used to live here was a copy of part of it.

Also read
[`docs/platform/governance/AGENT_WORKING_RULES.md`](docs/platform/governance/AGENT_WORKING_RULES.md)
for the collaboration boundaries — what may change without approval, what needs sign-off, and
how to behave at a boundary you cannot resolve.

---

## Why this file is a pointer

It was a hand-maintained duplicate of `CLAUDE.md`, and it drifted. At the point it was
replaced (2026-08-05) every one of its eleven sections was already covered in `CLAUDE.md`,
while it carried **none** of the preceding two months of changes — checked against eight
representative facts, all present in `CLAUDE.md` and absent here:

| | in `CLAUDE.md` | was in `CODEX.md` |
|---|---|---|
| `transactional_email` connector type (FR-9) | yes | no |
| `reconcile_backfill` on populated tables (FR-8) | yes | no |
| `env_ignore_empty` empty-env guard (FR-10) | yes | no |
| `LOCKFILE-PLATFORM-1` | yes | no |
| `NATIVE-CI-1`, `MCP-SDK-2X-1` | yes | no |
| v2.0.1 release state | yes | no |

An agent following the stale copy would have used the wrong connector type, skipped the
backfill declaration on a new column, and not known that `npm ci` is the only meaningful
lockfile check. **A stale instruction file is more dangerous than a missing one**, because it
reads as authoritative.

Two files maintained by hand will diverge; the only question is when someone notices. So this
one holds no content. **Do not add rules here — add them to `CLAUDE.md`.**
