"""Scripts and workflows share one namespace, so a name belongs to whoever registered it.

The routes are called for real, because the status code is the contract. Every caller holds
`flow.execute`, so a 403 here can only come from the owner check.
"""
from __future__ import annotations

import sys

import pytest
from sqlalchemy.orm import sessionmaker

from AINDY.db.database import Base
from AINDY.db.models.nodus_workflow import NodusWorkflow
from AINDY.platform_layer import nodus_script_store as store
from AINDY.runtime import nodus_workflow_registry as nwr
from tests.fixtures.db import build_private_engine

pytestmark = pytest.mark.runtime_only

_ALICE = "00000000-0000-0000-0000-00000000b001"
_BOB = "00000000-0000-0000-0000-00000000b002"
_SCRIPT = "let x = 1"
_REPLACEMENT = "let x = 2"
_WORKFLOW = "workflow demo {\n  step a {\n    let x = 1\n  }\n}\n"
_WORKFLOW_2 = "workflow demo {\n  step a {\n    let x = 2\n  }\n}\n"


def _key(user: str, *scopes: str) -> dict:
    return {
        "sub": user,
        "user_id": user,
        "auth_type": "api_key",
        "api_key_scopes": list(scopes),
    }


@pytest.fixture
def act_as(runtime_only_app):
    from AINDY.services.auth_service import get_current_user

    def _apply(principal: dict):
        runtime_only_app.dependency_overrides[get_current_user] = lambda: principal

    yield _apply
    runtime_only_app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def scripts(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "_NODUS_SCRIPT_REGISTRY", {})
    monkeypatch.setattr(store, "_SCRIPTS_DIR", tmp_path)
    import AINDY.routes.platform.nodus_router  # noqa: F401  (the package attribute is the router)

    monkeypatch.setattr(sys.modules["AINDY.routes.platform.nodus_router"], "_SCRIPTS_DIR", tmp_path)
    return tmp_path


def _upload(client, name="shared", content=_SCRIPT, overwrite=False):
    return client.post(
        "/platform/nodus/upload",
        json={"name": name, "content": content, "overwrite": overwrite},
    )


def test_control_an_owner_can_replace_their_own_script(runtime_only_client, act_as, scripts):
    act_as(_key(_ALICE, "flow.execute"))
    assert _upload(runtime_only_client).status_code == 201
    assert _upload(runtime_only_client, content=_REPLACEMENT, overwrite=True).status_code == 201
    assert store.load_script_source("shared") == _REPLACEMENT


def test_another_key_cannot_replace_a_script(runtime_only_client, act_as, scripts):
    act_as(_key(_ALICE, "flow.execute"))
    assert _upload(runtime_only_client).status_code == 201

    act_as(_key(_BOB, "flow.execute"))
    response = _upload(runtime_only_client, content=_REPLACEMENT, overwrite=True)

    assert response.status_code == 403, response.text
    assert store.load_script_source("shared") == _SCRIPT
    assert (scripts / "shared.nodus").read_text(encoding="utf-8") == _SCRIPT


def test_an_operator_can_replace_a_script(runtime_only_client, act_as, scripts):
    act_as(_key(_ALICE, "flow.execute"))
    assert _upload(runtime_only_client).status_code == 201

    act_as(_key(_BOB, "flow.execute", "platform.admin"))
    assert _upload(runtime_only_client, content=_REPLACEMENT, overwrite=True).status_code == 201
    assert store.load_script_source("shared") == _REPLACEMENT


def test_a_script_on_disk_counts_as_existing(runtime_only_client, act_as, scripts):
    """After a restart the registry is empty and only the file remains."""
    (scripts / "shared.nodus").write_text(_SCRIPT, encoding="utf-8")
    act_as(_key(_BOB, "flow.execute"))

    assert _upload(runtime_only_client, content=_REPLACEMENT).status_code == 409
    assert (scripts / "shared.nodus").read_text(encoding="utf-8") == _SCRIPT


def test_a_script_on_disk_has_no_owner_so_only_an_operator_replaces_it(runtime_only_client, act_as, scripts):
    (scripts / "shared.nodus").write_text(_SCRIPT, encoding="utf-8")

    act_as(_key(_BOB, "flow.execute"))
    assert _upload(runtime_only_client, content=_REPLACEMENT, overwrite=True).status_code == 403
    assert (scripts / "shared.nodus").read_text(encoding="utf-8") == _SCRIPT

    act_as(_key(_BOB, "flow.execute", "platform.admin"))
    assert _upload(runtime_only_client, content=_REPLACEMENT, overwrite=True).status_code == 201
    assert (scripts / "shared.nodus").read_text(encoding="utf-8") == _REPLACEMENT


def test_the_store_itself_counts_a_file_on_disk(scripts):
    """Not only through the route: any caller of the store that enforces an owner."""
    (scripts / "shared.nodus").write_text(_SCRIPT, encoding="utf-8")

    with pytest.raises(store.NodusScriptOwnerError):
        store.store_script(
            name="shared",
            content=_REPLACEMENT,
            description=None,
            uploaded_at=None,
            uploaded_by=_BOB,
            enforce_owner=True,
        )

    assert store.load_script_source("shared") == _SCRIPT


# -- workflows ---------------------------------------------------------------------------------


@pytest.fixture
def workflows(monkeypatch):
    monkeypatch.setattr(nwr, "_NODUS_WORKFLOWS", {})


def _register(client, source=_WORKFLOW, name="shared_flow"):
    return client.post(
        "/platform/nodus/flow",
        json={"flow_name": name, "script": source, "register": True, "run": False},
    )


def test_control_an_owner_can_replace_their_own_workflow(runtime_only_client, act_as, workflows):
    act_as(_key(_ALICE, "flow.execute"))
    first = _register(runtime_only_client)
    assert first.status_code == 200, first.text
    assert _register(runtime_only_client, _WORKFLOW_2).status_code == 200
    meta = nwr.get_nodus_workflow("shared_flow")
    assert meta["source"] == _WORKFLOW_2
    assert meta["created_by"] == _ALICE


def test_another_key_cannot_replace_a_workflow(runtime_only_client, act_as, workflows):
    act_as(_key(_ALICE, "flow.execute"))
    assert _register(runtime_only_client).status_code == 200

    act_as(_key(_BOB, "flow.execute"))
    response = _register(runtime_only_client, _WORKFLOW_2)

    assert response.status_code == 403, response.text
    assert nwr.get_nodus_workflow("shared_flow")["source"] == _WORKFLOW


def test_a_key_cannot_replace_a_workflow_a_plugin_registered(runtime_only_client, act_as, workflows):
    nwr.register_nodus_workflow("shared_flow", _WORKFLOW, allow_legacy_missing_provenance=True)
    act_as(_key(_BOB, "flow.execute"))

    assert _register(runtime_only_client, _WORKFLOW_2).status_code == 403
    assert nwr.get_nodus_workflow("shared_flow")["source"] == _WORKFLOW


def test_an_operator_can_replace_a_workflow_and_the_owner_stays(runtime_only_client, act_as, workflows):
    act_as(_key(_ALICE, "flow.execute"))
    assert _register(runtime_only_client).status_code == 200

    act_as(_key(_BOB, "flow.execute", "platform.admin"))
    assert _register(runtime_only_client, _WORKFLOW_2).status_code == 200
    meta = nwr.get_nodus_workflow("shared_flow")
    assert meta["source"] == _WORKFLOW_2
    assert meta["created_by"] == _ALICE


# -- the registry against its table ------------------------------------------------------------


@pytest.fixture
def session(tmp_path):
    engine = build_private_engine(tmp_path / "workflows.db")
    Base.metadata.tables["nodus_workflows"].create(bind=engine, checkfirst=True)
    db = sessionmaker(bind=engine)()
    yield db
    db.close()
    engine.dispose()


def _reg(db, source, *, user, enforce=True):
    return nwr.register_nodus_workflow(
        "persisted",
        source,
        allow_legacy_missing_provenance=True,
        overwrite=True,
        created_by=user,
        enforce_owner=enforce,
        db=db,
    )


def test_a_row_another_process_wrote_still_has_its_owner(workflows, session):
    """The in-memory registry is per process, so memory alone cannot answer who owns a name."""
    _reg(session, _WORKFLOW, user=_ALICE)
    nwr._NODUS_WORKFLOWS.clear()

    with pytest.raises(nwr.NodusWorkflowOwnerError):
        _reg(session, _WORKFLOW_2, user=_BOB)

    assert session.query(NodusWorkflow).filter_by(name="persisted").one().source == _WORKFLOW
    assert nwr.get_nodus_workflow("persisted") is None


def test_a_refusal_leaves_memory_and_row_alone(workflows, session):
    _reg(session, _WORKFLOW, user=_ALICE)

    with pytest.raises(nwr.NodusWorkflowOwnerError):
        _reg(session, _WORKFLOW_2, user=_BOB)

    assert nwr.get_nodus_workflow("persisted")["source"] == _WORKFLOW
    assert session.query(NodusWorkflow).filter_by(name="persisted").one().source == _WORKFLOW


def test_the_owner_survives_a_restart(workflows, session):
    _reg(session, _WORKFLOW, user=_ALICE)
    nwr._NODUS_WORKFLOWS.clear()

    assert nwr.rehydrate_nodus_workflows(session) == 1
    assert nwr.get_nodus_workflow("persisted")["created_by"] == _ALICE


def test_a_deleted_name_is_free_again(workflows, session):
    _reg(session, _WORKFLOW, user=_ALICE)
    assert nwr.delete_nodus_workflow("persisted", db=session) is True

    meta = _reg(session, _WORKFLOW_2, user=_BOB)

    assert meta["created_by"] == _BOB
    assert session.query(NodusWorkflow).filter_by(name="persisted").one().created_by == _BOB
