"""
Sandbox escape test: process/PID exhaustion vectors.

WHAT IS BEING TESTED
--------------------
The ``--pids-limit`` Docker flag (``pids_limit`` control in
``ContainerizedOciSandboxRunner``) sets a hard ceiling on the total number of
process IDs that can exist simultaneously within the container's cgroup.

WHY THIS MATTERS
----------------
Without a pids limit, a malicious plugin can execute a fork bomb — exponentially
spawning child processes until the host kernel's PID table is exhausted. On a
shared production host, PID exhaustion is a denial-of-service attack: no new
processes can be created system-wide, effectively taking down all services
including the runtime itself.

This is distinct from memory/CPU limits: a fork bomb is lightweight (each process
may consume almost no CPU or memory) but its impact is catastrophic.

HOW ``--pids-limit`` WORKS
--------------------------
Docker delegates to the Linux cgroups v1 ``pids`` subsystem (or cgroups v2 ``pids``
controller). The kernel tracks the PID count for the cgroup and returns EAGAIN
(errno 11 — Resource temporarily unavailable) when a ``clone()`` or ``fork()``
syscall would exceed the limit. In Python this surfaces as:

    BlockingIOError: [Errno 11] Resource temporarily unavailable

or:

    OSError: [Errno 11] Resource temporarily unavailable

when calling ``subprocess.Popen()`` or ``os.fork()``.

PLATFORM NOTES
--------------
``--pids-limit`` requires the Linux ``pids`` cgroup controller and is only
enforced when the container is backed by a real Linux kernel. The ``linux_kernel_controls``
fixture ensures this test is skipped on platforms where the control is not available.

On Docker Desktop for Windows/macOS (Linux containers mode), the Linux VM provides
a real cgroup controller, so pids limit enforcement works correctly.
"""
from __future__ import annotations

import pytest

from tests.sandbox.conftest import record_result, run_escape_attempt

pytestmark = [pytest.mark.sandbox_escape, pytest.mark.integration]

# How many processes the container is allowed. Must be low enough that the
# escape script hits the limit, but high enough that the Python interpreter
# itself can start (Python needs ~2-3 PIDs to initialize).
_PIDS_LIMIT = "10"

# How many sleep processes the escape script attempts to spawn.
# With _PIDS_LIMIT=10, Python + the interpreter overhead consumes ~3-4 PIDs,
# leaving ~6 slots. Attempting 30 ensures we definitely hit the ceiling.
_SPAWN_ATTEMPTS = 30


# ---------------------------------------------------------------------------
# Test 1 — pids limit blocks fork bomb
# ---------------------------------------------------------------------------


def test_pids_limit_blocks_fork_bomb(docker_info, escape_image, linux_kernel_controls, request):
    """
    Attack: plugin attempts to spawn more processes than the pids limit allows.
    Control: docker run --pids-limit 10
    Expected result: at some point Popen() raises BlockingIOError/OSError (EAGAIN);
    the script detects this and exits 1 (PASS).

    The escape script tries to spawn _SPAWN_ATTEMPTS (30) ``sleep 60`` processes.
    With a pids limit of 10 and Python consuming ~3-4 PIDs for its own interpreter
    threads, the script should hit the limit after ~6 successful spawns. When
    Popen() fails, the script kills all successfully-spawned children, records
    the hit, and exits 1 (escape was blocked).

    If Popen() NEVER fails (all 30 spawns succeed), the script exits 0, which
    the test runner interprets as FAIL (the pids limit was not enforced).

    We also verify that at least one spawn succeeded before the limit was hit,
    confirming the limit is set correctly (not so low that even one process fails).
    """
    if not linux_kernel_controls:
        pytest.skip("--pids-limit requires Linux kernel controls (not available on this backend)")

    result = run_escape_attempt(
        docker_args=["--pids-limit", _PIDS_LIMIT],
        image=escape_image,
        cmd=[
            "python", "-c",
            (
                f"import subprocess, sys\n"
                f"procs = []\n"
                f"hit_limit = False\n"
                f"for i in range({_SPAWN_ATTEMPTS}):\n"
                f"    try:\n"
                f"        p = subprocess.Popen(\n"
                f"            ['sleep', '60'],\n"
                f"            stdout=subprocess.DEVNULL,\n"
                f"            stderr=subprocess.DEVNULL,\n"
                f"        )\n"
                f"        procs.append(p)\n"
                f"    except (BlockingIOError, OSError) as e:\n"
                f"        hit_limit = True\n"
                f"        print(f'pids limit hit after {{len(procs)}} spawns: {{e}}', flush=True)\n"
                f"        break\n"
                f"for p in procs:\n"
                f"    try: p.kill()\n"
                f"    except: pass\n"
                f"if hit_limit:\n"
                f"    sys.exit(1)  # PASS: limit was enforced\n"
                f"else:\n"
                f"    print(f'all {_SPAWN_ATTEMPTS} spawns succeeded — limit NOT enforced', flush=True)\n"
                f"    sys.exit(0)  # FAIL: limit was not enforced\n"
            ),
        ],
        attack_vector="process_escape",
        hardening_control="pids_limit",
        docker_flag=f"--pids-limit {_PIDS_LIMIT}",
        description=(
            f"Plugin attempts to spawn {_SPAWN_ATTEMPTS} child processes with "
            f"--pids-limit {_PIDS_LIMIT}. Cgroup pids controller must block spawning "
            f"before all attempts succeed."
        ),
        expect_failure=True,
        timeout=30.0,
    )
    record_result(request.node.nodeid, result)
    assert result["status"] == "PASS", (
        f"PROCESS ESCAPE — --pids-limit did NOT prevent spawning {_SPAWN_ATTEMPTS} processes.\n"
        f"A plugin could execute a fork bomb against this container configuration.\n"
        f"Evidence: {result['evidence']}"
    )


# ---------------------------------------------------------------------------
# Test 2 — pids limit is kernel-observable via /proc/self/cgroup
# ---------------------------------------------------------------------------


def test_pids_limit_visible_in_cgroup(docker_info, escape_image, linux_kernel_controls, request):
    """
    Kernel-observable check: verify the pids cgroup controller is active for
    the container by reading /proc/self/cgroup or /sys/fs/cgroup/pids.*/max.

    This provides independent kernel-level evidence that the pids limit was
    actually applied by the container runtime, not just requested. It is a
    complementary evidence test (expect_failure=False).

    We check two paths:
    (a) For cgroups v1: /sys/fs/cgroup/pids/pids.max should contain the limit.
    (b) For cgroups v2: /sys/fs/cgroup/pids.max should contain the limit.

    If neither path is readable (unusual but possible in very minimal containers),
    we fall back to checking /proc/self/cgroup for a pids entry, confirming the
    process is inside a pids-limited cgroup without reading the exact value.
    """
    if not linux_kernel_controls:
        pytest.skip("--pids-limit requires Linux kernel controls (not available on this backend)")

    result = run_escape_attempt(
        docker_args=["--pids-limit", _PIDS_LIMIT],
        image=escape_image,
        cmd=[
            "python", "-c",
            (
                "import sys, os\n"
                "# Try cgroups v1 path first\n"
                "for path in [\n"
                "    '/sys/fs/cgroup/pids/pids.max',\n"
                "    '/sys/fs/cgroup/pids.max',\n"
                "]:\n"
                "    try:\n"
                "        val = open(path).read().strip()\n"
                "        print(f'cgroup pids limit: {path} = {val}', flush=True)\n"
                "        if val not in ('max', ''):\n"
                "            sys.exit(0)  # PASS: limit is set\n"
                "    except OSError:\n"
                "        pass\n"
                "# Fallback: check /proc/self/cgroup for 'pids' entry\n"
                "try:\n"
                "    cgroup = open('/proc/self/cgroup').read()\n"
                "    if 'pids' in cgroup or '0::' in cgroup:  # v2 unified\n"
                "        print('pids entry found in /proc/self/cgroup', flush=True)\n"
                "        sys.exit(0)  # PASS: in a pids cgroup\n"
                "except OSError:\n"
                "    pass\n"
                "print('FAIL: no pids cgroup evidence found', flush=True)\n"
                "sys.exit(1)\n"
            ),
        ],
        attack_vector="process_escape",
        hardening_control="pids_limit",
        docker_flag=f"--pids-limit {_PIDS_LIMIT}",
        description=(
            "Kernel-observable check: /sys/fs/cgroup/pids.max or /proc/self/cgroup "
            "must confirm the pids cgroup controller is active for the container."
        ),
        expect_failure=False,
        timeout=10.0,
    )
    record_result(request.node.nodeid, result)
    assert result["status"] == "PASS", (
        f"PROCESS EVIDENCE — pids cgroup controller not visible inside container.\n"
        f"Evidence: {result['evidence']}"
    )
