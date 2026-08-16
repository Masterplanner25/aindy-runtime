#!/usr/bin/env python3
"""Fold `changelog.d/` fragments into `CHANGELOG.md`'s `## Unreleased` section.

Each PR writes a new file rather than editing a shared section, so concurrent PRs cannot
collide. The CHANGELOG protocol in `CLAUDE.md` is unchanged — entries are still authored in the
PR that makes the change; only their location moved until release.

Usage::

    python scripts/assemble_changelog.py            # fold fragments in and delete them
    python scripts/assemble_changelog.py --check    # exit 1 if any fragment is unfolded
    python scripts/assemble_changelog.py --dry-run  # print what would be written

Ordering is by filename, so a `00-` prefix pins an operator-must-read entry to the top — which
is what the protocol means by "not buried in a bullet".
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
FRAGMENT_DIR = ROOT / "changelog.d"
CHANGELOG = ROOT / "CHANGELOG.md"
UNRELEASED = "## Unreleased"
PLACEHOLDER = "_Nothing yet._"


def fragments() -> list[pathlib.Path]:
    """Fragment files, in filename order. `README.md` is documentation, not an entry."""
    if not FRAGMENT_DIR.is_dir():
        return []
    return sorted(p for p in FRAGMENT_DIR.glob("*.md") if p.name.lower() != "readme.md")


def _render(paths: list[pathlib.Path]) -> str:
    blocks = []
    for path in paths:
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        blocks.append(text)
    return "\n\n".join(blocks)


def assemble(*, dry_run: bool = False) -> int:
    found = fragments()
    if not found:
        print("no changelog fragments to assemble.")
        return 0

    body = _render(found)
    if not body:
        print(f"{len(found)} fragment(s) were empty; nothing to fold in.")
        return 0

    text = CHANGELOG.read_text(encoding="utf-8")
    if UNRELEASED not in text:
        print(f"error: {CHANGELOG.name} has no '{UNRELEASED}' heading.", file=sys.stderr)
        return 2

    head, _, tail = text.partition(UNRELEASED)
    # Drop the placeholder if the section is otherwise empty, so we do not end up with
    # "_Nothing yet._" sitting above real entries.
    tail = tail.replace(f"\n\n{PLACEHOLDER}\n", "\n", 1)
    updated = f"{head}{UNRELEASED}\n\n{body}\n{tail}"

    if dry_run:
        print(f"would fold {len(found)} fragment(s) into {UNRELEASED}:")
        for path in found:
            print(f"  {path.name}")
        return 0

    CHANGELOG.write_text(updated, encoding="utf-8", newline="\n")
    for path in found:
        path.unlink()
    print(f"folded {len(found)} fragment(s) into {UNRELEASED} and removed them:")
    for path in found:
        print(f"  {path.name}")
    return 0


def check() -> int:
    """Fail if fragments are still present.

    Intended for the release flow, not for every PR — during normal development fragments are
    *supposed* to exist. Running this on every commit would invert the whole design.
    """
    found = fragments()
    if not found:
        print("ok: no unfolded changelog fragments.")
        return 0
    print(
        f"error: {len(found)} changelog fragment(s) are not folded into {CHANGELOG.name}.\n"
        "Run: python scripts/assemble_changelog.py",
        file=sys.stderr,
    )
    for path in found:
        print(f"  {path.name}", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if any fragment is unfolded (for the release flow, not per-PR CI).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be folded in without writing.",
    )
    args = parser.parse_args()

    if args.check:
        return check()
    return assemble(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
