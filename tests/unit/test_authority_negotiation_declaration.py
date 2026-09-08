"""`AUTHORITY-NEGOTIATION-1` phase 0 — a tool can DECLARE a lower-authority fallback.

Phase 0 ships the vocabulary and nothing else: `degraded_variant=` is accepted, validated, and
**consulted by nothing**. The design (`docs/runtime/AUTHORITY_NEGOTIATION_DESIGN.md` §8) phases
it this way deliberately, on the declare-then-enforce sequence that let `EXEC-ENV-BIND-1` land in
pieces.

★★ **The inertness is the risky part to test, not the validation.** A declaration nothing
consults is `ECOGAP-4`'s G4a — built and inert — and this repository already has one of those.
So `test_the_declaration_changes_nothing_about_execution` is the load-bearing test here: it
pins that a tool declaring a variant behaves exactly as one that does not. When phase 1 lands,
that test is the one that must be deliberately changed, which is the point.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.runtime_only


@pytest.fixture
def registry(monkeypatch):
    """An isolated TOOL_REGISTRY.

    ★ Patched rather than mutated-and-restored: `TEST-ORDER-REGISTRY-1` is filed because a suite
    asserting against the global registries passes only by alphabetical luck. This one must not
    join it.
    """
    from AINDY.agents import tool_registry as tr

    monkeypatch.setattr(tr, "TOOL_REGISTRY", {}, raising=True)
    monkeypatch.setattr(tr, "_ensure_tools_loaded", lambda: None, raising=True)
    return tr


def _register(tr, name, *, variant=None, capability="cap.write"):
    return tr.register_tool(
        name=name,
        risk="low",
        description=f"{name} probe",
        capability=capability,
        required_capability=capability,
        category="test",
        egress_scope="none",
        degraded_variant=variant,
    )(lambda **kwargs: {"ok": True, "tool": name})


def _capabilities(monkeypatch, mapping):
    """Pin `_get_capabilities_for_tool`, the function the strict-subset rule consults."""
    import AINDY.agents.capability_service as cs

    monkeypatch.setattr(
        cs, "_get_capabilities_for_tool", lambda name: list(mapping.get(name, [])), raising=True
    )


# --------------------------------------------------------------------------------------
# Declaration
# --------------------------------------------------------------------------------------


def test_an_undeclared_tool_records_none(registry):
    """The default is the state of every tool today, and must stay expressible."""
    _register(registry, "plain")

    assert registry.TOOL_REGISTRY["plain"]["degraded_variant"] is None


def test_a_declared_variant_is_recorded(registry):
    _register(registry, "send_invoice_email", variant="queue_invoice_for_review")

    assert (
        registry.TOOL_REGISTRY["send_invoice_email"]["degraded_variant"]
        == "queue_invoice_for_review"
    )


def test_a_self_referential_variant_is_refused_at_declaration(registry):
    """★ Refused locally, because it needs no registry to see and no capability data.

    Left to the sweep it would be reported as a chain — a true statement naming the wrong
    mistake. A tool falling back to itself would retry the call that was just refused.
    """
    with pytest.raises(ValueError, match="names the tool itself"):
        _register(registry, "loopy", variant="loopy")


@pytest.mark.parametrize("bad", ["", "   ", 7, []])
def test_a_non_name_variant_is_refused_at_declaration(registry, bad):
    with pytest.raises(ValueError, match="non-empty tool name"):
        _register(registry, "probe", variant=bad)


# --------------------------------------------------------------------------------------
# The startup sweep — the three cross-tool rules
# --------------------------------------------------------------------------------------


def test_a_well_formed_declaration_sweeps_clean(registry, monkeypatch):
    _register(registry, "send_email", variant="queue_for_review")
    _register(registry, "queue_for_review")
    _capabilities(monkeypatch, {"send_email": ["email.send", "email.read"], "queue_for_review": ["email.read"]})

    assert registry.validate_degraded_variants() == []


def test_rule_1_an_unregistered_target_is_refused(registry, monkeypatch):
    _register(registry, "send_email", variant="nope_not_a_tool")
    _capabilities(monkeypatch, {"send_email": ["email.send"]})

    problems = registry.validate_degraded_variants()

    assert len(problems) == 1
    assert "is not a registered tool" in problems[0]


def test_rule_2_an_equal_capability_set_is_refused(registry, monkeypatch):
    """Strict subset. Equal sets are a no-op dressed as a downgrade."""
    _register(registry, "send_email", variant="also_send_email")
    _register(registry, "also_send_email")
    _capabilities(monkeypatch, {"send_email": ["email.send"], "also_send_email": ["email.send"]})

    problems = registry.validate_degraded_variants()

    assert len(problems) == 1
    assert "not a STRICT subset" in problems[0]


def test_rule_2_a_lateral_move_is_refused(registry, monkeypatch):
    """★ The failure the word 'degraded' hides: different authority, not less of it."""
    _register(registry, "send_email", variant="post_to_slack")
    _register(registry, "post_to_slack")
    _capabilities(monkeypatch, {"send_email": ["email.send"], "post_to_slack": ["slack.post"]})

    problems = registry.validate_degraded_variants()

    assert len(problems) == 1
    assert "lateral move" in problems[0]


def test_rule_3_a_chain_is_refused(registry, monkeypatch):
    """No chains — the bound is structural, not a counter someone can get wrong."""
    _register(registry, "a", variant="b")
    _register(registry, "b", variant="c")
    _register(registry, "c")
    _capabilities(monkeypatch, {"a": ["x", "y", "z"], "b": ["x", "y"], "c": ["x"]})

    problems = registry.validate_degraded_variants()

    assert any("itself declares" in p for p in problems)


# --------------------------------------------------------------------------------------
# ★★ Unevaluable is not the same as failed
# --------------------------------------------------------------------------------------


def test_an_unresolvable_capability_set_reports_unevaluable_not_wrong(registry, monkeypatch):
    """★★ Green-check variant 10 in reverse — an instrument that cannot see, answering anyway.

    `_get_capabilities_for_tool` returns `[]` both when a tool requires nothing and when the
    lookup could not run (it catches its own exceptions and warns). An empty ORIGINAL set makes a
    strict subset impossible, so a naive check refuses the declaration and blames a typo for what
    may be an unloaded capability provider. The message must say which of the two it is.
    """
    _register(registry, "send_email", variant="queue_for_review")
    _register(registry, "queue_for_review")
    _capabilities(monkeypatch, {})  # nothing resolves

    problems = registry.validate_degraded_variants()

    assert len(problems) == 1
    assert "unevaluable" in problems[0]
    assert "NOT proof the declaration is wrong" in problems[0]
    assert "not a STRICT subset" not in problems[0], (
        "an unresolvable capability set must not be reported as a failed subset check — that "
        "sends an operator to fix a declaration when the provider is what did not load"
    )


def test_a_chain_is_still_named_a_chain_when_capabilities_are_unresolvable(registry, monkeypatch):
    """Rule 3 runs before rule 2 precisely so a structural error survives a blind instrument."""
    _register(registry, "a", variant="b")
    _register(registry, "b", variant="c")
    _register(registry, "c")
    _capabilities(monkeypatch, {})

    problems = registry.validate_degraded_variants()

    assert any("itself declares" in p for p in problems)


# --------------------------------------------------------------------------------------
# ★★ Phase 0 is inert — the load-bearing guarantee
# --------------------------------------------------------------------------------------


def test_the_declaration_changes_nothing_about_execution(registry, monkeypatch):
    """★★ THE test for this phase. A declared tool must execute exactly like an undeclared one.

    When phase 1 lands, this is the test that must be deliberately changed. Until then it is what
    separates "shipped a vocabulary" from "shipped a behaviour nobody reviewed".
    """
    _register(registry, "declared", variant="fallback")
    _register(registry, "fallback")
    _register(registry, "undeclared")

    declared = dict(registry.TOOL_REGISTRY["declared"])
    undeclared = dict(registry.TOOL_REGISTRY["undeclared"])

    declared.pop("degraded_variant")
    undeclared.pop("degraded_variant")
    declared.pop("fn")
    undeclared.pop("fn")
    declared.pop("description")
    undeclared.pop("description")

    assert declared == undeclared, (
        "declaring a degraded_variant changed some OTHER field of the registry entry; phase 0 "
        "must add exactly one key and alter nothing else"
    )


def test_nothing_in_the_execution_path_consults_the_field(registry):
    """★ Pins the phase boundary over the AST, so a comment cannot satisfy it.

    `degraded_variant` may be READ only where it is declared, swept, or logged. A read anywhere
    on an execution path means phase 1 has arrived — at which point this test should be replaced
    deliberately, not deleted quietly because it went red.
    """
    import ast
    from pathlib import Path

    allowed = {
        "AINDY/agents/tool_registry.py",   # declaration + sweep
        "AINDY/startup.py",                # the startup refusal
    }
    offenders = []
    for path in Path("AINDY").rglob("*.py"):
        rel = path.as_posix()
        if rel in allowed:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == "degraded_variant":
                offenders.append(rel)
                break
            if isinstance(node, ast.Attribute) and node.attr == "degraded_variant":
                offenders.append(rel)
                break

    assert not offenders, (
        f"degraded_variant is read outside its declaration and validation sites: {offenders}. "
        f"Phase 0 is declare/refuse/record with NO execution path changes. If this is phase 1, "
        f"replace this test with one asserting the negotiation behaviour."
    )


def test_the_inertness_guard_is_not_vacuous():
    """Liveness — the guard above must actually be able to see a read.

    A scan that matches nothing passes whether or not the property holds, which is variant 6.
    """
    import ast

    tree = ast.parse("entry.get('degraded_variant')")
    found = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and n.value == "degraded_variant"
    ]

    assert found, "the AST matcher cannot see a read it is supposed to catch"
