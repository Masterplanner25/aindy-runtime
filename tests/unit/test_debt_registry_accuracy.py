"""The `TECH_DEBT.md` prefix registry in CLAUDE.md must not file closed items as open.

CLAUDE.md's registry is an index: one line per debt item, grouped under `### Open — P0/P1/P2`
and `### Closed — ...` headings. It is the first thing anyone reads to answer "what is open?".

★ **It drifted, and the drift was invisible.** After a week of closures, six entries whose own
text began `**CLOSED 2026-08-16 (#458)**` or `**FIXED 2026-08-16 (#466)**` were still filed under
`### Open — P0` and `### Open — P2`. One line still read "D open" two days after D merged.
Anyone scanning for open work got a materially wrong answer, and nothing said so.

That is the same failure this repo catalogues elsewhere — an index asserting one thing while its
entries assert another. `ISOLATION-DOC-STATUS-1` was literally a document contradicting itself
between line 6 and line 148.

**What this checks, and what it deliberately does not.** It only fires when a bullet under an
`### Open` heading *opens* with a closure marker — `CLOSED`/`FIXED`/`DONE` followed by a date.
That is unambiguous. It does **not** try to parse the prose for partial closure: entries like
`IDEM-11` legitimately describe closed halves while remaining open, and a checker that guessed at
those would produce false positives and be disabled within a month.
"""
from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.runtime_only

_CLAUDE = pathlib.Path(__file__).resolve().parents[2] / "CLAUDE.md"

# `**CLOSED 2026-08-16 ...`, `**FIXED 2026-08-16 ...`, `**CLOSED (2026-08-16)`, etc., appearing
# at the START of the entry's description — i.e. the entry's own headline verdict.
_CLOSURE_HEADLINE = re.compile(
    r"^- \*\*(?P<name>[A-Za-z0-9.\-]+)\*\*\s+—\s+\*\*(?:CLOSED|FIXED|RESOLVED)\b[^*]*\d{4}-\d{2}-\d{2}"
)


def _registry_sections() -> dict[str, list[str]]:
    """Map each `### Open …` / `### Closed …` heading to the entry lines beneath it."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in _CLAUDE.read_text(encoding="utf-8").split("\n"):
        if line.startswith("### "):
            current = line[4:].strip() if line.startswith(("### Open", "### Closed")) else None
            if current:
                sections[current] = []
        elif current and line.startswith("- **"):
            sections[current].append(line)
    return sections


def test_the_registry_is_parseable():
    """Liveness. If the heading shape changes, every check below passes on an empty set."""
    sections = _registry_sections()

    open_entries = sum(len(v) for k, v in sections.items() if k.startswith("Open"))
    assert any(k.startswith("Open — P0") for k in sections), "no P0 section found"
    assert open_entries >= 15, f"only {open_entries} open entries parsed — the scan is broken"


def test_no_open_entry_headlines_itself_as_closed():
    """★ The drift this exists to catch.

    An entry whose own first claim is "CLOSED 2026-08-16" is not open, whatever heading it sits
    under. Move it to a `### Closed` section rather than relaxing this test — the point of the
    index is that it can be trusted at a glance.
    """
    misfiled = [
        f"{_CLOSURE_HEADLINE.match(line).group('name')} (under '{heading}')"
        for heading, entries in _registry_sections().items()
        if heading.startswith("Open")
        for line in entries
        if _CLOSURE_HEADLINE.match(line)
    ]

    assert not misfiled, (
        "these entries are filed under an Open heading while their own text opens with a "
        f"closure verdict: {misfiled}. Someone scanning 'what is open' gets a wrong answer."
    )


def test_closed_sections_are_not_empty():
    """The counterpart: closures have to land somewhere, or they are being deleted instead."""
    sections = _registry_sections()
    closed = [k for k in sections if k.startswith("Closed")]

    assert closed, "no Closed section in the registry"
    assert sum(len(sections[k]) for k in closed) >= 5, (
        "the Closed sections are nearly empty, which means closures are being dropped rather "
        "than filed — the history is the point"
    )
