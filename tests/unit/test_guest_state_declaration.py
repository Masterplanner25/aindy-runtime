"""ORCHESTRATOR-SPLIT-1 store 4 — the runtime declares the guest workflow store.

nodus's workflow framework keeps its own run records, created inside the guest VM because
the runtime appends ``run_workflow(<name>)`` to the guest script source. The host reads none
of them. **That does not make the substrate a non-decision**: at nodus 6.0.0 the default
store flips ``LocalWorkflowStore`` (JSON) → ``SQLiteWorkflowStore`` and the two cannot read
each other's records, so an undeclared host changes durability substrate on a schedule it
does not set. Approved under ``AGENT_WORKING_RULES`` §8; see
``docs/runtime/WORKFLOW_STORE_DECLARATION_PROPOSAL.md``.

★ **What this file is careful about, and why.** The easy version of these tests asserts that
a dict got two keys — which would pass just as happily if nothing ever called the function,
or if nodus read different variable names than the ones we write. So:

- ``test_nodus_own_readers_agree`` asserts through **nodus's own resolvers**, not our dict.
  If nodus renames a variable, our declaration silently stops working and this fails.
- The two entry-point tests **drive ``main()`` and ``serve_forever()`` for real** rather than
  reading their source. A source assertion cannot tell a call from a comment
  (``CLAUDE.md`` → *a source-text assertion is a supplement, never the coverage*).
- A liveness control runs first, so the "is declared" assertions cannot pass on a worker
  that is simply broken — the shape that scored 4/7 on ``EVENTBUS-COVERAGE-1``'s first draft.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.runtime_only

pytest.importorskip("nodus.runtime.embedding")

from AINDY.runtime import nodus_worker  # noqa: E402

_REPO_ROOT = str(Path(__file__).resolve().parents[2])


# ── Liveness control ─────────────────────────────────────────────────────────


def test_liveness_a_benign_script_still_succeeds():
    """★ Without this, every assertion below can pass on a worker that runs nothing."""
    result = nodus_worker.run_one(
        {"script": 'set_state("x", 41 + 1)\n', "state": {}, "context": {"user_id": "t"}}
    )
    assert result["status"] == "success", result
    assert result["output_state"].get("x") == 42


# ── The declaration itself ───────────────────────────────────────────────────


def test_declares_backend_and_sweep_into_an_empty_environment():
    env: dict[str, str] = {}
    applied = nodus_worker.declare_guest_state_environment(env)

    assert env["NODUS_WORKFLOW_STORE_BACKEND"] == "sqlite"
    assert env["NODUS_WORKFLOW_AUTOSWEEP"] == "0"
    assert applied == env


def test_the_root_is_deliberately_not_defaulted():
    """There is no portable correct absolute path, and an ``AINDY_``-prefixed alias would be
    a second vocabulary for a question nodus already names. Deployment sets it."""
    env: dict[str, str] = {}
    nodus_worker.declare_guest_state_environment(env)

    assert "NODUS_RUN_STATE_ROOT" not in env
    # And emphatically not the legacy half-relocating variable.
    assert "NODUS_WORKFLOW_STORE_ROOT" not in env


@pytest.mark.parametrize(
    "key,operator_value",
    [
        ("NODUS_WORKFLOW_STORE_BACKEND", "local"),
        ("NODUS_WORKFLOW_AUTOSWEEP", "1"),
    ],
)
def test_an_operator_value_is_never_overwritten(key, operator_value):
    """The runtime's job is to ensure the question is answered, not to win it."""
    env = {key: operator_value}
    applied = nodus_worker.declare_guest_state_environment(env)

    assert env[key] == operator_value
    assert key not in applied, "reported as applied when the operator had already set it"


def test_a_blank_value_counts_as_unset():
    """Matches nodus's own ``workflow_store_backend_from_env``, which returns None for "".

    An empty string is what a compose file leaves behind for an unset interpolation, so
    treating it as *answered* would silently keep the undeclared default.
    """
    env = {"NODUS_WORKFLOW_STORE_BACKEND": "   ", "NODUS_WORKFLOW_AUTOSWEEP": ""}
    nodus_worker.declare_guest_state_environment(env)

    assert env["NODUS_WORKFLOW_STORE_BACKEND"] == "sqlite"
    assert env["NODUS_WORKFLOW_AUTOSWEEP"] == "0"


def test_is_idempotent():
    env: dict[str, str] = {}
    nodus_worker.declare_guest_state_environment(env)
    applied_again = nodus_worker.declare_guest_state_environment(env)

    assert applied_again == {}, "a second call re-applied a value it had already declared"


# ── ★ The mechanism: nodus's own readers must agree ──────────────────────────


def test_nodus_own_readers_agree(monkeypatch):
    """★ Assert through nodus's resolvers, not our dict.

    This is the test that fails if nodus renames a variable underneath us — the case where
    the declaration is still 'applied' and no longer configures anything.
    """
    from nodus_lang_workflow import store as nodus_store
    from nodus_lang_workflow import runner as nodus_runner

    for key in ("NODUS_WORKFLOW_STORE_BACKEND", "NODUS_WORKFLOW_AUTOSWEEP"):
        monkeypatch.delenv(key, raising=False)

    # Precondition: undeclared, nodus reports no backend and an armed sweep.
    assert nodus_store.workflow_store_backend_from_env() is None
    assert nodus_runner._autosweep_enabled() is True

    nodus_worker.declare_guest_state_environment()

    assert nodus_store.workflow_store_backend_from_env() == "sqlite"
    assert nodus_runner._autosweep_enabled() is False


def test_the_declared_backend_actually_builds_a_sqlite_store(tmp_path, monkeypatch):
    """One step further: the declared name is one nodus's factory accepts and routes."""
    from nodus_lang_workflow.store import create_workflow_store, workflow_store_backend_from_env

    monkeypatch.delenv("NODUS_WORKFLOW_STORE_BACKEND", raising=False)
    nodus_worker.declare_guest_state_environment()

    store = create_workflow_store(
        backend=workflow_store_backend_from_env(),
        path=str(tmp_path / "wf.sqlite3"),
    )
    assert store.store_info()["backend"] == "sqlite"


# ── ★ The entry points, driven rather than read ──────────────────────────────


def test_main_entry_point_declares(monkeypatch):
    """Drives the real ``main()``. It reads stdin and writes stdout; neither is redirected
    at the fd level, so it is safe in-process."""
    for key in ("NODUS_WORKFLOW_STORE_BACKEND", "NODUS_WORKFLOW_AUTOSWEEP"):
        monkeypatch.delenv(key, raising=False)

    payload = {"script": 'set_state("x", 1)\n', "state": {}, "context": {"user_id": "t"}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    assert nodus_worker.main() == 0
    assert os.environ["NODUS_WORKFLOW_STORE_BACKEND"] == "sqlite"
    assert os.environ["NODUS_WORKFLOW_AUTOSWEEP"] == "0"


def test_serve_entry_point_declares(tmp_path):
    """Drives the real ``serve_forever()`` in a SUBPROCESS.

    It cannot be driven in-process: it ``os.dup2``s devnull over stdout's file descriptor,
    which would silence the test run itself. It leaves **stderr** alone, which is the
    observation channel used here. stdin is at EOF, so the loop returns 0 immediately.
    """
    child = textwrap.dedent(
        f"""
        import os, sys
        sys.path.insert(0, {_REPO_ROOT!r})
        for k in ("NODUS_WORKFLOW_STORE_BACKEND", "NODUS_WORKFLOW_AUTOSWEEP"):
            os.environ.pop(k, None)
        from AINDY.runtime import nodus_worker as w
        rc = w.serve_forever()
        print(
            "RESULT",
            rc,
            os.environ.get("NODUS_WORKFLOW_STORE_BACKEND"),
            os.environ.get("NODUS_WORKFLOW_AUTOSWEEP"),
            file=sys.stderr,
        )
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", child],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert "RESULT" in proc.stderr, f"child never reached the assertion:\n{proc.stderr}"
    line = [ln for ln in proc.stderr.splitlines() if ln.startswith("RESULT")][-1]
    _, rc, backend, autosweep = line.split()

    assert rc == "0"
    assert backend == "sqlite"
    assert autosweep == "0"


# ── ★ End-to-end: a real guest workflow, a real worker, a real filesystem ────


_WORKFLOW_SRC = """workflow build {
  step fetch {
    let n = 1
  }
  step compile after fetch {
    let m = 2
  }
}
run_workflow(build)
"""


def _run_worker_in(cwd, state_root, extra_env):
    """Spawn the real one-shot worker exactly as `nodus_runtime_adapter` does."""
    env = dict(os.environ)
    for key in (
        "NODUS_WORKFLOW_STORE_BACKEND",
        "NODUS_WORKFLOW_AUTOSWEEP",
        "NODUS_RUN_STATE_ROOT",
        "NODUS_WORKFLOW_STORE_ROOT",
    ):
        env.pop(key, None)
    env["PYTHONPATH"] = _REPO_ROOT
    env["NODUS_RUN_STATE_ROOT"] = str(state_root)
    env.update(extra_env)

    payload = {"script": _WORKFLOW_SRC, "state": {}, "context": {"user_id": "e2e"}}
    proc = subprocess.run(
        [sys.executable, str(Path(_REPO_ROOT) / "AINDY" / "runtime" / "nodus_worker.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(cwd),
        timeout=300,
        env=env,
    )
    assert proc.stdout.strip(), f"worker produced no result:\n{proc.stderr[-2000:]}"
    return json.loads(proc.stdout)


def _sqlite_rows(path):
    import sqlite3

    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("select count(*) from workflow_runs").fetchone()[0]
    finally:
        conn.close()


def test_a_real_guest_workflow_lands_in_sqlite_under_the_declared_root(tmp_path):
    """★ The verification the proposal's §8 asks for: assert the mechanism, not the setting.

    Every other test here proves the runtime *says* sqlite. This one proves a real workflow,
    run through the real entry point, actually writes there — and that nothing is left at the
    working directory, which is where 629 records accumulated in the repo root.
    """
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    state_root = tmp_path / "state"

    result = _run_worker_in(cwd, state_root, {})
    assert result["status"] == "success", result

    assert (state_root / "workflow_framework.sqlite3").is_file(), "no sqlite store was created"
    assert _sqlite_rows(state_root / "workflow_framework.sqlite3") == 1

    json_runs = state_root / "workflow_framework" / "runs"
    assert not json_runs.exists() or not list(json_runs.iterdir()), "JSON records were written"

    # ★ Both halves of a run's state move together — the reason NODUS_RUN_STATE_ROOT is the
    #   right variable and the legacy NODUS_WORKFLOW_STORE_ROOT is not.
    assert (state_root / "graphs").is_dir(), "the graph half did not follow the record half"

    # ★ And nothing is left where it used to accumulate.
    assert not (cwd / ".nodus").exists(), "guest state was written to the working directory"


def test_an_operator_pinning_local_still_gets_json(tmp_path):
    """Control for the test above. Without it, 'sqlite was written' could equally mean the
    declaration is ignored and sqlite is simply the default — the assertion would pass either
    way, and the operator-override guarantee would be untested end to end."""
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    state_root = tmp_path / "state"

    result = _run_worker_in(cwd, state_root, {"NODUS_WORKFLOW_STORE_BACKEND": "local"})
    assert result["status"] == "success", result

    assert not (state_root / "workflow_framework.sqlite3").exists(), "declaration overrode the operator"
    assert list((state_root / "workflow_framework" / "runs").iterdir()), "no JSON record written"
