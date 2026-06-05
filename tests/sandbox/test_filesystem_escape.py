"""
Sandbox escape test: filesystem vectors.

WHAT IS BEING TESTED
--------------------
Three filesystem isolation controls provided by ``ContainerizedOciSandboxRunner``:

1. ``--read-only`` (read_only_rootfs)
   The container's entire root filesystem is mounted read-only by the container
   runtime. A plugin that tries to write to any path outside an explicit tmpfs
   (e.g. ``/etc``, ``/usr``, ``/var``) must fail with EROFS / permission denied.

   WHY THIS MATTERS: Without a read-only rootfs, a malicious plugin could modify
   system files, install backdoors, or persist data across container restarts.
   ``ContainerizedOciSandboxRunner`` uses ``--read-only`` with a tmpfs at ``/tmp``
   so plugins get writable scratch space while the rest of the filesystem is frozen.

2. ``--mount type=bind,...,readonly`` (plugin mount read-only)
   The plugin-root directory is bind-mounted into the container as read-only. A
   plugin must not be able to write back into its own source tree.

   WHY THIS MATTERS: If the plugin mount were writable, a plugin could replace its
   own handler file (``handler.py``) between invocations, achieving code injection
   into the next plugin execution cycle without host-side detection.

3. ``--read-only`` + ``--mount type=tmpfs,dst=/tmp`` (tmpfs isolation)
   The tmpfs at ``/tmp`` is writable but isolated: its contents are per-container
   and are discarded when the container exits. We verify that ``/tmp`` is actually
   writable (so plugins can use it for scratch space) while ``/etc`` remains
   read-only (rootfs control is not accidentally relaxed by the tmpfs mount).

   WHY THIS MATTERS: A common misconfiguration is to add a writable tmpfs but
   inadvertently grant too broad access. This test confirms the tmpfs scope is
   constrained to the target path.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from tests.sandbox.conftest import record_result, run_escape_attempt

pytestmark = [pytest.mark.sandbox_escape, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Test 1 — read-only rootfs blocks writes to system paths
# ---------------------------------------------------------------------------


def test_readonly_rootfs_blocks_write(docker_info, escape_image, request):
    """
    Attack: plugin writes to /etc inside a container with --read-only rootfs.
    Control: docker run --read-only
    Expected result: write fails (Read-only file system / Permission denied).

    This is the primary guard against persistent filesystem tampering. On Linux
    the kernel enforces EROFS; on Docker Desktop the Linux VM kernel enforces it.
    The container exits non-zero; the test PASSES.
    """
    result = run_escape_attempt(
        docker_args=["--read-only"],
        image=escape_image,
        cmd=[
            "python", "-c",
            (
                "import sys\n"
                "try:\n"
                "    open('/etc/aindy_escape_probe.txt', 'w').write('escaped')\n"
                "    sys.exit(0)  # FAIL: write succeeded\n"
                "except (OSError, PermissionError):\n"
                "    sys.exit(1)  # PASS: write was blocked\n"
            ),
        ],
        attack_vector="filesystem_escape",
        hardening_control="read_only_rootfs",
        docker_flag="--read-only",
        description=(
            "Plugin attempts to write to /etc with --read-only rootfs active. "
            "Blocked by EROFS (Read-only file system)."
        ),
        expect_failure=True,
    )
    record_result(request.node.nodeid, result)
    assert result["status"] == "PASS", (
        f"FILESYSTEM ESCAPE — read-only rootfs NOT enforced.\n"
        f"A plugin running with --read-only could write to /etc.\n"
        f"Evidence: {result['evidence']}"
    )


# ---------------------------------------------------------------------------
# Test 2 — plugin bind mount is read-only
# ---------------------------------------------------------------------------


def test_plugin_mount_read_only(docker_info, escape_image, request):
    """
    Attack: plugin writes back into its own source directory via the bind mount.
    Control: docker run --mount type=bind,...,readonly
    Expected result: write fails; the bind mount is read-only.

    The runner mounts the plugin root as: ``type=bind,src=<host_dir>,dst=/plugin-root,readonly``.
    This test creates a real temp directory on the host (simulating a plugin root),
    bind-mounts it read-only, and verifies the container cannot write to it.

    If this control fails, a plugin could replace its own handler.py with a modified
    version that executes arbitrary code the next time the extension is loaded.
    """
    with tempfile.TemporaryDirectory() as plugin_root:
        canary = Path(plugin_root) / "handler.py"
        canary.write_text("# original handler\n")

        result = run_escape_attempt(
            docker_args=[
                "--mount",
                f"type=bind,src={plugin_root},dst=/plugin-root,readonly",
            ],
            image=escape_image,
            cmd=[
                "python", "-c",
                (
                    "import sys\n"
                    "try:\n"
                    "    open('/plugin-root/handler.py', 'w').write('# injected')\n"
                    "    sys.exit(0)  # FAIL: write succeeded\n"
                    "except (OSError, PermissionError):\n"
                    "    sys.exit(1)  # PASS: write was blocked\n"
                ),
            ],
            attack_vector="filesystem_escape",
            hardening_control="read_only_bind_mount",
            docker_flag="--mount type=bind,...,readonly",
            description=(
                "Plugin attempts to overwrite its own handler.py via the read-only "
                "plugin-root bind mount. Blocked by read-only mount flag."
            ),
            expect_failure=True,
        )
    record_result(request.node.nodeid, result)
    assert result["status"] == "PASS", (
        f"FILESYSTEM ESCAPE — plugin bind mount is WRITABLE.\n"
        f"A plugin could replace its own handler.py for persistent code injection.\n"
        f"Evidence: {result['evidence']}"
    )


# ---------------------------------------------------------------------------
# Test 3 — tmpfs at /tmp is writable; /etc remains read-only
# ---------------------------------------------------------------------------


def test_tmpfs_writable_rootfs_read_only(docker_info, escape_image, request):
    """
    Attack: plugin checks whether --read-only + tmpfs:/tmp inadvertently allows
    writes outside /tmp (e.g. to /etc).
    Control: docker run --read-only --mount type=tmpfs,dst=/tmp,...

    Two assertions in one container run:
    (a) /tmp is writable (tmpfs allows plugin scratch space)
    (b) /etc is NOT writable (read-only rootfs is not relaxed by the tmpfs mount)

    The container exits 0 if BOTH assertions hold (good state). It exits non-zero
    if either assertion fails (either /tmp is not writable, or /etc is writable).

    WHY TWO IN ONE: Running a single container invocation is more representative of
    real plugin execution and avoids two separate docker pull/start/stop cycles.
    We use expect_failure=False because PASS requires the container to exit zero
    (the "good" state is: /tmp writable, /etc not writable).
    """
    result = run_escape_attempt(
        docker_args=[
            "--read-only",
            "--mount", "type=tmpfs,dst=/tmp,tmpfs-size=16m",
        ],
        image=escape_image,
        cmd=[
            "python", "-c",
            (
                "import sys, os\n"
                "# (a) /tmp must be writable\n"
                "try:\n"
                "    open('/tmp/sandbox_probe.txt', 'w').write('ok')\n"
                "except Exception as e:\n"
                "    print(f'FAIL: /tmp not writable: {e}', flush=True)\n"
                "    sys.exit(1)\n"
                "# (b) /etc must NOT be writable\n"
                "try:\n"
                "    open('/etc/aindy_escape_probe.txt', 'w').write('escaped')\n"
                "    print('FAIL: /etc is writable despite --read-only', flush=True)\n"
                "    sys.exit(2)\n"
                "except (OSError, PermissionError):\n"
                "    pass  # expected\n"
                "print('PASS: /tmp writable, /etc read-only', flush=True)\n"
                "sys.exit(0)\n"
            ),
        ],
        attack_vector="filesystem_escape",
        hardening_control="read_only_rootfs_with_tmpfs",
        docker_flag="--read-only --mount type=tmpfs,dst=/tmp",
        description=(
            "Verifies that --read-only + tmpfs:/tmp gives writable /tmp for "
            "plugin scratch space while /etc remains read-only. Both conditions "
            "must hold for PASS."
        ),
        expect_failure=False,
    )
    record_result(request.node.nodeid, result)
    assert result["status"] == "PASS", (
        f"FILESYSTEM CONTROL — tmpfs/rootfs combination is misconfigured.\n"
        f"Either /tmp is not writable (plugin cannot write scratch files) or "
        f"/etc is writable (rootfs protection relaxed).\n"
        f"Evidence: {result['evidence']}"
    )
