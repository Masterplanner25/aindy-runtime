"""CAPABILITY-PROVIDER-TIMEOUT-1 — capability providers ran on every tool check.

`_load_capability_definition_providers` is reached from `get_capability_definitions`,
`get_capability_definition`, `get_capabilities_for_tool` and `get_capabilities_for_agent`, and
therefore from `check_tool_capability` — **the tool-execution path**. Providers are
subprocess-isolated by `_maybe_wrap_runtime_callback`, so every capability check spawned a
process per provider and waited on a 30s budget.

Under CPU contention that budget was exceeded and the exception was swallowed into a
`logger.warning`, leaving the capability set empty.

★ **It fails closed, and the first filing of this entry said otherwise.** `check_tool_capability`
refuses a tool with no registered mapping (`if not required_capabilities and tool_name in
TOOL_REGISTRY`), so the outcome is *denied tool execution*, not a vacuous check. That was
established by running it, not by reading it — the entry had been filed at P1 on the assumption
that an empty capability vocabulary would let things through. `test_an_empty_capability_set_denies`
pins the real behaviour so the correction cannot decay back into the guess.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.runtime_only

TOOL = "memory.recall"


@pytest.fixture
def registry_module():
    from AINDY.platform_layer import registry

    return registry


@pytest.fixture
def isolated_providers(registry_module, monkeypatch):
    """Swap the provider list for one this test owns, and restore the caches it touches."""
    monkeypatch.setattr(registry_module, "_capability_definition_providers", [])
    monkeypatch.setattr(registry_module, "_runtime_agent_defaults_loaded", True)
    monkeypatch.setattr(registry_module, "load_plugins", lambda *a, **k: None)
    yield registry_module


def _bundle(name="cap_probe"):
    return {
        "definitions": {name: {"description": "probe", "risk_level": "low"}},
        "tool_capabilities": {"probe.tool": [name]},
    }


# --------------------------------------------------------------------------------------
# ★ The claim: a provider runs once, not per call
# --------------------------------------------------------------------------------------


def test_a_provider_is_called_once_across_many_lookups(isolated_providers):
    """★ The whole point. Each of these calls used to spawn a subprocess.

    Counts real invocations rather than timing anything, because "it got faster" is not the
    claim — "it stopped calling out per capability check" is.
    """
    registry = isolated_providers
    calls = []

    def provider():
        calls.append(1)
        return _bundle()

    registry._capability_definition_providers.append(provider)

    for _ in range(5):
        registry.get_capability_definitions()
        registry.get_capabilities_for_tool("probe.tool")

    assert len(calls) == 1, f"provider ran {len(calls)} times across 10 lookups"


def test_the_bundle_is_still_applied_on_every_call(isolated_providers):
    """Caching the provider's *output* must not stop the output being applied.

    If a caller clears the registry's definition dicts — the test fixtures do exactly this —
    the next lookup has to repopulate them from the cached bundle rather than returning empty.
    """
    registry = isolated_providers

    registry._capability_definition_providers.append(lambda: _bundle())
    assert "cap_probe" in registry.get_capability_definitions()

    registry._capability_definitions.clear()
    registry._tool_capabilities.clear()

    assert "cap_probe" in registry.get_capability_definitions(), (
        "the cached bundle was not re-applied after the definitions were cleared"
    )


def test_a_new_provider_is_picked_up(isolated_providers):
    """The cache lives on the provider object, so a different provider is a different cache."""
    registry = isolated_providers

    registry._capability_definition_providers.append(lambda: _bundle("first"))
    assert "first" in registry.get_capability_definitions()

    registry._capability_definition_providers.append(lambda: _bundle("second"))
    definitions = registry.get_capability_definitions()

    assert "first" in definitions and "second" in definitions


# --------------------------------------------------------------------------------------
# Failure handling
# --------------------------------------------------------------------------------------


def test_a_failing_provider_is_retried_not_cached(isolated_providers):
    """★ A transient timeout must not become permanent.

    Caching the failure — or latching "providers loaded" before they succeeded — would leave the
    capability set empty for the life of the process, so a 30-second blip would take tool
    execution down until restart.
    """
    registry = isolated_providers
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("runtime callback command timed out after 30s")
        return _bundle("recovered")

    registry._capability_definition_providers.append(flaky)

    first = registry.get_capability_definitions()
    assert "recovered" not in first

    second = registry.get_capability_definitions()

    assert len(attempts) == 2, "the failed provider was not retried"
    assert "recovered" in second, "the retry did not repair the capability set"


def test_a_failing_provider_logs_at_error(isolated_providers, caplog):
    """It was a WARNING, which is why it went unnoticed through two full-suite runs.

    The message must also say what the failure *costs* — an incomplete capability set denies
    tool execution — because the downstream error names the tool, not the cause.
    """
    import logging

    registry = isolated_providers

    def boom():
        raise RuntimeError("runtime callback command timed out after 30s")

    registry._capability_definition_providers.append(boom)

    with caplog.at_level(logging.ERROR, logger="AINDY.platform_layer.registry"):
        registry.get_capability_definitions()

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "a failed capability provider produced no ERROR record"
    assert "tool execution will be refused" in errors[0].getMessage()


def test_one_failing_provider_does_not_suppress_a_healthy_one(isolated_providers):
    """Partial degradation must stay partial — the surviving bundle is still applied."""
    registry = isolated_providers

    def boom():
        raise RuntimeError("timed out")

    registry._capability_definition_providers.extend([boom, lambda: _bundle("healthy")])

    assert "healthy" in registry.get_capability_definitions()


# --------------------------------------------------------------------------------------
# ★ The correction: which way it fails
# --------------------------------------------------------------------------------------


def test_an_empty_capability_set_denies_rather_than_permits(monkeypatch):
    """★ Pins the direction, because this entry was filed at P1 on the opposite assumption.

    An empty mapping reaches `check_tool_capability` as `required_capabilities == []`, and the
    loop over it is vacuous — which looks like a fail-open until you find the guard underneath:
    `if not required_capabilities and tool_name in TOOL_REGISTRY`. The guard is what makes this
    an availability problem rather than a security one, and it is conditional, so it is worth a
    test rather than a comment.
    """
    from AINDY.agents import capability_service as cs
    from AINDY.agents.tool_registry import TOOL_REGISTRY
    from AINDY.platform_layer import registry

    monkeypatch.setattr(
        cs,
        "validate_token",
        lambda **kwargs: {
            "ok": True,
            "granted_tools": [TOOL],
            "allowed_capabilities": [],
            "error": None,
        },
    )
    monkeypatch.setitem(TOOL_REGISTRY, TOOL, object())
    monkeypatch.setattr(registry, "_capability_definitions", {})
    monkeypatch.setattr(registry, "_tool_capabilities", {})
    monkeypatch.setattr(registry, "_capability_definition_providers", [])
    monkeypatch.setattr(registry, "_runtime_agent_defaults_loaded", True)
    monkeypatch.setattr(registry, "load_plugins", lambda *a, **k: None)

    result = cs.check_tool_capability(token={}, run_id="r", user_id="u", tool_name=TOOL)

    assert result["ok"] is False, (
        "an empty capability set let a tool through — this would make the whole capability "
        "model vacuous whenever a provider is slow"
    )
    assert "no registered capability mapping" in result["error"]
