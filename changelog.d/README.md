# `changelog.d/` — one file per change

**Write your changelog entry as a new file here, not as an edit to `CHANGELOG.md`.**

The CHANGELOG protocol in `CLAUDE.md` is unchanged and still the point: *a PR that changes
behaviour, API surface, configuration, schema, or what CI proves writes its own entry, in the
same PR, while the reasoning is still in your head.* Only the **location** moved.

## Why

Editing a shared `## Unreleased` section means every concurrent PR collides at the same lines.
That happened three times in one afternoon (#449/#450/#451), and the failure mode is worse than
the annoyance suggests: the reflexive "keep mine" resolution **silently reverted another PR's
entry**, with nothing failing to catch it. A dropped changelog paragraph does not break a build.

A new file cannot conflict with another new file.

## How

Create `changelog.d/<PR-number>-<short-slug>.md` containing exactly what you would have written
under `## Unreleased`:

```markdown
### Fixed — the widget returned 500 instead of 409 (`WIDGET-1`, #123)

One sentence on what changed, and *why it was wrong* where that is not obvious.
```

- Start with a `### Added|Changed|Fixed|Removed — <short title> (#PR)` heading, exactly as
  before. The assembler does not rewrite your text; it concatenates.
- **Prefix the file `00-` if an operator must read it before upgrading.** Those sort first, which
  is what the protocol means by "at the top of `Unreleased`, not buried in a bullet".
- If you do not know the PR number yet, use the branch name and rename later — the number in the
  heading is what matters.

## At release

```bash
python scripts/assemble_changelog.py          # fold fragments into ## Unreleased
python scripts/assemble_changelog.py --check  # CI: verify none are stranded
```

Assembly is part of cutting a release, which is where `RELEASE_CHECKLIST.md` already puts
CHANGELOG *verification*. Fragments are deleted by the assembler once folded in.

**Do not hand-edit `## Unreleased` any more.** If you do, you reintroduce exactly the collision
this directory removes.
