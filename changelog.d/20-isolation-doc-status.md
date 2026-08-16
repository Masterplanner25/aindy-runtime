### Fixed — `ISOLATION_MODEL_PLAN.md` contradicted itself (`ISOLATION-DOC-STATUS-1`, #458)

Line 6 said *"Planning — no implementation has begun"* while line 148 of the same file said
*"Scope B1 complete"* — and the sandbox runners, plugin host, certification surface and
nine-file escape suite were all built, wired, and passing 17/17 on every release tag.

**Why it survived:** the file lives at the **repository root**, outside `docs/runtime/`, so the
`Runtime Docs Validation` frontmatter and `last_verified` checks that catch exactly this never
looked at it.

The status now says implemented, and — deliberately, so the correction does not over-reach in
the other direction — states what is **not**: Tier-2 is certified on Linux only (`C3` open), and
the provider is reachable from a single seam (`TOOL-SEAM-ISOLATION-1`, `EXEC-ENV-BIND-1`).
