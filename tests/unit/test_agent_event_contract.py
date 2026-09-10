"""The agent-event vocabulary contract — the guard system events have had all along.

`SystemEventTypes` has been pinned by a SHA-256 baseline since early on
(`test_system_event_contract.py`). Agent events had the model, the emitter and a warning, and
never got the guard. This is that missing half, deliberately built to the same shape so the two
can be read against each other.

★★ **What the absence cost, measured before this existed.** Two `AGENT_EVENT_TYPES` sets had
drifted apart, and neither described reality: the copy in `db/models/agent_event.py` held 9 names
and had zero importers, the enforced copy in `agent_event_service.py` held 14, and **20 were in
active use**. Six types — `AGENT_STEP_COMPLETED`, `FAILED`, and the four `DELEGATION_*` — logged
*"Unknown event type"* on every emission and were written anyway.

★ **The guard is a TEST, not a runtime rejection, and that matches the precedent exactly.**
`emit_event` still warns and still writes. Losing an audit row is strictly worse than recording
one with an undeclared name, so the runtime must never refuse. This test is what makes a new name
*intentional*; the warning is what makes it *visible*.

★ **`test_every_emitted_type_is_declared` is the one that would have caught the original drift**,
and it is the reason this file is not just a hash. A baseline pins the list against *itself* — it
cannot notice that the list stopped describing the code. That is green-check variant 12 (a census
that is never re-derived), so the census here is derived from source.
"""

from __future__ import annotations

import ast
import hashlib
import json
import pathlib
import re

import pytest

pytestmark = pytest.mark.runtime_only

_BASELINE_FILE = pathlib.Path(__file__).parent.parent / "baselines" / "agent_event_contract.json"
_AINDY_ROOT = pathlib.Path(__file__).resolve().parents[2] / "AINDY"

# ★ Read off the model, not written from memory. The first draft of this line guessed and was
#   wrong in both directions — it invented nothing but MISSED `occurred_at` and `system_event_id`
#   — which is green-check variant 12 (a hand-written census inside a check) committed in the
#   very file that adds a test against it. Kept as a literal deliberately: this one pins an
#   EXPECTED set so a silent ORM change is caught, and it is compared against a DERIVED set
#   below rather than used as one.
_EXPECTED_MODEL_COLUMNS = frozenset({
    "id", "run_id", "correlation_id", "user_id", "event_type",
    "payload", "created_at", "occurred_at", "system_event_id",
})


def _collect_event_types() -> list[str]:
    from AINDY.agents.agent_event_types import AGENT_EVENT_TYPES

    return sorted(AGENT_EVENT_TYPES)


def _hash_event_types(types: list[str]) -> str:
    return hashlib.sha256("\n".join(types).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 1. Vocabulary registry hash
# ---------------------------------------------------------------------------

def test_agent_event_type_registry_is_stable():
    """Fails when the vocabulary gains, loses or renames a type.

    To update the baseline after an intentional change:
        python -c "
        from tests.unit.test_agent_event_contract import _collect_event_types, _hash_event_types
        import json, pathlib
        t = _collect_event_types(); h = _hash_event_types(t)
        pathlib.Path('tests/baselines/agent_event_contract.json').write_text(
            json.dumps({'hash': h, 'types': t}, indent=2)
        )
        print('Baseline updated:', h)
        "
    """
    current_types = _collect_event_types()
    current_hash = _hash_event_types(current_types)

    if not _BASELINE_FILE.exists():
        _BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _BASELINE_FILE.write_text(
            json.dumps({"hash": current_hash, "types": current_types}, indent=2)
        )
        pytest.skip(
            f"Baseline created ({current_hash}) — commit "
            "tests/baselines/agent_event_contract.json"
        )

    baseline = json.loads(_BASELINE_FILE.read_text())
    added = set(current_types) - set(baseline["types"])
    removed = set(baseline["types"]) - set(current_types)

    assert current_hash == baseline["hash"], (
        f"The agent-event vocabulary drifted.\n"
        f"  Added:   {sorted(added) or '(none)'}\n"
        f"  Removed: {sorted(removed) or '(none)'}\n"
        f"  New hash: {current_hash}\n"
        "Update the baseline (see docstring) if the change is intentional. An event type is a "
        "STORED value — removing or renaming one orphans every row already written with it."
    )


# ---------------------------------------------------------------------------
# 2. ★ The census, derived from source rather than written by hand
# ---------------------------------------------------------------------------

def _string_literals(node, local_names) -> set[str]:
    """Every string this expression can evaluate to, as far as the AST can tell.

    ★ Three shapes, and the first draft handled only the first. Mutation testing found the gap:
    removing an emitted type from the vocabulary failed only the HASH guard, which meant the
    census could not see the call sites it claimed to derive from.

    - a plain literal            `event_type="X"`
    - a ternary                  `event_type="X" if c else "Y"`   <- both branches
    - a local variable           `event_type = "X"` ... `event_type=event_type`

    The ternary case is not hypothetical: it is how `AGENT_STEP_COMPLETED`/`AGENT_STEP_FAILED`
    are emitted, and a regex that stops at the first branch reports one of the two. That is how
    the original survey of this drift undercounted by one.
    """
    out: set[str] = set()
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            out.add(node.value)
    elif isinstance(node, ast.IfExp):
        out |= _string_literals(node.body, local_names)
        out |= _string_literals(node.orelse, local_names)
    elif isinstance(node, ast.Name):
        out |= local_names.get(node.id, set())
    return out


def _emitted_agent_event_types() -> dict[str, set[str]]:
    """Every event type literal reaching `record_agent_event` / `emit_event`, derived from source.

    ★ Scoped per function, because a name assigned in one function says nothing about the same
    name in another. Assignments are collected first so an assignment *below* the call (or in a
    sibling branch, which is the coordinator's shape) still resolves.
    """
    emitters = {"record_agent_event", "emit_event"}
    found: dict[str, set[str]] = {}

    for path in _AINDY_ROOT.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue

        scopes = [tree] + [
            n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for scope in scopes:
            local_names: dict[str, set[str]] = {}
            for node in ast.walk(scope):
                if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Constant, ast.IfExp)):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            local_names.setdefault(target.id, set()).update(
                                _string_literals(node.value, {})
                            )

            for node in ast.walk(scope):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
                if name not in emitters:
                    continue
                for kw in node.keywords:
                    if kw.arg != "event_type":
                        continue
                    for value in _string_literals(kw.value, local_names):
                        if value.isupper():
                            found.setdefault(value, set()).add(path.as_posix())
    return found


def test_every_emitted_type_is_declared():
    """★ The test that would have caught the original drift.

    A baseline pins the list against itself and cannot notice that it stopped describing the
    code. Six types were emitted-but-undeclared for months precisely because nothing compared
    the declaration to the call sites.
    """
    from AINDY.agents.agent_event_types import AGENT_EVENT_TYPES

    emitted = _emitted_agent_event_types()
    undeclared = {k: v for k, v in emitted.items() if k not in AGENT_EVENT_TYPES}

    assert not undeclared, (
        "these agent event types are emitted but not declared, so each logs "
        '"Unknown event type" on every emission and is written anyway:\n'
        + "\n".join(f"  {k}  <- {', '.join(sorted(v))}" for k, v in sorted(undeclared.items()))
        + "\nAdd them to AINDY/agents/agent_event_types.py and update the baseline."
    )


def test_the_census_is_not_vacuous():
    """★ Liveness. A source sweep that finds nothing satisfies the test above for free — variant
    6, arriving one level up. Asserts the walk sees a type it is known to emit."""
    emitted = _emitted_agent_event_types()

    assert len(emitted) >= 10, f"the AST census found only {len(emitted)} emitted types"
    assert "CAPABILITY_DENIED" in emitted, (
        "the census cannot see a call site it is known to have; the AST matcher is broken"
    )


# ---------------------------------------------------------------------------
# 3. The vocabulary must not move back under the schema hash
# ---------------------------------------------------------------------------

def test_the_vocabulary_is_not_defined_under_db_models():
    """★ The regression this whole change exists to prevent.

    `scripts/check_schema_version.py` content-hashes every file under `AINDY/db/models/`, so a
    vocabulary defined there costs a SCHEMA_CONTRACT_VERSION bump, a baseline regeneration and
    two test-assertion edits per added string — for a change with no DDL, since `event_type` is
    a plain String(32). A copy lived there, was never updated after the initial extraction, and
    ended four types stale with zero importers. That was structural, not carelessness.
    """
    offenders = []
    for path in (_AINDY_ROOT / "db" / "models").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"^AGENT_EVENT_TYPES\s*=", text, re.M):
            offenders.append(path.as_posix())

    assert not offenders, (
        f"AGENT_EVENT_TYPES is defined under db/models again: {offenders}. That directory is "
        "content-hashed by the schema contract, so every added event type would cost a schema "
        "ceremony for a change with no DDL — which is exactly why the previous copy rotted. "
        "The vocabulary belongs in AINDY/agents/agent_event_types.py."
    )


def test_the_service_re_export_is_the_canonical_object():
    """One list, not two that agree today. Identity, not equality — equal sets drift apart."""
    from AINDY.agents.agent_event_service import AGENT_EVENT_TYPES as service
    from AINDY.agents.agent_event_types import AGENT_EVENT_TYPES as canonical

    assert service is canonical


# ---------------------------------------------------------------------------
# 4. AgentEvent ORM model column set
# ---------------------------------------------------------------------------

def test_agent_event_model_columns_are_stable():
    """Mirrors the SystemEvent half of the system-event contract."""
    from sqlalchemy import inspect as sa_inspect

    from AINDY.db.models.agent_event import AgentEvent

    actual = frozenset(c.key for c in sa_inspect(AgentEvent).mapper.column_attrs)
    added = actual - _EXPECTED_MODEL_COLUMNS
    removed = _EXPECTED_MODEL_COLUMNS - actual

    assert actual == _EXPECTED_MODEL_COLUMNS, (
        f"AgentEvent columns drifted.\n"
        f"  Added:   {sorted(added) or '(none)'}\n"
        f"  Removed: {sorted(removed) or '(none)'}\n"
        "Schema changes go through Alembic; update this expectation deliberately."
    )


def test_event_type_is_wide_enough_for_every_declared_name():
    """★ A 32-char column and a vocabulary nobody measures against it.

    Nothing enforces the vocabulary at the DB level, so an over-long name would be silently
    truncated or would raise on insert depending on the backend — found at emission time, in
    production, on the audit path.
    """
    from AINDY.agents.agent_event_types import AGENT_EVENT_TYPES
    from AINDY.db.models.agent_event import AgentEvent

    limit = AgentEvent.__table__.c.event_type.type.length
    too_long = sorted(t for t in AGENT_EVENT_TYPES if len(t) > limit)

    assert not too_long, f"event types longer than the {limit}-char column: {too_long}"
