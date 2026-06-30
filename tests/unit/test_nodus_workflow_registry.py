"""Unit tests for the RTR-1 Nodus workflow registration surface.

Covers register_nodus_workflow (validation, dup/overwrite, both kinds), source
persistence + boot rehydration on SQLite, list hides source, and run-by-name
dispatch. The flow-graph compiler (compile_nodus_flow) is monkeypatched where a
compiled flow is needed — its real `flow.step` DSL collides with nodus-lang
4.0.5's reserved `step` keyword (tracked separately); the registry surface is
agnostic to that.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from AINDY.db.database import Base
import AINDY.db.model_registry  # noqa: F401  (populate metadata)
from AINDY.platform_layer.extension_policy import OWNER_FIRST_PARTY_APP
from AINDY.runtime import nodus_workflow_registry as nwr


@pytest.fixture(autouse=True)
def _clean_registry():
    """Isolate the module-global registry + FLOW_REGISTRY between tests."""
    from AINDY.runtime.flow_engine import FLOW_REGISTRY

    nwr._NODUS_WORKFLOWS.clear()
    before = set(FLOW_REGISTRY)
    yield
    nwr._NODUS_WORKFLOWS.clear()
    for name in set(FLOW_REGISTRY) - before:
        FLOW_REGISTRY.pop(name, None)


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.tables["nodus_workflows"].create(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _register_script(name="wf", source="let x = 1", **kw):
    kw.setdefault("owner_class", OWNER_FIRST_PARTY_APP)
    kw.setdefault("allow_legacy_missing_provenance", True)
    return nwr.register_nodus_workflow(name, source, kind="script", **kw)


# --------------------------------------------------------------------------- #
# Registration + validation
# --------------------------------------------------------------------------- #

def test_register_script_workflow_returns_metadata():
    meta = _register_script(name="digest", source="let x = 41")
    assert meta["name"] == "digest"
    assert meta["kind"] == "script"
    assert meta["content_hash"] and len(meta["content_hash"]) == 64
    assert meta["version"] == meta["content_hash"][:12]
    assert nwr.get_nodus_workflow("digest")["source"] == "let x = 41"


def test_duplicate_without_overwrite_rejected():
    _register_script(name="dup")
    with pytest.raises(ValueError, match="already exists"):
        _register_script(name="dup", source="let x = 2")


def test_overwrite_replaces_source_and_version():
    _register_script(name="ow", source="let x = 1")
    v1 = nwr.get_nodus_workflow("ow")["version"]
    meta = _register_script(name="ow", source="let x = 99", overwrite=True)
    assert meta["version"] != v1
    assert nwr.get_nodus_workflow("ow")["source"] == "let x = 99"


@pytest.mark.parametrize(
    "name,source,kind",
    [
        ("bad name", "let x = 1", "script"),   # space in name
        ("ok", "", "script"),                   # empty source
        ("ok", "let x = 1", "bogus-kind"),      # invalid kind
    ],
)
def test_validator_rejects_bad_input(name, source, kind):
    with pytest.raises(ValueError):
        nwr.register_nodus_workflow(
            name, source, kind=kind,
            owner_class=OWNER_FIRST_PARTY_APP, allow_legacy_missing_provenance=True,
        )


def test_list_hides_source_but_get_includes_it():
    _register_script(name="secret", source="let token = 1")
    listed = nwr.list_nodus_workflows()
    assert all("source" not in w for w in listed)
    assert {w["name"] for w in listed} == {"secret"}
    assert nwr.get_nodus_workflow("secret")["source"] == "let token = 1"


# --------------------------------------------------------------------------- #
# flow-graph kind (compiler monkeypatched)
# --------------------------------------------------------------------------- #

def test_flow_graph_registers_compiled_flow(monkeypatch):
    from AINDY.runtime import nodus_flow_compiler

    fake_flow = {"start": "a", "edges": {"a": ["b"], "b": []}, "end": ["b"]}
    monkeypatch.setattr(
        nodus_flow_compiler, "compile_nodus_flow", lambda source, name: dict(fake_flow)
    )

    meta = nwr.register_nodus_workflow(
        "graph_wf", "flow.node(\"a\")", kind="flow-graph",
        owner_class=OWNER_FIRST_PARTY_APP, allow_legacy_missing_provenance=True,
    )
    from AINDY.runtime.flow_engine import FLOW_REGISTRY

    assert meta["kind"] == "flow-graph"
    assert FLOW_REGISTRY["graph_wf"]["start"] == "a"


def test_failed_compile_leaves_registry_unchanged(monkeypatch):
    from AINDY.runtime import nodus_flow_compiler

    def _boom(source, name):
        raise ValueError("compile failed")

    monkeypatch.setattr(nodus_flow_compiler, "compile_nodus_flow", _boom)
    with pytest.raises(ValueError, match="compile failed"):
        nwr.register_nodus_workflow(
            "broken", "flow.node(\"a\")", kind="flow-graph",
            owner_class=OWNER_FIRST_PARTY_APP, allow_legacy_missing_provenance=True,
        )
    assert nwr.get_nodus_workflow("broken") is None


# --------------------------------------------------------------------------- #
# Persistence + rehydration
# --------------------------------------------------------------------------- #

def test_register_persists_source_row(session):
    from AINDY.db.models.nodus_workflow import NodusWorkflow

    _register_script(name="persisted", source="let x = 7", db=session)
    row = session.query(NodusWorkflow).filter_by(name="persisted").one()
    assert row.source == "let x = 7"
    assert row.kind == "script"
    assert row.is_active is True
    assert row.content_hash and len(row.content_hash) == 64


def test_rehydrate_recompiles_active_rows(session):
    _register_script(name="rehy_a", source="let a = 1", db=session)
    _register_script(name="rehy_b", source="let b = 2", db=session)
    # Simulate a fresh process: in-memory registry is empty, rows persist.
    nwr._NODUS_WORKFLOWS.clear()
    assert nwr.get_nodus_workflow("rehy_a") is None

    count = nwr.rehydrate_nodus_workflows(session)
    assert count == 2
    assert nwr.get_nodus_workflow("rehy_a")["source"] == "let a = 1"
    assert nwr.get_nodus_workflow("rehy_b")["source"] == "let b = 2"


def test_delete_soft_deletes_and_unregisters(session):
    from AINDY.db.models.nodus_workflow import NodusWorkflow

    _register_script(name="gone", source="let x = 1", db=session)
    assert nwr.delete_nodus_workflow("gone", db=session) is True
    assert nwr.get_nodus_workflow("gone") is None
    row = session.query(NodusWorkflow).filter_by(name="gone").one()
    assert row.is_active is False
    # Rehydration skips inactive rows.
    assert nwr.rehydrate_nodus_workflows(session) == 0


# --------------------------------------------------------------------------- #
# run-by-name dispatch
# --------------------------------------------------------------------------- #

def test_run_unknown_workflow_raises():
    with pytest.raises(LookupError, match="not registered"):
        nwr.run_nodus_workflow("nope", db=None, user_id="u1")


def test_run_script_workflow_dispatches_to_script_runner(monkeypatch):
    _register_script(name="run_me", source="let x = 5")
    captured = {}

    def _fake_run(**kwargs):
        captured.update(kwargs)
        return {"status": "completed"}

    monkeypatch.setattr(
        "AINDY.runtime.nodus_execution_service.run_nodus_script_via_flow", _fake_run
    )
    result = nwr.run_nodus_workflow("run_me", db="DB", user_id="u1", input_payload={"k": 1})
    assert result["status"] == "completed"
    assert captured["script"] == "let x = 5"
    assert captured["input_payload"] == {"k": 1}
    assert captured["workflow_type"] == "nodus_workflow:run_me"
