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
`EXEC-ENV-BIND-1` (`PHASES 1+2 SHIPPED …; open for 3-4`) legitimately describes a closed half
while remaining open, and a checker that guessed at those would produce false positives and be
disabled within a month.

★ **The word list is the weak part, and it has already failed once.** On 2026-08-20 two entries
sat under `Open — P0` whose own headlines read `FLIPPED ON 2026-08-19` and `A+B+C1+C2 ALL
SHIPPED 2026-08-19` — both closed, both invisible here, because the pattern knew only
`CLOSED|FIXED|RESOLVED`. The same person wrote the entries and this guard months apart and
simply reached for different words. **A check that matches a vocabulary is only as complete as
the vocabulary someone happens to use.** When it misses one, add the word — do not reword the
entry to suit the regex; the entries are evidence about how people actually write.

★ **Residual, stated because the mutation run measured it: a QUALIFIED prefix still escapes.**
`A+B+C1+C2 ALL SHIPPED 2026-08-19` goes green here — so of the two entries that drifted, this
widening would have caught one. That is the no-guessing boundary holding, not a bug: a regex
cannot tell `A+B+C1+C2 ALL SHIPPED` (all of them) from `PHASES 1+2 SHIPPED` (some of them), and
the false-positive direction is the one that gets a check deleted. **The entry was reworded to
lead with `CLOSED` instead** — which is the right resolution when the guard cannot decide, and
the reason the registry convention asks for a leading verdict at all.

*(The docstring also claimed `DONE` was covered when the pattern never included it. It is now.
A sentence describing a regex is a second copy of that regex, and it drifted the same way.)*
"""

from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.runtime_only

_CLAUDE = pathlib.Path(__file__).resolve().parents[2] / "CLAUDE.md"

# `**CLOSED 2026-08-16 ...`, `**FIXED 2026-08-16 ...`, `**CLOSED (2026-08-16)`, etc., appearing
# at the START of the entry's description — i.e. the entry's own headline verdict.
# `ALL` is the only permitted qualifier, because it strengthens the claim. Anything else in
# front (`PHASES 1+2 SHIPPED`, `(b) and (c) shipped`) is a PARTIAL closure this must not judge.
_CLOSURE_WORDS = ("CLOSED", "FIXED", "RESOLVED", "DONE", "SHIPPED", "FLIPPED")
_CLOSURE_HEADLINE = re.compile(
    r"^- \*\*(?P<name>[A-Za-z0-9.\-]+)\*\*\s+—\s+\*\*(?:ALL\s+)?(?:"
    + "|".join(_CLOSURE_WORDS)
    + r")\b[^*]*\d{4}-\d{2}-\d{2}"
)


def _registry_sections() -> dict[str, list[str]]:
    """Map each `### Open …` / `### Closed …` heading to the entry lines beneath it."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in _CLAUDE.read_text(encoding="utf-8").split("\n"):
        if line.startswith("### "):
            current = (
                line[4:].strip()
                if line.startswith(("### Open", "### Closed"))
                else None
            )
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
    assert open_entries >= 15, (
        f"only {open_entries} open entries parsed — the scan is broken"
    )


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


# ---------------------------------------------------------------------------
# Entry size — the registry's own "one line per item" rule, enforced.
# ---------------------------------------------------------------------------
#
# ★ Stating the rule three times did not work. Measured across the file's history:
#
#   trimmed to  66 KB  ->  98.8 KB within a week
#   #487 "re-trim"     ->  reported -14,936 B; the file went 96,913 -> 115,234 B
#
# That last one is the instructive failure, and it was not dishonesty: six entries really were
# trimmed. The delta was measured **over the entries touched**, while the same commit added
# several new ones. Nothing contradicted it, because nothing measured the file.
#
# So the guard is per-entry, not a whole-file budget. A file budget can be satisfied by deleting
# an unrelated entry; a per-entry cap can only be satisfied by the entry that broke it.
#
# The caps are the current high-water mark, NOT an endorsement of that length. They are a
# ratchet: they stop the next entry from exceeding the worst one already here. Ratchet them
# **down** in a dedicated pass. Never raise one to accommodate a new entry — 79 of 91 entries
# already have a larger record in `TECH_DEBT.md`, so the detail has somewhere to go, and an
# entry that cannot be trimmed without loss is one whose text was never indexed anywhere.

_MAX_ENTRY_BYTES = 1150
_MAX_CLOSED_ENTRY_BYTES = 850


def _cap_for(heading: str) -> int:
    return _MAX_CLOSED_ENTRY_BYTES if heading.startswith("Closed") else _MAX_ENTRY_BYTES


def test_no_registry_entry_exceeds_its_size_cap():
    """★ One line per item. Detail belongs in `TECH_DEBT.md`, which is 6x this file."""
    oversized = [
        (heading, line[:60], len(line), _cap_for(heading))
        for heading, entries in _registry_sections().items()
        for line in entries
        if len(line) > _cap_for(heading)
    ]

    assert not oversized, (
        "registry entries over the size cap — move the detail into `TECH_DEBT.md` and leave the "
        "status, the hook and the pointer here. Do NOT raise the cap: "
        + "; ".join(f"{h}: {s}… is {n}B > {cap}B" for h, s, n, cap in oversized)
    )


def test_the_size_cap_is_a_ratchet_not_a_ceiling_we_are_far_below():
    """Liveness control for the cap.

    ★ A cap set far above the real distribution passes for years without ever being the
    reason anything is short — the `Upgrade Path Guard` failure mode (variant 9: green because
    there was nothing to catch). This asserts the cap is still *near* the data it governs, so
    the test above is doing work. If this fails because everything got much shorter, that is
    the moment to ratchet the caps down — which is the intended maintenance action, not a
    reason to delete this test.
    """
    sections = _registry_sections()
    largest_open = max(
        (len(line) for h, e in sections.items() if h.startswith("Open") for line in e),
        default=0,
    )
    largest_closed = max(
        (
            len(line)
            for h, e in sections.items()
            if h.startswith("Closed")
            for line in e
        ),
        default=0,
    )

    assert largest_open > _MAX_ENTRY_BYTES * 0.7, (
        f"largest open entry is {largest_open}B against a {_MAX_ENTRY_BYTES}B cap — the cap is "
        "no longer close to the data. Ratchet it down."
    )
    assert largest_closed > _MAX_CLOSED_ENTRY_BYTES * 0.7, (
        f"largest closed entry is {largest_closed}B against a {_MAX_CLOSED_ENTRY_BYTES}B cap — ratchet it down."
    )


def test_the_registry_does_not_take_over_the_file():
    """The failure the caps exist to prevent, stated as the outcome rather than the mechanism.

    Per-entry caps bound each line; nothing bounds the *count*. The registry reached 68% of
    CLAUDE.md once by growing in both directions at once, and at that size the file stops being
    an orientation document. This is deliberately loose — it is a backstop, and the per-entry
    cap is the working control.

    ★ Renamed 2026-08-20 from `..._stays_a_minority_of_the_file`, which asserted more than it
    checked: the bound is 60%, so the test passed happily at 51% — a majority. A name that
    overstates its check is read as a guarantee by everyone who greps for one and never opens
    it, which is the same defect as this module's docstring claiming `DONE` was covered when
    the pattern never included it. If you want a real minority bound, lower the number; do not
    restore the name.
    """
    text = _CLAUDE.read_text(encoding="utf-8")
    lines = text.split("\n")
    start = next(
        i
        for i, ln in enumerate(lines)
        if ln.startswith("## TECH_DEBT.md — prefix registry")
    )
    end = next(
        i for i, ln in enumerate(lines[start + 1 :], start + 1) if ln.startswith("## ")
    )
    registry = len("\n".join(lines[start:end]))

    share = registry / len(text)
    assert share < 0.60, (
        f"the registry is {share:.0%} of CLAUDE.md ({registry:,}B of {len(text):,}B). It is an "
        "index; `TECH_DEBT.md` is the record. Move detail down rather than raising this bound."
    )
