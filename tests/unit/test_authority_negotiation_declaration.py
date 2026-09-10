"""`AUTHORITY-NEGOTIATION-1` — a tool can DECLARE a lower-authority fallback (the declaration half).

Phase 0 shipped the vocabulary: `degraded_variant=` accepted, validated locally at the decorator
and across tools by a startup sweep. **Phase 1 (#613-onward) made it consulted** — see
`test_authority_negotiation_behaviour.py` for the negotiation itself. This file still owns the
DECLARATION: what a valid declaration is, what is refused, and what the sweep reports.

★★ **Updated at phase 1, deliberately.** Two things here were written against phase 0's
inertness and were changed rather than deleted when it ended:

- `test_nothing_in_the_execution_path_consults_the_field` became
  `test_only_the_negotiation_module_consults_the_field`. The property worth protecting was never
  "nobody reads this" — it was **"the set of readers is small and known"**, and that survives the
  phase change. It did its job on the way out: landing phase 1 turned it red with exactly the
  message it was written to emit.
- `test_the_declaration_changes_nothing_about_execution` is unchanged, because what it actually
  asserts — that declaring a variant adds exactly one key to the registry entry and alters
  nothing else — is as true in phase 1 as in phase 0. Its docstring overstated it as an
  execution claim; that is corrected below.
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
# ★★ The declaration is exactly one key — unchanged by phase 1
# --------------------------------------------------------------------------------------


def test_the_declaration_changes_nothing_about_execution(registry, monkeypatch):
    """A declaration adds exactly one key to the registry entry and alters nothing else.

    ★ The name and the old docstring said "changes nothing about EXECUTION", which overstated
    it — this compares registry entries, not behaviour. The narrower claim is the one it can
    actually make, and it survives phase 1 untouched: negotiation reads the key, it does not
    change the shape of what declaring one produces.
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


def test_only_the_negotiation_module_consults_the_field(registry):
    """★ REPLACED at phase 1, deliberately — not deleted because it went red.

    Phase 0's version asserted that NOTHING read ``degraded_variant`` outside its declaration
    and validation sites, and it did its job: landing phase 1 turned it red with exactly the
    message it was written to emit.

    The guard is inverted rather than dropped, because the property worth protecting survived
    the phase change. It was never "nobody reads this" — it was **"the set of readers is small
    and known"**. A negotiation that grew a second, ad-hoc reader somewhere in the execution
    path is the thing to catch, and deleting the test would have stopped catching it.

    ``AINDY/agents/authority_negotiation.py`` is the one execution-path reader. The declaration
    and sweep sites are unchanged.
    """
    import ast
    from pathlib import Path

    allowed = {
        "AINDY/agents/tool_registry.py",          # declaration + startup sweep
        "AINDY/startup.py",                       # the startup refusal
        "AINDY/agents/authority_negotiation.py",  # phase 1 — the single execution-path reader
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
        f"degraded_variant is read outside the declaration, sweep and negotiation sites: "
        f"{offenders}. Phase 1 routes every consultation through "
        f"authority_negotiation.negotiate_capability_denial(); a second reader means the "
        f"single bounded attempt is no longer structurally guaranteed."
    )


def test_the_negotiation_module_actually_reads_it(registry):
    """Liveness for the guard above — the allow-list must name a real reader, not a stale path.

    Without this, the guard degrades into a list of files that happen not to exist, and it would
    pass just as happily if phase 1 were reverted and the allow-list left behind.
    """
    import ast
    from pathlib import Path

    path = Path("AINDY/agents/authority_negotiation.py")
    assert path.is_file(), "the phase 1 module is gone; this allow-list entry is now a lie"

    tree = ast.parse(path.read_text(encoding="utf-8"))
    reads = [
        n for n in ast.walk(tree)
        if (isinstance(n, ast.Constant) and n.value == "degraded_variant")
        or (isinstance(n, ast.Attribute) and n.attr == "degraded_variant")
    ]
    assert reads, "authority_negotiation.py is allow-listed but never reads degraded_variant"


def test_the_sweep_does_not_force_tools_to_load():
    """★★ THE REGRESSION THIS SUITE MISSED, and CI caught instead.

    The first version of `validate_degraded_variants()` opened with `_ensure_tools_loaded()` —
    reasonable-looking, and wrong. That function runs `_ensure_runtime_agent_defaults()`, which
    IS a trusted bootstrap registration, so calling it took a platform-only boot from
    `bootstrap_registration_count: 0` to `1` and failed `tests/api/test_version_api.py`.
    **Phase 0 is meant to be inert and that made it observable on an audit surface.**

    ★ Why the rest of this file could not see it: every test here patches `_ensure_tools_loaded`
    to a no-op so the registry stays isolated. That fixture is correct for what it isolates and
    it made the suite structurally blind to the side effect — the guard has to assert the call
    does not happen, which a no-op patch can never do. **A fixture that neutralises a dependency
    also neutralises any test of how that dependency is used.**
    """
    from AINDY.agents import tool_registry as tr

    calls = []
    original = tr.TOOL_REGISTRY
    try:
        tr.TOOL_REGISTRY = {}
        real_ensure = tr._ensure_tools_loaded
        tr._ensure_tools_loaded = lambda: calls.append(1)
        tr.validate_degraded_variants()
    finally:
        tr._ensure_tools_loaded = real_ensure
        tr.TOOL_REGISTRY = original

    assert calls == [], (
        "validate_degraded_variants() called _ensure_tools_loaded(). That registers runtime "
        "agent defaults, which is a trusted bootstrap registration and changes "
        "bootstrap_registration_count on the version API. Validating a registry is not a reason "
        "to populate one."
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
