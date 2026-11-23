"""NODUS-STDLIB-ROOT-1 — a guest's module imports never write into the installed package.

nodus writes its bytecode cache and `deps.json` under `project_root/.nodus/` on every module
import, with no error handling. The worker used to pass `AINDY/nodus/stdlib` as that root, which
in an installed runtime is site-packages: read-only for the Docker image's `aindy` user, so every
guest `import` failed with `[Errno 13] Permission denied: …/.nodus/cache` (measured in the
2.40.0 image). As root it wrote into site-packages; in a checkout, into the repo.

The read-only case itself is driven by Install Smoke (a real wheel, the stdlib dir `chmod a-w`)
and was reproduced in the Docker image; Windows cannot make a directory refuse file creation, so
here the assertions are where the writes land and what `import "memory"` resolves to.
"""
from __future__ import annotations

import os
import uuid

import pytest


def _run_one(script: str) -> dict:
    pytest.importorskip("nodus.runtime.embedding")
    from AINDY.runtime import nodus_worker

    return nodus_worker.run_one({
        "script": script,
        "filename": "root.nd",
        "state": {},
        "memory_context": {},
        "input_payload": {},
        "context": {"user_id": str(uuid.uuid4()), "execution_unit_id": str(uuid.uuid4()), "trace_id": "t-root"},
    })


@pytest.fixture
def stdlib_copy(tmp_path, monkeypatch):
    """Point the worker at a private copy of the runtime stdlib and a fresh guest root."""
    from AINDY.runtime import nodus_worker

    src = nodus_worker._STDLIB_DIR
    dst = tmp_path / "stdlib"
    dst.mkdir()
    names = [n for n in os.listdir(src) if n.endswith(".nd")]
    assert names, "the runtime ships no .nd modules; this suite would test nothing"
    for name in names:
        (dst / name).write_bytes((open(os.path.join(src, name), "rb")).read())
    monkeypatch.setattr(nodus_worker, "_STDLIB_DIR", str(dst))
    monkeypatch.setattr(nodus_worker, "_guest_root", None)
    return dst


def test_a_stdlib_import_writes_its_cache_into_the_guest_root_not_the_stdlib(stdlib_copy):
    from AINDY.runtime import nodus_worker

    result = _run_one('import "std:strings" as s\nset_state("u", s.upper("ab"))')

    assert result["status"] == "success", result
    assert result["output_state"]["u"] == "AB"
    # Liveness: the cache write this is about really happened — in the guest root.
    guest_root = nodus_worker._guest_project_root()
    assert os.path.isdir(os.path.join(guest_root, ".nodus", "cache")), os.listdir(guest_root)
    # …and never in the stdlib directory, which is site-packages in an install.
    assert not (stdlib_copy / ".nodus").exists()


def test_import_memory_resolves_to_the_runtimes_module_not_nodus_std_memory(stdlib_copy):
    pytest.importorskip("nodus.runtime.module_loader")
    from nodus.runtime.module_loader import resolve_import_path

    from AINDY.runtime import nodus_worker

    guest_root = nodus_worker._guest_project_root()
    resolved = resolve_import_path("memory", guest_root, {"project_root": guest_root}, None, "<t>")

    assert os.path.normcase(resolved) == os.path.normcase(os.path.join(guest_root, "memory.nd"))
    with open(resolved, encoding="utf-8") as handle:
        assert "__memory_stdlib_share" in handle.read()


def test_without_the_runtime_module_memory_falls_through_to_nodus_std_memory(tmp_path):
    """Control for the test above: this fall-through is what every wheel install got."""
    pytest.importorskip("nodus.runtime.module_loader")
    from nodus.runtime.module_loader import resolve_import_path

    resolved = resolve_import_path("memory", str(tmp_path), {"project_root": str(tmp_path)}, None, "<t>")

    assert os.path.normcase(str(tmp_path)) not in os.path.normcase(resolved)
    with open(resolved, encoding="utf-8") as handle:
        assert "__memory_stdlib_share" not in handle.read()


def test_one_root_per_process_and_a_fork_gets_its_own(stdlib_copy, monkeypatch):
    from AINDY.runtime import nodus_worker

    first = nodus_worker._guest_project_root()
    assert nodus_worker._guest_project_root() == first
    assert os.path.normcase(first) != os.path.normcase(str(stdlib_copy))

    real_pid = os.getpid()
    monkeypatch.setattr(nodus_worker.os, "getpid", lambda: real_pid + 1)
    second = nodus_worker._guest_project_root()

    assert second != first
    # The parent's exit handler must not remove a directory it did not create, and vice versa.
    nodus_worker._remove_guest_root(real_pid, second)
    assert os.path.isdir(second)
    nodus_worker._remove_guest_root(real_pid + 1, second)
    assert not os.path.isdir(second)
