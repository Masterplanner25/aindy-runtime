"""DEC-010 — `docs/governance/DECISION_LOG.md` is the register of decisions; cited ids must exist.

Three places held decisions and none had a rule (#648). The rule is now DEC-010: a decision is
recorded as `DEC-NNN` in the log, in the PR that acts on it, and everything else cites the id.
This pins the half a guard can pin:

* every `DEC-NNN` cited anywhere under `AINDY/`, `docs/`, `tests/`, `CLAUDE.md`, `TECH_DEBT.md`
  or `CHANGELOG.md` is defined in the log — exactly once;
* ids in the log are unique and each entry carries a `**Status:**` line with a known status;
* the CLAUDE.md index lists every id defined at or after DEC-010 (the index is derived, never a
  hand census — variant 12) and nothing that is not defined.

It does NOT try to decide whether a `DECIDED` / `DECLINED` marker in prose "should" carry an id —
that judgement is the author's; if the markers drift, that is phase 2.
"""
from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.runtime_only

REPO = pathlib.Path(__file__).resolve().parents[2]
LOG = REPO / "docs" / "governance" / "DECISION_LOG.md"
INDEX_START = "### Recorded decisions — an INDEX of `docs/governance/DECISION_LOG.md`"
KNOWN_STATUSES = {"accepted", "provisional", "superseded", "declined", "needs review"}

_DEF = re.compile(r"^### (DEC-\d{3})\s*$", re.M)
_CITE = re.compile(r"\bDEC-\d{3}\b")
_STATUS = re.compile(r"^\*\*Status:\*\* `([^`]+)`", re.M)


def _defined() -> list[str]:
    return _DEF.findall(LOG.read_text(encoding="utf-8-sig"))


def _cited_everywhere() -> dict[str, set[str]]:
    roots = [REPO / "AINDY", REPO / "docs", REPO / "tests", REPO / "CLAUDE.md", REPO / "TECH_DEBT.md", REPO / "CHANGELOG.md"]
    cites: dict[str, set[str]] = {}
    for root in roots:
        files = [root] if root.is_file() else list(root.rglob("*.md")) + list(root.rglob("*.py"))
        for f in files:
            if "archive" in f.parts or f == LOG:
                continue
            for m in _CITE.findall(f.read_text(encoding="utf-8-sig", errors="ignore")):
                cites.setdefault(m, set()).add(f.relative_to(REPO).as_posix())
    return cites


def test_every_cited_decision_is_defined_exactly_once():
    defined = _defined()
    assert len(defined) >= 19, f"the log defines only {len(defined)} decisions; the parser is broken"
    dupes = {d for d in defined if defined.count(d) > 1}
    assert not dupes, f"duplicate ids in the log: {sorted(dupes)}"
    missing = {k: sorted(v) for k, v in _cited_everywhere().items() if k not in set(defined)}
    assert not missing, f"decisions cited but not defined in DECISION_LOG.md: {missing}"


def test_every_entry_has_a_known_status():
    text = LOG.read_text(encoding="utf-8-sig")
    blocks = _DEF.split(text)[1:]  # [id, body, id, body, …]
    for dec_id, body in zip(blocks[0::2], blocks[1::2]):
        m = _STATUS.search(body)
        assert m, f"{dec_id} has no **Status:** line"
        assert m.group(1) in KNOWN_STATUSES, f"{dec_id} has unknown status {m.group(1)!r}"


def test_the_claude_md_index_is_derived_from_the_log():
    """Every id from DEC-010 on is indexed, and the index names nothing undefined. DEC-001..009 are
    the founding principles and are pointed at, not listed."""
    claude = (REPO / "CLAUDE.md").read_text(encoding="utf-8-sig")
    start = claude.index(INDEX_START)
    end = claude.index("### Standing rule — not an item", start)
    indexed = set(_CITE.findall(claude[start:end]))
    defined = set(_defined())
    expected = {d for d in defined if int(d.split("-")[1]) >= 11}
    assert expected <= indexed, f"defined but not indexed in CLAUDE.md: {sorted(expected - indexed)}"
    assert indexed <= defined, f"indexed but not defined: {sorted(indexed - defined)}"
