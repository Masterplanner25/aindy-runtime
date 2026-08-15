"""`registry.register_agent` — registering an agent *identity* (APP-FR-* FR-12).

The registry already had eight `register_agent_*` hooks — `_tool`, `_planner_backend`,
`_planner_context`, `_run_tools`, `_completion_hook`, `_event`, `_ranking_strategy`,
`_capabilities` — and every one registers **behaviour attached to** an agent. None
registered the agent itself, so the roster was whatever `startup._bootstrap_system_agents`
hardcoded.

**A correction to the filed report, verified here.** FR-12 stated that "the only ways to
add a row are a runtime code change or a raw INSERT". That is not true:
`POST /platform/admin/agents/register` exists in `admin_router.py`, is mounted at
`/platform`, and is runtime-owned. What was genuinely missing is narrower:

  * no *platform hook*, so an app could not declare an identity at plugin-load;
  * `owner_user_id` was never written by **any** path — which is exactly why the live
    table showed `count(owner_user_id) = 0`;
  * reads were not scoped by owner;
  * the seven system namespaces were not reserved, so the admin route's
    idempotent-*update* branch could silently rewrite the platform's own rows.

Marked `runtime_only` — without it CI collects nothing here (CI-MARKER-1).
"""
from __future__ import annotations

import pytest

from AINDY.db.models.agent import SYSTEM_AGENTS
from AINDY.platform_layer import registry

pytestmark = pytest.mark.runtime_only


@pytest.fixture(autouse=True)
def clean_agent_registry():
    """`_agents` is module-level state; isolate it per test."""
    saved = dict(registry._agents)
    registry._agents.clear()
    yield
    registry._agents.clear()
    registry._agents.update(saved)


class TestRegisterAgent:
    def test_registers_a_spec_without_touching_a_database(self):
        """Registration happens at plugin-load, long before a session exists — so it
        must be declarative. `_apply_registered_agents()` does the DB work at startup."""
        spec = registry.register_agent("Scribe", "scribe")
        assert spec["name"] == "Scribe"
        assert spec["memory_namespace"] == "scribe"
        assert registry.get_agent_spec("scribe") == spec

    def test_defaults(self):
        spec = registry.register_agent("Scribe", "scribe")
        assert spec["agent_type"] == "custom"
        assert spec["description"] is None
        assert spec["owner_user_id"] is None
        assert spec["metadata"] is None

    def test_all_fields_round_trip(self):
        spec = registry.register_agent(
            "Scribe",
            "scribe",
            agent_type="assistant",
            description="writes things down",
            owner_user_id="11111111-1111-1111-1111-111111111111",
            metadata={"provider": "codex", "workspace": "w1"},
        )
        assert spec["agent_type"] == "assistant"
        assert spec["description"] == "writes things down"
        assert spec["owner_user_id"] == "11111111-1111-1111-1111-111111111111"
        assert spec["metadata"] == {"provider": "codex", "workspace": "w1"}

    def test_metadata_is_copied_not_aliased(self):
        payload = {"provider": "codex"}
        registry.register_agent("Scribe", "scribe", metadata=payload)
        payload["provider"] = "mutated"
        assert registry.get_agent_spec("scribe")["metadata"] == {"provider": "codex"}

    def test_duplicate_namespace_is_refused(self):
        registry.register_agent("Scribe", "scribe")
        with pytest.raises(ValueError, match="already registered"):
            registry.register_agent("Other", "scribe")

    def test_overwrite_is_explicit(self):
        registry.register_agent("Scribe", "scribe")
        spec = registry.register_agent("Renamed", "scribe", overwrite=True)
        assert spec["name"] == "Renamed"

    @pytest.mark.parametrize("bad", ["", "   ", None])
    def test_namespace_is_required(self, bad):
        with pytest.raises(ValueError, match="memory_namespace is required"):
            registry.register_agent("Scribe", bad)

    @pytest.mark.parametrize("bad", ["", "   ", None])
    def test_name_is_required(self, bad):
        with pytest.raises(ValueError, match="name is required"):
            registry.register_agent(bad, "scribe")

    def test_namespace_is_stripped(self):
        registry.register_agent("Scribe", "  scribe  ")
        assert registry.get_agent_spec("scribe") is not None


class TestSystemNamespacesAreReserved:
    """Without this, an app could rewrite the platform's own agent rows — and the next
    boot would not repair it, because `_bootstrap_system_agents` only *inserts* when the
    row is absent."""

    @pytest.mark.parametrize("namespace", sorted(SYSTEM_AGENTS))
    def test_every_system_namespace_is_refused(self, namespace):
        with pytest.raises(ValueError, match="reserved"):
            registry.register_agent("Impostor", namespace)

    def test_overwrite_does_not_bypass_the_reservation(self):
        with pytest.raises(ValueError, match="reserved"):
            registry.register_agent("Impostor", "runtime", overwrite=True)

    def test_the_error_names_the_reserved_set(self):
        with pytest.raises(ValueError) as excinfo:
            registry.register_agent("Impostor", "memory")
        assert "runtime" in str(excinfo.value)

    def test_a_non_reserved_namespace_is_allowed(self):
        assert registry.register_agent("Scribe", "scribe")["memory_namespace"] == "scribe"

    def test_the_reserved_set_is_the_seven_system_agents(self):
        """Pins the roster: `AGENT_USER` is deliberately *not* reserved, since the whole
        point of FR-12 is that user-owned agents become registrable."""
        assert len(SYSTEM_AGENTS) == 7
        assert "user" not in SYSTEM_AGENTS


class TestOwnerScopedReads:
    """The read half of FR-12: without scoping, one user can enumerate another's agents."""

    OWNER_A = "11111111-1111-1111-1111-111111111111"
    OWNER_B = "22222222-2222-2222-2222-222222222222"

    def _seed(self):
        registry.register_agent("Shared", "shared")
        registry.register_agent("A's", "a-agent", owner_user_id=self.OWNER_A)
        registry.register_agent("B's", "b-agent", owner_user_id=self.OWNER_B)

    def test_an_owner_sees_their_own_and_the_shared_ones(self):
        self._seed()
        visible = {s["memory_namespace"] for s in registry.list_agent_specs_for_owner(self.OWNER_A)}
        assert visible == {"shared", "a-agent"}

    def test_an_owner_cannot_see_another_owners_agent(self):
        self._seed()
        visible = {s["memory_namespace"] for s in registry.list_agent_specs_for_owner(self.OWNER_A)}
        assert "b-agent" not in visible

    def test_an_anonymous_reader_sees_only_shared_agents(self):
        self._seed()
        visible = {s["memory_namespace"] for s in registry.list_agent_specs_for_owner(None)}
        assert visible == {"shared"}

    def test_the_returned_specs_are_copies(self):
        """A caller mutating a result must not corrupt the registry."""
        self._seed()
        result = registry.list_agent_specs_for_owner(self.OWNER_A)
        result[0]["name"] = "hacked"
        assert all(s["name"] != "hacked" for _, s in registry.iter_agent_specs())

    def test_iter_agent_specs_is_unscoped_and_stays_that_way(self):
        """`iter_agent_specs` is the startup/upsert view and deliberately sees
        everything. Scoping belongs in `list_agent_specs_for_owner`; conflating them
        would either break the upsert or leak across owners."""
        self._seed()
        assert len({ns for ns, _ in registry.iter_agent_specs()}) == 3


class TestExtensionCapabilityGate:
    def test_registration_is_gated_like_the_other_hooks(self):
        """`register_agent` goes through the same in-process extension capability guard
        as `register_connector`, so it cannot be called from an isolated subprocess
        callback that has no such grant."""
        assert registry.INPROC_CAP_REGISTER_AGENT == "registry.register_agent"

    def test_the_capability_is_registered_in_the_known_set(self):
        assert (
            registry.INPROC_CAP_REGISTER_AGENT
            in registry._ALL_INPROC_EXTENSION_CAPABILITIES
        )


class TestAdminRouteReservesSystemNamespaces:
    """The same reservation, on the pre-existing admin route.

    `POST /platform/admin/agents/register` is idempotent on `memory_namespace` and its
    *update* branch rewrote name / type / description of whatever row it matched. Pointed
    at `runtime` or `memory` that silently rewrote a platform system agent — and the next
    boot would not repair it, because `_bootstrap_system_agents` only INSERTs when the row
    is absent. Asserted at the source level because the route needs an app + admin
    principal to exercise, which is a heavier fixture than this guard warrants.
    """

    @pytest.fixture(scope="class")
    def source(self) -> str:
        from pathlib import Path

        return Path("AINDY/routes/platform/admin_router.py").read_text(encoding="utf-8")

    def test_the_register_route_checks_the_reserved_set(self, source):
        register = source.split("def register_agent(")[1].split("@router.")[0]
        assert "SYSTEM_AGENTS" in register
        assert "409" in register

    def test_the_guard_precedes_the_lookup_it_protects(self, source):
        """Ordering matters: the check must run before the row is fetched and mutated."""
        register = source.split("def register_agent(")[1].split("@router.")[0]
        assert register.index("SYSTEM_AGENTS") < register.index("db.query(Agent)")

    def test_registry_and_route_share_one_reserved_set(self):
        """Two guards reading two lists would drift. Both import `SYSTEM_AGENTS` from
        the model."""
        from pathlib import Path

        route = Path("AINDY/routes/platform/admin_router.py").read_text(encoding="utf-8")
        reg = Path("AINDY/platform_layer/registry.py").read_text(encoding="utf-8")
        assert "from AINDY.db.models.agent import SYSTEM_AGENTS" in route
        assert "SYSTEM_AGENTS as _RESERVED_NAMESPACES" in reg
