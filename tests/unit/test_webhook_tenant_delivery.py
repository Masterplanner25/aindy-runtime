"""A subscription receives its owner's events; only an operator's receives every tenant's.

The decision is made at delivery, so a subscription restored at boot and an owner who stops being
an operator both get the right answer.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from AINDY.db.database import Base
from AINDY.db.models.user import User
from AINDY.platform_layer import event_service
from tests.fixtures.db import build_private_engine

pytestmark = pytest.mark.runtime_only

_ADMIN = uuid.UUID("00000000-0000-0000-0000-00000000a001")
_OWNER = uuid.UUID("00000000-0000-0000-0000-00000000a002")
_OTHER = uuid.UUID("00000000-0000-0000-0000-00000000a003")


@pytest.fixture
def users(monkeypatch, tmp_path):
    engine = build_private_engine(tmp_path / "users.db")
    Base.metadata.tables["users"].create(bind=engine, checkfirst=True)
    factory = sessionmaker(bind=engine)
    session = factory()
    for user_id, is_admin in ((_ADMIN, True), (_OWNER, False), (_OTHER, False)):
        session.add(
            User(
                id=user_id,
                email=f"{user_id}@example.test",
                hashed_password="x",
                is_admin=is_admin,
            )
        )
    session.commit()
    session.close()
    monkeypatch.setattr("AINDY.db.database.SessionLocal", factory)
    event_service._operator_cache.clear()
    yield factory
    event_service._operator_cache.clear()
    engine.dispose()


@pytest.fixture
def subscribe(monkeypatch):
    monkeypatch.setattr(event_service, "_SUBSCRIPTIONS", {})

    def _add(created_by, event_type="*"):
        sub_id = str(uuid.uuid4())
        event_service._SUBSCRIPTIONS[sub_id] = {
            "id": sub_id,
            "event_type": event_type,
            "callback_url": "https://example.test/hook",
            "created_by": str(created_by) if created_by else None,
            "_secret": None,
        }
        return sub_id

    return _add


@pytest.fixture
def delivered(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(event_service, "_deliver_one", lambda sub, body: seen.append(sub["id"]))
    return seen


def _dispatch(user_id):
    return event_service.dispatch_webhooks(
        event_type="execution.completed",
        event_id=str(uuid.uuid4()),
        payload={"detail": "x"},
        user_id=str(user_id) if user_id else None,
        trace_id=None,
        source="test",
    )


def test_control_an_owner_receives_their_own_event(users, subscribe, delivered):
    sub = subscribe(_OWNER)
    assert _dispatch(_OWNER) == 1
    assert delivered == [sub]


def test_an_owner_does_not_receive_another_tenants_event(users, subscribe, delivered):
    subscribe(_OWNER)
    assert _dispatch(_OTHER) == 0
    assert delivered == []


def test_an_owner_does_not_receive_an_event_with_no_tenant(users, subscribe, delivered):
    subscribe(_OWNER)
    assert _dispatch(None) == 0
    assert delivered == []


@pytest.mark.parametrize("event_user", [_OTHER, None], ids=["other_tenant", "no_tenant"])
def test_an_operators_subscription_receives_every_tenant(users, subscribe, delivered, event_user):
    sub = subscribe(_ADMIN)
    assert _dispatch(event_user) == 1
    assert delivered == [sub]


def test_a_subscription_with_no_owner_was_registered_by_the_deployment(users, subscribe, delivered):
    """A manifest or bootstrap subscription has no creator, and only the deployment can add one."""
    sub = subscribe(None)
    assert _dispatch(_OTHER) == 1
    assert delivered == [sub]


def test_a_mixed_set_delivers_to_exactly_who_may_see_it(users, subscribe, delivered):
    mine = subscribe(_OWNER)
    other = subscribe(_OTHER)
    operator = subscribe(_ADMIN)

    assert _dispatch(_OWNER) == 2
    assert set(delivered) == {mine, operator}
    assert other not in delivered


def test_a_demoted_operator_stops_receiving_once_the_cache_expires(users, subscribe, delivered, monkeypatch):
    subscribe(_ADMIN)
    assert _dispatch(_OTHER) == 1

    session = users()
    session.query(User).filter(User.id == _ADMIN).update({"is_admin": False})
    session.commit()
    session.close()
    monkeypatch.setattr(event_service, "_OPERATOR_CACHE_TTL_SECONDS", 0.0)

    assert _dispatch(_OTHER) == 0


def test_an_unreadable_owner_fails_closed(subscribe, delivered, monkeypatch):
    def _broken():
        raise RuntimeError("database down")

    monkeypatch.setattr("AINDY.db.database.SessionLocal", _broken)
    event_service._operator_cache.clear()
    subscribe(_OWNER)

    assert _dispatch(_OTHER) == 0
    assert event_service._operator_cache == {}, "a failed read must not be remembered"


@pytest.mark.parametrize("owner", ["not-a-uuid", str(uuid.uuid4())], ids=["unparseable", "no_such_user"])
def test_an_owner_who_is_not_a_known_user_is_not_an_operator(users, subscribe, delivered, owner):
    subscribe(owner)
    assert _dispatch(_OTHER) == 0
