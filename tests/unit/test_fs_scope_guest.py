"""FS-SCOPE-1 phase 1 — the guest's filesystem roots and `readonly` mode are enforced.

Two holes on the path that already enforces a filesystem bound:

* ``clamp_to_floor`` narrowed the MODE and passed the declared ROOTS through untouched, so a spec
  declaring ``scoped`` with roots ``["/"]`` reached nodus as ``allowed_paths=["/"]``: the whole
  filesystem, under a floor whose bound is a per-execution scratch directory.
* ``readonly`` was translated exactly like ``scoped``, so a guest that declared read-only could
  write anywhere it could read.

Proven through the real worker (``nodus_worker.run_one``) and real guest file builtins, each with
a control that shows the same script succeeds when the floor does allow it, so a "blocked" can't
pass on a worker that blocks everything.
"""
from __future__ import annotations

import os
from dataclasses import replace

import pytest

pytestmark = pytest.mark.runtime_only

pytest.importorskip("nodus.runtime.embedding")

from AINDY.core import execution_environment as ee  # noqa: E402
from AINDY.core.execution_environment import (  # noqa: E402
    ExecutionEnvironmentSpec,
    Visibility,
    clamp_to_floor,
)


def _spec(mode: str, roots: list[str]) -> ExecutionEnvironmentSpec:
    return ExecutionEnvironmentSpec(visibility=Visibility(filesystem=mode, filesystem_roots=tuple(roots)))


def _floor_with_roots(roots: list[str]) -> ExecutionEnvironmentSpec:
    floor = ee.GUEST_FLOOR
    return replace(floor, visibility=replace(floor.visibility, filesystem_roots=tuple(roots)))


@pytest.fixture
def area(tmp_path):
    inside = tmp_path / "inside"
    outside = tmp_path / "outside"
    inside.mkdir()
    outside.mkdir()
    (inside / "note.txt").write_text("inside", encoding="utf-8")
    (outside / "secret.txt").write_text("s3cret", encoding="utf-8")
    return inside, outside


def _run(script: str, env_spec: dict | None):
    from AINDY.runtime import nodus_worker

    payload = {"script": script, "state": {}, "context": {"user_id": "fs-scope"}}
    if env_spec is not None:
        payload["env_spec"] = env_spec
    return nodus_worker.run_one(payload)


def _p(path) -> str:
    return str(path).replace("\\", "/")


def _read(path) -> str:
    return f'set_state("r", read_file("{_p(path)}"))\n'


def _write(path) -> str:
    return f'write_file("{_p(path)}", "x")\nset_state("r", "wrote")\n'


# ── the clamp ────────────────────────────────────────────────────────────────


def test_the_guest_floor_drops_every_declared_root_and_says_so():
    effective, widened = clamp_to_floor(_spec("scoped", ["/"]), ee.guest_floor())
    assert effective.visibility.filesystem_roots == ()
    assert "visibility.filesystem_roots" in widened


def test_a_host_declaration_clamped_to_scoped_loses_its_roots():
    """The mode clamp turns `host` into `scoped`; the roots must then be read as a bound."""
    effective, widened = clamp_to_floor(_spec("host", ["/"]), ee.guest_floor())
    assert effective.visibility.filesystem == "scoped"
    assert effective.visibility.filesystem_roots == ()
    assert "visibility.filesystem_roots" in widened


def test_a_floor_with_roots_keeps_only_the_roots_inside_it(area):
    inside, outside = area
    sub = inside / "sub"
    sub.mkdir()
    escape = os.path.join(str(inside), "..", "outside")
    effective, widened = clamp_to_floor(
        _spec("scoped", [str(sub), str(outside), escape]), _floor_with_roots([str(inside)])
    )
    assert effective.visibility.filesystem_roots == (str(sub),)
    assert "visibility.filesystem_roots" in widened


def test_roots_inside_the_floor_are_not_a_widening(area):
    inside, _ = area
    effective, widened = clamp_to_floor(_spec("readonly", [str(inside)]), _floor_with_roots([str(inside)]))
    assert effective.visibility.filesystem_roots == (str(inside),)
    assert "visibility.filesystem_roots" not in widened


def test_the_tool_floor_bounds_nothing_so_roots_stand(area):
    inside, _ = area
    effective, widened = clamp_to_floor(_spec("scoped", [str(inside)]), ee.tool_floor())
    assert effective.visibility.filesystem_roots == (str(inside),)
    assert widened == ()


# ── through the real worker ──────────────────────────────────────────────────


def test_control_a_root_the_floor_allows_is_readable(area, monkeypatch):
    inside, _ = area
    monkeypatch.setattr(ee, "guest_floor", lambda: _floor_with_roots([str(inside)]))
    result = _run(_read(inside / "note.txt"), {"visibility": {"filesystem": "scoped", "filesystem_roots": [str(inside)]}})
    assert result.get("status") == "success", result
    assert result["output_state"]["r"] == "inside"


def test_a_declared_root_outside_the_floor_is_not_readable(area, monkeypatch):
    inside, outside = area
    monkeypatch.setattr(ee, "guest_floor", lambda: _floor_with_roots([str(inside)]))
    result = _run(_read(outside / "secret.txt"),
                  {"visibility": {"filesystem": "scoped", "filesystem_roots": [str(outside)]}})
    assert result.get("status") != "success", result
    assert "s3cret" not in str(result)


def test_under_the_real_guest_floor_a_declared_root_grants_nothing(area):
    _, outside = area
    result = _run(_read(outside / "secret.txt"), {"visibility": {"filesystem": "scoped", "filesystem_roots": ["/", str(outside)]}})
    assert result.get("status") != "success", result
    assert "s3cret" not in str(result)


def test_control_scoped_may_write_inside_its_root(area, monkeypatch):
    inside, _ = area
    monkeypatch.setattr(ee, "guest_floor", lambda: _floor_with_roots([str(inside)]))
    result = _run(_write(inside / "w.txt"), {"visibility": {"filesystem": "scoped", "filesystem_roots": [str(inside)]}})
    assert result.get("status") == "success", result
    assert (inside / "w.txt").exists()


def test_readonly_reads_but_never_writes(area, monkeypatch):
    inside, _ = area
    monkeypatch.setattr(ee, "guest_floor", lambda: _floor_with_roots([str(inside)]))
    spec = {"visibility": {"filesystem": "readonly", "filesystem_roots": [str(inside)]}}
    read = _run(_read(inside / "note.txt"), spec)
    assert read.get("status") == "success", read
    wrote = _run(_write(inside / "w.txt"), spec)
    assert wrote.get("status") != "success", wrote
    assert not (inside / "w.txt").exists(), "a read-only guest wrote a file"
