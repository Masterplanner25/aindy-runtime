"""EXEC-ENV-BIND-1 phase 2 — the guest path *asks* for its environment.

Companion to ``test_guest_confinement.py``, which proves the three ambient capabilities are
denied. This file covers the fourth bound that entry left open, and the mechanism that now
supplies all four.

★ **The residual this closes, stated precisely.** ``GUEST-CONFINE-1``'s fix landed three of its
own four steps. ``nodus_worker.py`` used to carry the comment *"the VM already confines
filesystem access: `allowed_paths` defaults to the cwd"* — true of nodus and misleading here,
because **nothing sets the worker's cwd**. Neither ``nodus_worker_pool.WarmNodusWorker`` nor
``nodus_runtime_adapter``'s ``subprocess.run`` passes ``cwd=``, so the guest inherited the
*server's* working directory: ``/home/aindy`` in Docker, which holds ``alembic/`` — a guest could
write migrations that run on next boot — and the repo root in dev, which holds ``AINDY/.env``.

The escape was closed in August; the **bound** was an undeclared inherited default until now.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.runtime_only

pytest.importorskip("nodus.runtime.embedding")

from AINDY.core.execution_environment import (  # noqa: E402
    ExecutionEnvironmentSpec,
    Authority,
    Visibility,
    clamp_to_floor,
    guest_floor,
)


def _capture_vm_kwargs(payload: dict) -> dict:
    """Run a benign script and return the kwargs the worker actually passed to NodusRuntime."""
    import nodus.runtime.embedding as embedding

    from AINDY.runtime import nodus_worker

    captured: dict = {}
    original = embedding.NodusRuntime

    class _Recording(original):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)
            captured["_allowed_paths_existed"] = [
                (p, os.path.isdir(p)) for p in (kwargs.get("allowed_paths") or [])
            ]
            super().__init__(*args, **kwargs)

    embedding.NodusRuntime = _Recording
    try:
        nodus_worker.run_one(
            {
                "script": 'set_state("x", 1)\n',
                "state": {},
                "context": {"user_id": "env-bind-test"},
                **payload,
            }
        )
    finally:
        embedding.NodusRuntime = original
    return captured


# ── Liveness control ─────────────────────────────────────────────────────────


def test_liveness_a_benign_script_still_succeeds():
    """★ Without this, every 'is confined' assertion below passes on a worker that cannot run
    anything at all — the shape that scored 4/7 on EVENTBUS-COVERAGE-1's first draft."""
    from AINDY.runtime import nodus_worker

    result = nodus_worker.run_one(
        {"script": 'set_state("x", 41 + 1)\n', "state": {}, "context": {"user_id": "t"}}
    )
    assert result.get("status") == "success", result
    assert result["output_state"]["x"] == 42


# ── The residual: allowed_paths is explicit and is not the server's cwd ──────


def test_allowed_paths_is_passed_explicitly():
    """★ The core of phase 2. Left unspecified, nodus resolves `[os.getcwd()]` at construction."""
    captured = _capture_vm_kwargs({})
    assert "allowed_paths" in captured, (
        "allowed_paths was not passed — the guest is back to inheriting the server's cwd, "
        "which is GUEST-CONFINE-1's residual reopened"
    )
    assert captured["allowed_paths"], "allowed_paths must name a real scratch root, not be empty"


def test_the_guest_root_is_not_the_server_working_directory():
    """The bound is the whole point: /home/aindy holds alembic/, the repo root holds AINDY/.env."""
    captured = _capture_vm_kwargs({})
    cwd = Path(os.getcwd()).resolve()

    for raw in captured["allowed_paths"]:
        root = Path(raw).resolve()
        assert root != cwd, f"guest root is the server cwd ({root})"
        assert cwd not in root.parents, f"guest root {root} sits inside the server cwd"
        assert root not in cwd.parents, f"guest root {root} CONTAINS the server cwd"


def test_the_scratch_root_exists_while_the_vm_does():
    """The TemporaryDirectory must outlive construction — a dropped reference would remove the
    only path the guest may touch, mid-run."""
    captured = _capture_vm_kwargs({})
    assert captured["_allowed_paths_existed"], "no roots captured"
    for path, existed in captured["_allowed_paths_existed"]:
        assert existed, f"guest root {path} did not exist when the VM was constructed"


def test_each_execution_gets_its_own_scratch_root():
    """A warm worker serves many requests in one process (NODUS-WARMPOOL-1); scratch must not be
    shared between them, or one tenant's leftovers are readable by the next."""
    first = _capture_vm_kwargs({})["allowed_paths"]
    second = _capture_vm_kwargs({})["allowed_paths"]
    assert set(first).isdisjoint(second), f"scratch root reused across executions: {first}"


# ── NODUS_ALLOWED_PATHS is now inert ─────────────────────────────────────────


def test_the_env_escape_hatch_can_no_longer_widen_the_guest(monkeypatch, tmp_path):
    """★ nodus reads NODUS_ALLOWED_PATHS only on its unspecified-default branch, so passing
    allowed_paths explicitly makes the variable inert. That is a deliberate behaviour change and
    the safe direction: an operator can no longer widen the guest's filesystem bound out-of-band.
    """
    escape = tmp_path / "escape-hatch"
    escape.mkdir()
    monkeypatch.setenv("NODUS_ALLOWED_PATHS", str(escape))

    captured = _capture_vm_kwargs({})

    assert str(escape) not in [str(p) for p in captured["allowed_paths"]], (
        "NODUS_ALLOWED_PATHS widened the guest — allowed_paths is not being passed explicitly"
    )


# ── The safety property: a guest cannot widen its own sandbox ────────────────


def test_a_permissive_declared_spec_cannot_widen_the_guest():
    """★ THE safety property of phase 2. A declared spec is clamped to the guest floor, so a
    script that arrives with a permissive descriptor gets no more than GUEST-CONFINE-1 allows."""
    greedy = ExecutionEnvironmentSpec(
        visibility=Visibility(filesystem="host", env="inherit"),
        authority=Authority(network="open", subprocess=True),
    )
    captured = _capture_vm_kwargs({"env_spec": greedy.to_dict()})

    assert captured.get("allow_subprocess") is False
    assert captured.get("allow_network") is False
    assert captured.get("allow_env") is False
    assert captured.get("allowed_paths"), "filesystem=host must be clamped, not granted"


def test_a_stricter_declared_spec_does_narrow_further():
    """The clamp is an intersection, not a floor override — asking for MORE must still work."""
    strict = ExecutionEnvironmentSpec(visibility=Visibility(filesystem="none", env="none"))
    captured = _capture_vm_kwargs({"env_spec": strict.to_dict()})
    assert captured.get("allowed_paths") == [], "filesystem=none must yield no readable roots"


def test_a_malformed_spec_falls_back_to_the_floor_rather_than_failing():
    """★ Validation belongs at require_execution_unit; by the time a payload reaches the worker
    the gate has accepted it. Falling back to the floor is the most restrictive option, so the
    failure direction is safe."""
    captured = _capture_vm_kwargs({"env_spec": {"authority": {"network": "wide-open"}}})
    assert captured.get("allow_network") is False
    assert captured.get("allow_subprocess") is False
    assert captured.get("allowed_paths"), "fallback must still bound the filesystem"


# ── The floor itself ─────────────────────────────────────────────────────────


def test_the_guest_floor_denies_all_three_ambient_capabilities():
    """GUEST-CONFINE-1, restated as a declaration rather than three literals."""
    floor = guest_floor()
    assert floor.authority.subprocess is False
    assert floor.authority.network == "none"
    assert floor.visibility.env == "none"
    assert floor.visibility.filesystem != "host"


def test_clamping_any_spec_to_the_guest_floor_never_grants_more():
    """Property check across the axis vocabularies rather than one hand-picked spec."""
    from AINDY.core.execution_environment import ENV_ORDER, FILESYSTEM_ORDER, NETWORK_ORDER

    floor = guest_floor()
    for fs in FILESYSTEM_ORDER:
        for env in ENV_ORDER:
            for net in NETWORK_ORDER:
                for sub in (True, False):
                    effective, _ = clamp_to_floor(
                        ExecutionEnvironmentSpec(
                            visibility=Visibility(filesystem=fs, env=env),
                            authority=Authority(network=net, subprocess=sub),
                        ),
                        floor,
                    )
                    assert effective.authority.subprocess is False
                    assert effective.authority.network == "none"
                    assert effective.visibility.env == "none"
                    assert FILESYSTEM_ORDER.index(effective.visibility.filesystem) <= (
                        FILESYSTEM_ORDER.index(floor.visibility.filesystem)
                    )
