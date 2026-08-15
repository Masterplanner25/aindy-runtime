"""User-owned agents — the authenticated half of APP-FR-* FR-12.

FR-12 shipped the platform hook and the admin route; neither let an ordinary
authenticated user own an agent, so `agents.owner_user_id` was written by exactly one
path and read by none (`count(owner_user_id) = 0` on the live stack).

Two things had to change before a user-facing surface was even coherent, and both are
pinned here rather than left to the route:

  * `agents.name` carried a **global** UNIQUE. The first user to register "Assistant"
    would have taken that name from everyone else, and the 409 saying so reports on a
    row the caller cannot see. Now unique per owner, via two partial indexes.
  * `memory_agents_list_node` listed **every** active agent to every caller. Harmless
    while all seven rows were un-owned; a cross-user leak the moment users own agents.

Marked `runtime_only` — without it CI collects nothing here (CI-MARKER-1).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from AINDY.db.models.agent import Agent, SYSTEM_AGENTS, SYSTEM_AGENT_SPECS
from AINDY.routes.platform.agents_router import (
    SLUG_PATTERN,
    derive_user_namespace,
)
from AINDY.services.auth_service import get_current_user

pytestmark = pytest.mark.runtime_only


def _user() -> dict:
    uid = str(uuid.uuid4())
    return {"sub": uid, "user_id": uid, "is_admin": False, "auth_type": "jwt"}


def _row(db, *, name, namespace, owner=None, active=True) -> Agent:
    agent = Agent(
        id=str(uuid.uuid4()),
        name=name,
        memory_namespace=namespace,
        agent_type="custom",
        owner_user_id=uuid.UUID(owner) if owner else None,
        is_active=active,
    )
    db.add(agent)
    db.commit()
    return agent


# ---------------------------------------------------------------------------
# Schema: name is unique per owner, not globally
# ---------------------------------------------------------------------------

class TestPerOwnerNameUniqueness:
    def test_two_users_may_share_a_display_name(self, db_session):
        """The defect the migration exists for. Under the old global UNIQUE the second
        insert raised, meaning one user's naming choice was binding on every other."""
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        _row(db_session, name="Assistant", namespace=derive_user_namespace(a, "asst"), owner=a)
        _row(db_session, name="Assistant", namespace=derive_user_namespace(b, "asst"), owner=b)

        assert db_session.query(Agent).filter(Agent.name == "Assistant").count() == 2

    def test_one_user_may_not_reuse_their_own_display_name(self, db_session):
        owner = str(uuid.uuid4())
        _row(db_session, name="Assistant", namespace=derive_user_namespace(owner, "one"), owner=owner)
        with pytest.raises(IntegrityError):
            _row(db_session, name="Assistant", namespace=derive_user_namespace(owner, "two"), owner=owner)
        db_session.rollback()

    def test_shared_agents_keep_the_global_guarantee(self, db_session):
        """The half a plain `UNIQUE (owner_user_id, name)` would have silently dropped.

        SQL treats NULLs as distinct inside a unique constraint, so every un-owned row
        would have escaped it and two agents named "Runtime" would both be accepted.
        The partial index on `owner_user_id IS NULL` is what keeps that from happening.
        """
        _row(db_session, name="Shared Thing", namespace="shared-one", owner=None)
        with pytest.raises(IntegrityError):
            _row(db_session, name="Shared Thing", namespace="shared-two", owner=None)
        db_session.rollback()

    def test_memory_namespace_stays_globally_unique(self, db_session):
        """It is the tag on every memory node the agent writes (`source_agent`), so one
        namespace must mean one agent process-wide regardless of owner."""
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        _row(db_session, name="One", namespace="collide", owner=a)
        with pytest.raises(IntegrityError):
            _row(db_session, name="Two", namespace="collide", owner=b)
        db_session.rollback()


# ---------------------------------------------------------------------------
# Namespace derivation
# ---------------------------------------------------------------------------

class TestNamespaceDerivation:
    def test_namespace_is_scoped_to_the_owner(self):
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        assert derive_user_namespace(a, "notes") != derive_user_namespace(b, "notes")

    def test_same_owner_same_slug_is_stable(self):
        owner = str(uuid.uuid4())
        assert derive_user_namespace(owner, "notes") == derive_user_namespace(owner, "notes")

    @pytest.mark.parametrize("slug", ["a", "notes", "my-agent", "my_agent", "v1.2", "a" * 64])
    def test_accepted_slugs(self, slug):
        assert SLUG_PATTERN.match(slug)

    @pytest.mark.parametrize(
        "slug",
        [
            "",                # empty
            "-leading",        # must start alphanumeric
            "Upper",           # lowercase only, so the derived namespace is case-stable
            "has space",
            "has/slash",       # must not smuggle structure into a memory address
            "a" * 65,          # over length
        ],
    )
    def test_rejected_slugs(self, slug):
        assert not SLUG_PATTERN.match(slug)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

class TestUserAgentRoutes:
    def test_create_list_patch_deactivate_restore(self, runtime_only_app, runtime_only_client, mock_db):
        user = _user()
        runtime_only_app.dependency_overrides[get_current_user] = lambda: user

        created = runtime_only_client.post(
            "/platform/agents",
            json={"name": "Scribe", "slug": "scribe", "description": "writes things down",
                  "metadata": {"provider": "codex"}},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["owner_user_id"] == user["user_id"]
        assert body["memory_namespace"] == derive_user_namespace(user["user_id"], "scribe")
        assert body["metadata"] == {"provider": "codex"}
        assert body["is_active"] is True

        listed = runtime_only_client.get("/platform/agents")
        assert [a["slug"] for a in listed.json()["agents"]] == ["scribe"]

        patched = runtime_only_client.patch(
            "/platform/agents/scribe",
            json={"name": "Scribe II", "metadata": {"provider": "claude"}},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["name"] == "Scribe II"
        assert patched.json()["metadata"] == {"provider": "claude"}
        # The namespace is immutable — it is where this agent's memory already lives.
        assert patched.json()["memory_namespace"] == body["memory_namespace"]

        gone = runtime_only_client.delete("/platform/agents/scribe")
        assert gone.status_code == 200 and gone.json()["is_active"] is False

        back = runtime_only_client.post("/platform/agents/scribe/restore")
        assert back.status_code == 200
        assert back.json()["is_active"] is True
        assert back.json()["was_active"] is False

    def test_agent_type_is_not_caller_settable(self, runtime_only_app, runtime_only_client, mock_db):
        """`agent_capability_mappings` is keyed by `agent_type`, so a user-settable type
        is a self-service claim about what class of agent this is."""
        user = _user()
        runtime_only_app.dependency_overrides[get_current_user] = lambda: user

        created = runtime_only_client.post(
            "/platform/agents",
            json={"name": "Sneaky", "slug": "sneaky", "agent_type": "system"},
        )
        assert created.status_code == 201
        assert created.json()["agent_type"] == "custom"

    def test_duplicate_slug_is_rejected_not_silently_overwritten(
        self, runtime_only_app, runtime_only_client, mock_db
    ):
        """Unlike the admin route, create is not idempotent — an idempotent update
        branch is exactly what silently rewrote platform rows before FR-12."""
        user = _user()
        runtime_only_app.dependency_overrides[get_current_user] = lambda: user

        assert runtime_only_client.post(
            "/platform/agents", json={"name": "One", "slug": "dup"}
        ).status_code == 201
        second = runtime_only_client.post(
            "/platform/agents", json={"name": "Two", "slug": "dup"}
        )
        assert second.status_code == 409

    def test_invalid_slug_is_refused(self, runtime_only_app, runtime_only_client, mock_db):
        user = _user()
        runtime_only_app.dependency_overrides[get_current_user] = lambda: user
        r = runtime_only_client.post("/platform/agents", json={"name": "X", "slug": "Has Space"})
        assert r.status_code == 422

    def test_list_never_returns_another_users_agents(
        self, runtime_only_app, runtime_only_client, mock_db
    ):
        alice, bob = _user(), _user()

        runtime_only_app.dependency_overrides[get_current_user] = lambda: alice
        runtime_only_client.post("/platform/agents", json={"name": "Alice Bot", "slug": "abot"})

        runtime_only_app.dependency_overrides[get_current_user] = lambda: bob
        seen = runtime_only_client.get("/platform/agents").json()["agents"]
        assert seen == []

    def test_another_users_agent_is_404_not_403(
        self, runtime_only_app, runtime_only_client, mock_db
    ):
        """403 would confirm that someone else holds the slug."""
        alice, bob = _user(), _user()

        runtime_only_app.dependency_overrides[get_current_user] = lambda: alice
        runtime_only_client.post("/platform/agents", json={"name": "Alice Bot", "slug": "abot"})

        runtime_only_app.dependency_overrides[get_current_user] = lambda: bob
        assert runtime_only_client.patch(
            "/platform/agents/abot", json={"name": "stolen"}
        ).status_code == 404
        assert runtime_only_client.delete("/platform/agents/abot").status_code == 404

    def test_include_shared_returns_system_agents_but_never_foreign_owned(
        self, runtime_only_app, runtime_only_client, mock_db
    ):
        alice, bob = _user(), _user()
        _row(mock_db, name="Runtime", namespace="runtime", owner=None)

        runtime_only_app.dependency_overrides[get_current_user] = lambda: alice
        runtime_only_client.post("/platform/agents", json={"name": "Alice Bot", "slug": "abot"})

        runtime_only_app.dependency_overrides[get_current_user] = lambda: bob
        shared = runtime_only_client.get("/platform/agents?include_shared=true").json()["agents"]
        names = {a["name"] for a in shared}
        assert "Runtime" in names
        assert "Alice Bot" not in names

    def test_principal_without_a_user_cannot_own_an_agent(
        self, runtime_only_app, runtime_only_client, mock_db
    ):
        """Falling through would write `owner_user_id = NULL`, i.e. create a *shared*
        agent — the one outcome an ownership route must never produce by accident."""
        runtime_only_app.dependency_overrides[get_current_user] = lambda: {"auth_type": "api_key"}
        r = runtime_only_client.post("/platform/agents", json={"name": "X", "slug": "x"})
        assert r.status_code == 400

    def test_non_uuid_principal_is_400_not_500(
        self, runtime_only_app, runtime_only_client, mock_db
    ):
        """`owner_user_id` is a UUID column, so a non-UUID id would otherwise raise deep
        inside the query and surface as an internal error with no usable reason."""
        runtime_only_app.dependency_overrides[get_current_user] = lambda: {
            "user_id": "not-a-uuid", "auth_type": "jwt"}
        r = runtime_only_client.post("/platform/agents", json={"name": "X", "slug": "x"})
        assert r.status_code == 400, r.text


# ---------------------------------------------------------------------------
# Owner-scoped read on the memory agent listing
# ---------------------------------------------------------------------------

class TestMemoryAgentListScoping:
    def test_listing_excludes_another_users_agents(self, db_session):
        from AINDY.runtime.flow_definitions_memory import memory_agents_list_node

        alice, bob = str(uuid.uuid4()), str(uuid.uuid4())
        _row(db_session, name="Alice Bot", namespace=derive_user_namespace(alice, "a"), owner=alice)
        _row(db_session, name="Bob Bot", namespace=derive_user_namespace(bob, "b"), owner=bob)
        _row(db_session, name="Runtime", namespace="runtime", owner=None)

        result = memory_agents_list_node({}, {"db": db_session, "user_id": bob})
        assert result["status"] == "SUCCESS", result
        names = {a["name"] for a in result["output_patch"]["memory_agents_list_result"]["agents"]}

        assert "Bob Bot" in names
        assert "Runtime" in names, "shared/system agents stay visible to everyone"
        assert "Alice Bot" not in names

    def test_inactive_agents_are_still_excluded(self, db_session):
        from AINDY.runtime.flow_definitions_memory import memory_agents_list_node

        owner = str(uuid.uuid4())
        _row(db_session, name="Retired", namespace=derive_user_namespace(owner, "r"),
             owner=owner, active=False)

        result = memory_agents_list_node({}, {"db": db_session, "user_id": owner})
        names = {a["name"] for a in result["output_patch"]["memory_agents_list_result"]["agents"]}
        assert "Retired" not in names


# ---------------------------------------------------------------------------
# One roster, one source
# ---------------------------------------------------------------------------

def test_reserved_namespace_set_is_derived_from_the_spec_list():
    """The set (what an app may not register) and the list (what the platform seeds)
    described one roster and were maintained separately. Now one declaration."""
    assert SYSTEM_AGENTS == {spec["namespace"] for spec in SYSTEM_AGENT_SPECS}
    assert len(SYSTEM_AGENT_SPECS) == 7
