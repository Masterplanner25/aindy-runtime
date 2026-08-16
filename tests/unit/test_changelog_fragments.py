"""The `changelog.d/` assembler — the mechanism that stops concurrent PRs colliding.

Editing a shared `## Unreleased` section made every concurrent PR collide at the same lines
(#449/#450/#451 in one afternoon). The failure mode was worse than the annoyance: the reflexive
"keep mine" resolution **silently reverted another PR's entry**, and a dropped changelog
paragraph does not break a build.

These pin the two properties that matter — nothing is lost, and operator-must-read entries stay
on top — plus the ordering rule the protocol depends on.

The assembler runs at release, not per-PR: during development fragments are *supposed* to exist,
so a per-commit `--check` would invert the design. `test_check_mode_is_release_only_semantics`
records that intent so nobody wires it into the wrong job.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

pytestmark = pytest.mark.runtime_only

SCRIPT = "scripts/assemble_changelog.py"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """An isolated repo-shaped tree so tests never touch the real CHANGELOG."""
    import pathlib

    repo = tmp_path / "repo"
    (repo / "changelog.d").mkdir(parents=True)
    (repo / "scripts").mkdir()

    real = pathlib.Path(__file__).resolve().parents[2] / SCRIPT
    (repo / SCRIPT).write_text(real.read_text(encoding="utf-8"), encoding="utf-8")
    (repo / "CHANGELOG.md").write_text(
        "# Changelog\n\n## Unreleased\n\n_Nothing yet._\n\n## 2.2.0 — 2026-08-16\n\nolder.\n",
        encoding="utf-8",
    )
    return repo


def _fragment(repo, name: str, body: str) -> None:
    (repo / "changelog.d" / name).write_text(textwrap.dedent(body).strip() + "\n", encoding="utf-8")


def _run(repo, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, SCRIPT, *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def _changelog(repo) -> str:
    return (repo / "CHANGELOG.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------------------
# The property that matters: nothing is lost
# --------------------------------------------------------------------------------------


def test_every_fragment_reaches_the_changelog(workspace):
    """The whole point. A silently dropped entry is the failure this replaces."""
    _fragment(workspace, "10-alpha.md", "### Fixed — alpha (#1)\n\nalpha body.")
    _fragment(workspace, "20-beta.md", "### Added — beta (#2)\n\nbeta body.")
    _fragment(workspace, "30-gamma.md", "### Changed — gamma (#3)\n\ngamma body.")

    result = _run(workspace)
    text = _changelog(workspace)

    assert result.returncode == 0, result.stderr
    for token in ("alpha body.", "beta body.", "gamma body."):
        assert token in text, f"{token} was dropped during assembly"


def test_fragments_are_removed_after_folding(workspace):
    """Otherwise the next release folds them in a second time."""
    _fragment(workspace, "10-alpha.md", "### Fixed — alpha (#1)\n\nalpha body.")

    _run(workspace)

    assert not list((workspace / "changelog.d").glob("*.md")) or [
        p.name for p in (workspace / "changelog.d").glob("*.md")
    ] == ["README.md"]


def test_assembly_is_not_double_applied(workspace):
    """Running twice must not duplicate an entry."""
    _fragment(workspace, "10-alpha.md", "### Fixed — alpha (#1)\n\nalpha body.")

    _run(workspace)
    _run(workspace)

    assert _changelog(workspace).count("alpha body.") == 1


def test_existing_entries_survive(workspace):
    """Assembly must not disturb released sections below `Unreleased`."""
    _fragment(workspace, "10-alpha.md", "### Fixed — alpha (#1)\n\nalpha body.")

    _run(workspace)
    text = _changelog(workspace)

    assert "## 2.2.0 — 2026-08-16" in text
    assert "older." in text


# --------------------------------------------------------------------------------------
# Ordering — the protocol's "not buried in a bullet" rule
# --------------------------------------------------------------------------------------


def test_zero_prefixed_fragments_sort_to_the_top(workspace):
    """`00-` is how an operator-must-read entry stays at the top of the section."""
    _fragment(workspace, "50-ordinary.md", "### Added — ordinary (#2)\n\nordinary body.")
    _fragment(workspace, "00-breaking.md", "### ★ Changed — breaking (#1)\n\nbreaking body.")

    _run(workspace)
    text = _changelog(workspace)

    assert text.index("breaking body.") < text.index("ordinary body."), (
        "a 00- prefixed entry must precede ordinary ones, or the protocol's 'call it out at "
        "the top, not buried' rule is unenforceable"
    )


def test_fragment_order_does_not_depend_on_directory_order():
    """★ The test above is platform-dependently vacuous, so this one is the real guard.

    Removing `sorted()` from the assembler failed **zero** tests on Windows, because NTFS
    returns directory entries alphabetically anyway. On Linux — where CI runs — `glob` order is
    arbitrary, so the sort is load-bearing exactly where the filesystem hides it.

    This drives `fragments()` directly against a deliberately reversed listing, so the
    assertion is about the code rather than about the filesystem that happened to run it.
    """
    import importlib.util
    import pathlib

    script = pathlib.Path(__file__).resolve().parents[2] / SCRIPT
    spec = importlib.util.spec_from_file_location("assemble_changelog", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    names = ["50-ordinary.md", "00-breaking.md", "10-middle.md"]

    class _FakeDir:
        def is_dir(self):
            return True

        def glob(self, pattern):
            # Reversed on purpose: an unsorted implementation returns this order verbatim.
            return [pathlib.Path(n) for n in reversed(sorted(names))]

    module.FRAGMENT_DIR = _FakeDir()

    got = [p.name for p in module.fragments()]

    assert got == ["00-breaking.md", "10-middle.md", "50-ordinary.md"], (
        f"fragments() returned {got} — it is echoing directory order rather than sorting, so "
        f"a 00- prefixed operator note would land wherever the filesystem felt like putting it"
    )


def test_placeholder_is_replaced_not_left_above_real_entries(workspace):
    _fragment(workspace, "10-alpha.md", "### Fixed — alpha (#1)\n\nalpha body.")

    _run(workspace)
    text = _changelog(workspace)

    unreleased = text.split("## Unreleased")[1].split("## 2.2.0")[0]
    assert "_Nothing yet._" not in unreleased


def test_readme_is_not_treated_as_an_entry(workspace):
    """`changelog.d/README.md` documents the mechanism; it is not a changelog entry."""
    _fragment(workspace, "README.md", "# how to use this directory\n\nnot an entry.")
    _fragment(workspace, "10-alpha.md", "### Fixed — alpha (#1)\n\nalpha body.")

    _run(workspace)
    text = _changelog(workspace)

    assert "not an entry." not in text
    assert "alpha body." in text
    assert (workspace / "changelog.d" / "README.md").exists(), "README must not be consumed"


# --------------------------------------------------------------------------------------
# Degenerate cases and intent
# --------------------------------------------------------------------------------------


def test_no_fragments_is_a_no_op(workspace):
    before = _changelog(workspace)

    result = _run(workspace)

    assert result.returncode == 0
    assert _changelog(workspace) == before


def test_check_mode_is_release_only_semantics(workspace):
    """`--check` fails when fragments exist — which is NORMAL during development.

    Recorded as a test so nobody wires `--check` into per-PR CI: that would fail every PR that
    correctly wrote a fragment, i.e. invert the entire design.
    """
    _fragment(workspace, "10-alpha.md", "### Fixed — alpha (#1)\n\nalpha body.")

    pending = _run(workspace, "--check")
    assert pending.returncode == 1, "--check must fail while fragments are unfolded"

    _run(workspace)
    folded = _run(workspace, "--check")
    assert folded.returncode == 0, "--check must pass once fragments are folded in"


def test_dry_run_writes_nothing(workspace):
    _fragment(workspace, "10-alpha.md", "### Fixed — alpha (#1)\n\nalpha body.")
    before = _changelog(workspace)

    result = _run(workspace, "--dry-run")

    assert result.returncode == 0
    assert _changelog(workspace) == before
    assert (workspace / "changelog.d" / "10-alpha.md").exists(), "dry-run must not delete"
