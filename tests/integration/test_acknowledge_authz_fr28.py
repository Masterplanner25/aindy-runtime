"""FR-28 — acknowledge only a real message addressed to the acking agent.

`acknowledge_message` emitted an `agent.message.acknowledged` event unconditionally: it
acknowledged a message that never existed (phantom ack), and because `get_inbox` builds its
suppression set from every ack for the user, one agent could acknowledge another agent's message
by id and make it vanish from that agent's inbox. The fix resolves the message, authorises the
caller as its recipient, and only then emits.

Route cases (200/404/403) are driven through the real route on live Postgres (`ROUTE-GUARD-1` —
a route test must call the route; the status code is the contract). The suppression regression
and the exception contract are at the bus level, where no event is emitted so no agent_registry
FK is needed.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from AINDY.db.models.system_event import SystemEvent

pytestmark = pytest.mark.integration


def _seed_message(db, *, recipient_agent_id: str, user_id, message_type: str = "operation_request"):
    """Insert a committed agent message addressed to recipient_agent_id. agent_id column left
    NULL (the recipient lives in the payload, as publish_message writes it) so no FK is needed."""
    row = SystemEvent(
        id=uuid.uuid4(),
        type=f"agent.message.{message_type}",
        user_id=user_id,
        agent_id=None,
        payload={"message_type": message_type, "recipient_agent_id": recipient_agent_id},
    )
    db.add(row)
    db.commit()
    return str(row.id)


# ── route cases (ROUTE-GUARD-1) ───────────────────────────────────────────────


@pytest.fixture
def agent_client(runtime_only_app, auth_headers):
    """A real admin JWT (conftest `auth_headers` over `test_user`) so the normal scope
    derivation runs — the `agent.run` scope `_REQUIRE_AGENT` needs is in a session's derived
    set. The ack routes through the real dependency chain, not an override."""
    with TestClient(runtime_only_app, raise_server_exceptions=False) as client:
        client.headers.update(auth_headers)
        yield client


# These assert the route MAPS each bus outcome to the right status — the ROUTE-GUARD-1 contract
# (the status code is what a client reads). They patch `acknowledge_message` rather than seed a
# row, deliberately: pre-seeding through the savepoint `db_session` makes `test_user` invisible
# to the pipeline's own EU-claim write on PostgreSQL (a harness artifact documented under
# FR-25 b), which would 500 before the handler runs. The bus tests below drive the REAL
# resolve/authorise logic on PG, so patching the function here substitutes only behaviour that is
# verified for real a few lines down — not a fixture that blinds the test.
_ACK = "AINDY.routes.coordination_router.acknowledge_message"


def test_route_maps_a_successful_ack_to_200(agent_client):
    with patch(_ACK, return_value=str(uuid.uuid4())):
        resp = agent_client.post(
            f"/coordination/messages/{uuid.uuid4()}/acknowledge", json={"agent_id": str(uuid.uuid4())}
        )
    assert resp.status_code == 200, resp.text
    data = resp.json().get("data", resp.json())
    assert data["acknowledged"] is True


def test_route_maps_not_found_to_404(agent_client):
    from AINDY.agents.agent_message_bus import MessageNotFoundError

    with patch(_ACK, side_effect=MessageNotFoundError("nope")):
        resp = agent_client.post(
            f"/coordination/messages/{uuid.uuid4()}/acknowledge", json={"agent_id": str(uuid.uuid4())}
        )
    assert resp.status_code == 404, resp.text


def test_route_maps_not_owned_to_403(agent_client):
    from AINDY.agents.agent_message_bus import MessageNotOwnedError

    with patch(_ACK, side_effect=MessageNotOwnedError("not yours")):
        resp = agent_client.post(
            f"/coordination/messages/{uuid.uuid4()}/acknowledge", json={"agent_id": str(uuid.uuid4())}
        )
    assert resp.status_code == 403, resp.text


def test_route_rejects_a_malformed_id_with_422(agent_client):
    """UUIDPath on the param: now that the id is actually resolved downstream, a non-UUID is a
    422 at the boundary (FR-25 b's type) before the handler runs — not a 500, not a misleading
    404. No patch: validation precedes the endpoint."""
    resp = agent_client.post(
        "/coordination/messages/not-a-uuid/acknowledge", json={"agent_id": str(uuid.uuid4())}
    )
    assert resp.status_code == 422, resp.text


# ── bus-level: the exception contract + the suppression regression ────────────


def test_phantom_and_not_owned_raise_distinct_errors(db_session, test_user):
    from AINDY.agents.agent_message_bus import (
        MessageNotFoundError,
        MessageNotOwnedError,
        acknowledge_message,
    )

    with pytest.raises(MessageNotFoundError):
        acknowledge_message(db_session, message_id=str(uuid.uuid4()), agent_id=str(uuid.uuid4()), user_id=str(test_user.id))

    with pytest.raises(MessageNotFoundError):
        acknowledge_message(db_session, message_id="not-a-uuid", agent_id=str(uuid.uuid4()), user_id=str(test_user.id))

    owner = str(uuid.uuid4())
    mid = _seed_message(db_session, recipient_agent_id=owner, user_id=test_user.id)
    with pytest.raises(MessageNotOwnedError):
        acknowledge_message(db_session, message_id=mid, agent_id=str(uuid.uuid4()), user_id=str(test_user.id))


def test_one_agent_cannot_suppress_another_agents_inbox(db_session, test_user):
    """The facet with teeth: agent A acknowledging B's message must NOT remove it from B's inbox.
    Post-fix the ack is refused (MessageNotOwnedError) so no ack row is created and B still sees
    the message."""
    from AINDY.agents.agent_message_bus import (
        MessageNotOwnedError,
        acknowledge_message,
        get_inbox,
    )

    agent_b = str(uuid.uuid4())
    agent_a = str(uuid.uuid4())
    mid = _seed_message(db_session, recipient_agent_id=agent_b, user_id=test_user.id)

    before = {m["message_id"] for m in get_inbox(db_session, agent_id=agent_b, user_id=str(test_user.id))}
    assert mid in before, "seed sanity: B's message should be in B's inbox"

    with pytest.raises(MessageNotOwnedError):
        acknowledge_message(db_session, message_id=mid, agent_id=agent_a, user_id=str(test_user.id))

    after = {m["message_id"] for m in get_inbox(db_session, agent_id=agent_b, user_id=str(test_user.id))}
    assert mid in after, (
        "agent A's acknowledgement suppressed B's message from B's inbox — the cross-agent "
        "suppression FR-28 closes. The ack should have been refused and no ack row created."
    )
