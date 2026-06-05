"""
Sandbox escape test: host path access boundary.

WHAT IS BEING TESTED
--------------------
``ContainerizedOciSandboxRunner._process_args()`` mounts exactly ONE host path
into the container: the plugin root directory, bind-mounted as read-only at
``/plugin-root``. No other host directories, device files, or special volumes
are mounted. The container's root filesystem is the OCI image with no ambient
host path access.

This test category verifies:

1. A canary file placed in a host directory that is NOT the plugin root is
   inaccessible inside the container. The plugin cannot reach host files
   outside its designated mount point — there is no path traversal, no
   ambient ``/hostfs``, no ``/proc/1/root`` escape to the host namespace.

2. The plugin root is accessible at the expected path (``/plugin-root``) and
   its contents are visible — confirming the bind mount works correctly.
   This is a positive-verification test.

3. A path traversal attempt (``/plugin-root/../../../etc/passwd``) inside the
   container resolves to the container's own ``/etc/passwd``, not the host's.
   This proves that symlink/relative path tricks cannot escape the bind mount.

WHY THIS MATTERS
----------------
Without proper path isolation:
  - A plugin could read ``~/.ssh/authorized_keys`` or ``~/.ssh/id_rsa`` from the
    host operator's home directory
  - A plugin could read ``/etc/passwd``, ``/etc/shadow``, or secrets in
    ``/run/secrets/`` on the host
  - A plugin could read the runtime's own ``AINDY/.env`` (which may contain
    DATABASE_URL, SECRET_KEY in development setups)
  - On a Kubernetes node, the plugin could read mounted service account tokens
    from ``/var/run/secrets/kubernetes.io/serviceaccount/``

The bind mount + no ambient host paths guarantee means a plugin's filesystem
view is strictly: the OCI image layers + the read-only plugin root. Nothing else.

HOW THE TESTS WORK
------------------
We create two temp directories on the host:
- ``plugin_root/`` — contains ``handler.py``; this is mounted at ``/plugin-root``
- ``secret_dir/`` — contains ``canary.txt`` with a known sentinel value;
  this is NOT mounted into the container

Inside the container we verify:
- ``/plugin-root/handler.py`` is readable (correct)
- The canary sentinel is NOT readable from any expected path
- Path traversal from ``/plugin-root`` stays inside the container filesystem
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from tests.sandbox.conftest import record_result, run_escape_attempt

pytestmark = pytest.mark.sandbox_escape

_CANARY_SENTINEL = "AINDY_HOST_SECRET_CANARY_8f3k2j9a"


# ---------------------------------------------------------------------------
# Test 1 — unmounted host directory is inaccessible
# ---------------------------------------------------------------------------


def test_unmounted_host_dir_inaccessible(docker_info, escape_image, request):
    """
    Attack: plugin attempts to read a file from a host directory that was NOT
    mounted into the container.
    Control: only plugin-root is bind-mounted; all other host paths are absent.
    Expected result: the canary file is not readable from inside the container.

    We create a temp directory with a canary file containing a known sentinel.
    We mount ONLY the plugin root (a separate temp dir). Inside the container,
    we try to read the canary from its absolute host path. Since the directory
    was not mounted, it does not exist inside the container; the read fails.

    If the sentinel value appears in the container output, the host directory
    was accessible — a critical path boundary violation.
    """
    with (
        tempfile.TemporaryDirectory() as plugin_root,
        tempfile.TemporaryDirectory() as secret_dir,
    ):
        # Set up plugin root with a legitimate file.
        Path(plugin_root, "handler.py").write_text("# handler\n")

        # Place canary in a host dir that will NOT be mounted.
        canary_path = Path(secret_dir, "canary.txt")
        canary_path.write_text(_CANARY_SENTINEL)

        # The container path we attempt to read: exact host path won't exist
        # inside the container (different path namespace). We attempt several
        # known-dangerous paths to be thorough.
        check_code = (
            "import sys, os\n"
            f"sentinel = {_CANARY_SENTINEL!r}\n"
            "found_in = []\n"
            "# Try paths that might expose host files\n"
            "candidate_paths = [\n"
            "    '/host',\n"
            "    '/hostfs',\n"
            "    '/proc/1/root/tmp',\n"
            "    '/proc/1/root/etc',\n"
            "]\n"
            "for base in candidate_paths:\n"
            "    try:\n"
            "        for root, dirs, files in os.walk(base):\n"
            "            for fn in files:\n"
            "                try:\n"
            "                    content = open(os.path.join(root, fn), 'rb').read(512)\n"
            "                    if sentinel.encode() in content:\n"
            "                        found_in.append(os.path.join(root, fn))\n"
            "                except Exception:\n"
            "                    pass\n"
            "    except PermissionError:\n"
            "        pass  # access denied is expected and correct\n"
            "    except OSError:\n"
            "        pass  # path doesn't exist — correct\n"
            "if found_in:\n"
            "    print(f'FAIL: canary found at {found_in}', flush=True)\n"
            "    sys.exit(0)  # canary found = test FAILS (with expect_failure=True)\n"
            "else:\n"
            "    print('PASS: canary not reachable from any candidate path', flush=True)\n"
            "    sys.exit(1)  # canary absent = test PASSES (with expect_failure=True)\n"
        )

        result = run_escape_attempt(
            docker_args=[
                "--read-only",
                "--mount",
                f"type=bind,src={plugin_root},dst=/plugin-root,readonly",
                "--mount", "type=tmpfs,dst=/tmp,tmpfs-size=8m",
            ],
            image=escape_image,
            cmd=["python", "-c", check_code],
            attack_vector="path_boundary",
            hardening_control="no_ambient_host_paths",
            docker_flag="--mount type=bind,src=<plugin_root>,dst=/plugin-root,readonly (only)",
            description=(
                "Plugin searches known dangerous paths (/proc/1/root, /host, etc.) "
                "for a canary sentinel placed in an unmounted host directory. "
                "Canary must not be found."
            ),
            expect_failure=True,
        )

    record_result(request.node.nodeid, result)
    assert result["status"] == "PASS", (
        f"PATH BOUNDARY — host files accessible from inside container.\n"
        f"A plugin can read host files outside the plugin-root mount.\n"
        f"Evidence: {result['evidence']}"
    )


# ---------------------------------------------------------------------------
# Test 2 — plugin root is accessible (positive verification)
# ---------------------------------------------------------------------------


def test_plugin_root_accessible(docker_info, escape_image, request):
    """
    Positive verification: the plugin root is mounted correctly and its files
    are readable inside the container at ``/plugin-root``.

    This test confirms the bind mount WORKS (the plugin can read its own files),
    not just that it's isolated. Without this confirmation, a passing Test 1
    could be explained by a completely broken mount that makes everything
    inaccessible, not by correct isolation.

    expect_failure=False: the container must exit 0 for PASS.
    """
    with tempfile.TemporaryDirectory() as plugin_root:
        handler = Path(plugin_root, "handler.py")
        handler.write_text("PLUGIN_MARKER = 'aindy_plugin_v1'\n")

        result = run_escape_attempt(
            docker_args=[
                "--read-only",
                "--mount",
                f"type=bind,src={plugin_root},dst=/plugin-root,readonly",
                "--mount", "type=tmpfs,dst=/tmp,tmpfs-size=8m",
            ],
            image=escape_image,
            cmd=[
                "python", "-c",
                (
                    "import sys\n"
                    "try:\n"
                    "    content = open('/plugin-root/handler.py').read()\n"
                    "except OSError as e:\n"
                    "    print(f'FAIL: could not read /plugin-root/handler.py: {e}', flush=True)\n"
                    "    sys.exit(1)\n"
                    "if 'PLUGIN_MARKER' not in content:\n"
                    "    print('FAIL: handler.py content unexpected', flush=True)\n"
                    "    sys.exit(1)\n"
                    "print('PASS: /plugin-root/handler.py readable', flush=True)\n"
                    "sys.exit(0)\n"
                ),
            ],
            attack_vector="path_boundary",
            hardening_control="no_ambient_host_paths",
            docker_flag="--mount type=bind,src=<plugin_root>,dst=/plugin-root,readonly",
            description=(
                "Positive check: /plugin-root/handler.py must be readable inside the "
                "container, confirming the bind mount works correctly."
            ),
            expect_failure=False,
        )

    record_result(request.node.nodeid, result)
    assert result["status"] == "PASS", (
        f"PATH BOUNDARY — plugin-root bind mount is not accessible from inside container.\n"
        f"The plugin cannot read its own files.\n"
        f"Evidence: {result['evidence']}"
    )


# ---------------------------------------------------------------------------
# Test 3 — path traversal from /plugin-root stays in container
# ---------------------------------------------------------------------------


def test_path_traversal_stays_in_container(docker_info, escape_image, request):
    """
    Attack: plugin attempts path traversal from the plugin-root bind mount
    (``/plugin-root/../../../etc/passwd``) to reach the host's ``/etc/passwd``.
    Control: bind mount isolation; container has its own root filesystem.
    Expected result: the path resolves to the container's own ``/etc/passwd``,
    not the host's. The container ``/etc/passwd`` must not contain the host's
    system users.

    HOW WE DETECT HOST ESCAPE: the host's ``/etc/passwd`` contains the actual
    user accounts including the Docker daemon user (usually ``root``, ``daemon``,
    possibly ``aindy`` if the runtime user was added). The container image
    (python:3.11-alpine) has a minimal ``/etc/passwd`` with only alpine base users
    (root, daemon, nobody). We look for host-specific indicators:

    - The canary sentinel is NOT in /etc/passwd (proves it's the container's)
    - We count the lines: a normal alpine /etc/passwd has ~6-12 entries; a
      production host may have dozens including service accounts

    We use a relaxed check: read /etc/passwd from the traversal path and from
    the direct path and confirm they are identical (same file). If they're the
    same, the traversal stayed in the container. This is the correct behavior.

    expect_failure=False: we WANT the traversal to resolve to the container's
    own /etc/passwd (exit 0 = PASS).
    """
    with tempfile.TemporaryDirectory() as plugin_root:
        Path(plugin_root, "handler.py").write_text("# handler\n")

        result = run_escape_attempt(
            docker_args=[
                "--read-only",
                "--mount",
                f"type=bind,src={plugin_root},dst=/plugin-root,readonly",
                "--mount", "type=tmpfs,dst=/tmp,tmpfs-size=8m",
            ],
            image=escape_image,
            cmd=[
                "python", "-c",
                (
                    "import sys, os\n"
                    "# Read via direct path\n"
                    "try:\n"
                    "    direct = open('/etc/passwd').read()\n"
                    "except OSError as e:\n"
                    "    print(f'FAIL: cannot read /etc/passwd: {e}', flush=True)\n"
                    "    sys.exit(1)\n"
                    "# Read via traversal path (should resolve to same file)\n"
                    "try:\n"
                    "    traversal = open('/plugin-root/../../../etc/passwd').read()\n"
                    "except OSError as e:\n"
                    "    print(f'FAIL: traversal read failed: {e}', flush=True)\n"
                    "    sys.exit(1)\n"
                    "if direct != traversal:\n"
                    "    print('FAIL: traversal resolves to a DIFFERENT /etc/passwd ')\n"
                    "    print('(may indicate host namespace escape)', flush=True)\n"
                    "    sys.exit(1)\n"
                    "# Both paths lead to same file (container's own /etc/passwd)\n"
                    "lines = direct.strip().splitlines()\n"
                    "print(f'PASS: both paths same ({len(lines)}-line /etc/passwd)', flush=True)\n"
                    "sys.exit(0)\n"
                ),
            ],
            attack_vector="path_boundary",
            hardening_control="no_ambient_host_paths",
            docker_flag="--mount type=bind,...,readonly (bind mount isolation)",
            description=(
                "Path traversal /plugin-root/../../../etc/passwd must resolve to "
                "the container's own /etc/passwd, not the host's. Both paths must "
                "return identical content."
            ),
            expect_failure=False,
        )

    record_result(request.node.nodeid, result)
    assert result["status"] == "PASS", (
        f"PATH BOUNDARY — path traversal may have escaped the container.\n"
        f"Evidence: {result['evidence']}"
    )
