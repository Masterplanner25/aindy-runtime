"""FS-SCOPE-1 phase 2 — an isolated tool's declared filesystem scope is enforced in its worker.

Before: a tool declaring `visibility.filesystem = scoped|readonly|none` got `cwd=<scratch>` and
nothing else, so its worker could open any path the OS allowed. Now the parent resolves a
`FilesystemDecision`, the worker installs it as a process-wide audit hook before anything loads,
and the envelope reports the mechanism the worker applied.

★ An audit hook cannot be removed, so every enforcement test runs in a CHILD interpreter: the
real `tool_worker.run_one` with a probe tool registered in that child. Each refusal has a control
in the same child showing the same operation succeeds where the scope allows it, so a "denied"
cannot pass on a worker that denies everything.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.runtime_only

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_CHILD = textwrap.dedent('''
    import json, os, sys
    from AINDY.agents import tool_registry as tr
    from AINDY.agents import tool_worker

    tr._ensure_tools_loaded = lambda: None  # the probe is registered right here

    def probe(args, user_id, db):
        out = {}
        for name, op, path in args["ops"]:
            try:
                if op == "read":
                    with open(path, encoding="utf-8") as fh:
                        fh.read()
                elif op == "write":
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write("x")
                elif op == "list":
                    os.listdir(path)
                elif op == "remove":
                    os.remove(path)
                elif op == "import":
                    __import__(path)
                elif op == "tempfile":
                    import tempfile
                    with tempfile.NamedTemporaryFile() as fh:
                        fh.write(b"x")
                out[name] = "ok"
            except PermissionError as exc:
                out[name] = "denied" if "FS-SCOPE-1" in str(exc) else f"other: {exc}"
            except Exception as exc:
                out[name] = f"error: {type(exc).__name__}: {exc}"
        return out

    tr.TOOL_REGISTRY["test.fs_probe"] = {"fn": probe}
    request = json.loads(sys.stdin.read())
    sys.stdout.write(json.dumps(tool_worker.run_one(request)))
''')


def _worker(ops, filesystem=None, cwd=None):
    request = {"tool_name": "test.fs_probe", "args": {"ops": ops}, "user_id": "u"}
    if filesystem is not None:
        request["filesystem"] = filesystem
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([_REPO, env.get("PYTHONPATH", "")])
    proc = subprocess.run([sys.executable, "-c", _CHILD], input=json.dumps(request), capture_output=True,
                          text=True, timeout=120, cwd=cwd or _REPO, env=env)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


@pytest.fixture
def area(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    scratch = tmp_path / "scratch"
    for d in (root, outside, scratch):
        d.mkdir()
    (root / "in.txt").write_text("in", encoding="utf-8")
    (outside / "secret.txt").write_text("s3cret", encoding="utf-8")
    (outside / "victim.txt").write_text("v", encoding="utf-8")
    return SimpleNamespace(root=str(root), outside=str(outside), scratch=str(scratch))


def _fs(mode, a, roots=None):
    return {"mode": mode, "roots": [a.root] if roots is None else roots, "scratch": a.scratch}


# ── the worker enforces ──────────────────────────────────────────────────────


def test_control_without_a_decision_nothing_is_guarded(area):
    reply = _worker([("read_out", "read", f"{area.outside}/secret.txt"),
                     ("write_out", "write", f"{area.outside}/w.txt")])
    assert reply["ok"], reply
    assert reply["result"] == {"read_out": "ok", "write_out": "ok"}
    assert reply["filesystem_mechanism"] == "none"


def test_scoped_reads_and_writes_inside_and_nothing_outside(area):
    reply = _worker([
        ("read_in", "read", f"{area.root}/in.txt"),
        ("write_in", "write", f"{area.root}/w.txt"),
        ("write_scratch", "write", f"{area.scratch}/w.txt"),
        ("read_out", "read", f"{area.outside}/secret.txt"),
        ("write_out", "write", f"{area.outside}/w.txt"),
        ("list_out", "list", area.outside),
        ("remove_out", "remove", f"{area.outside}/victim.txt"),
        ("escape", "read", f"{area.root}/../outside/secret.txt"),
    ], _fs("scoped", area))
    assert reply["ok"], reply
    r = reply["result"]
    assert (r["read_in"], r["write_in"], r["write_scratch"]) == ("ok", "ok", "ok"), r
    assert (r["read_out"], r["write_out"], r["list_out"], r["remove_out"], r["escape"]) == (
        "denied", "denied", "denied", "denied", "denied"), r
    assert os.path.exists(f"{area.outside}/victim.txt")
    assert not os.path.exists(f"{area.outside}/w.txt")
    assert reply["filesystem_mechanism"] == "audit_hook:worker"


def test_a_symlink_inside_a_root_cannot_reach_outside_it(area):
    link = os.path.join(area.root, "link.txt")
    try:
        os.symlink(os.path.join(area.outside, "secret.txt"), link)
    except (OSError, NotImplementedError) as exc:  # Windows without the symlink privilege
        pytest.skip(f"cannot create a symlink here: {exc}")
    reply = _worker([("via_link", "read", link), ("read_in", "read", f"{area.root}/in.txt")],
                    _fs("scoped", area))
    assert reply["result"] == {"via_link": "denied", "read_in": "ok"}, reply


def test_readonly_reads_its_roots_and_writes_nothing(area):
    reply = _worker([
        ("read_in", "read", f"{area.root}/in.txt"),
        ("write_in", "write", f"{area.root}/w.txt"),
        ("write_scratch", "write", f"{area.scratch}/w.txt"),
    ], _fs("readonly", area))
    r = reply["result"]
    assert r == {"read_in": "ok", "write_in": "denied", "write_scratch": "denied"}, r


def test_none_sees_no_file_but_code_still_imports(area):
    reply = _worker([
        ("read_in", "read", f"{area.root}/in.txt"),
        ("import", "import", "email.mime.text"),
    ], _fs("none", area))
    r = reply["result"]
    assert r == {"read_in": "denied", "import": "ok"}, r


def test_scoped_temp_files_land_in_the_scratch_root(area):
    reply = _worker([("tmp", "tempfile", "")], _fs("scoped", area))
    assert reply["result"] == {"tmp": "ok"}, reply


def test_an_unreadable_decision_installs_nothing_and_says_so(area):
    reply = _worker([("read_out", "read", f"{area.outside}/secret.txt")], {"mode": "bogus"})
    assert reply["result"] == {"read_out": "ok"}
    assert reply["filesystem_mechanism"] == "none"


# ── the parent decides and reports ───────────────────────────────────────────


def _register(monkeypatch, tr, env_spec):
    monkeypatch.setitem(tr.TOOL_REGISTRY, "test.fs_parent", {"fn": lambda **k: {}, "env_spec": env_spec})


def _drive(tr, worker_reply):
    seen = {}

    def _fake(cmd, *, payload, tool_name, run_id, spawn_kwargs):
        seen["request"] = json.loads(payload)
        seen["cwd"] = spawn_kwargs.get("cwd")
        return subprocess.CompletedProcess(cmd, 0, json.dumps(worker_reply), "")

    with patch.object(tr, "_run_worker_or_kill_on_cancel", _fake):
        envelope = tr._run_tool_out_of_process("test.fs_parent", {}, "u")
    return seen, envelope


def test_a_declared_scope_rides_the_request_and_the_envelope_reports_the_worker(monkeypatch, area):
    import AINDY.agents.tool_registry as tr

    _register(monkeypatch, tr, {"visibility": {"filesystem": "scoped", "filesystem_roots": [area.root]}})
    seen, envelope = _drive(tr, {"ok": True, "result": {}, "filesystem_mechanism": "audit_hook:worker"})
    fs = seen["request"]["filesystem"]
    assert fs["mode"] == "scoped" and fs["roots"] == [area.root]
    assert fs["scratch"] and fs["scratch"] == seen["cwd"], "the guard's scratch is not the worker's cwd"
    assert envelope["filesystem"] == {"mode": "scoped", "mechanism": "audit_hook:worker"}


def test_a_worker_that_never_applied_it_is_reported_as_none(monkeypatch, area):
    import AINDY.agents.tool_registry as tr

    _register(monkeypatch, tr, {"visibility": {"filesystem": "readonly"}})
    seen, envelope = _drive(tr, {"ok": True, "result": {}})
    assert seen["request"]["filesystem"]["mode"] == "readonly"
    assert seen["cwd"], "a read-only worker ran in the server's working directory"
    assert envelope["filesystem"] == {"mode": "readonly", "mechanism": "none"}


@pytest.mark.parametrize("env_spec", [None, {"visibility": {"filesystem": "host"}},
                                      {"visibility": {"env": "allowlist", "env_allow": []}}])
def test_a_tool_that_bounds_no_filesystem_sends_no_decision(monkeypatch, env_spec):
    import AINDY.agents.tool_registry as tr

    _register(monkeypatch, tr, env_spec)
    seen, envelope = _drive(tr, {"ok": True, "result": {}})
    assert "filesystem" not in seen["request"]
    assert "filesystem" not in envelope


@pytest.mark.parametrize("isolation, warned", [(None, True), ("insecure-dev", False)])
def test_a_scope_declared_without_isolation_is_called_out(caplog, monkeypatch, isolation, warned):
    """In-process the scope cannot apply (the tool shares the server's process); say so at
    declaration. The isolated control must stay quiet, or the warning means nothing."""
    import logging

    import AINDY.agents.tool_registry as tr

    name = f"test.fs_decl_{isolation}"
    monkeypatch.delitem(tr.TOOL_REGISTRY, name, raising=False)
    with caplog.at_level(logging.WARNING, logger="AINDY.agents.tool_registry"):
        tr.register_tool(name, risk="low", description="d", capability="c", required_capability="rc",
                         category="test", egress_scope="none", isolation=isolation,
                         env_spec={"visibility": {"filesystem": "scoped"}})(lambda args, user_id, db: {})
    tr.TOOL_REGISTRY.pop(name, None)
    hit = any("no isolation" in r.getMessage() and name in r.getMessage() for r in caplog.records)
    assert hit is warned
