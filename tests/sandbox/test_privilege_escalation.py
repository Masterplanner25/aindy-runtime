"""
Sandbox escape test: privilege escalation vectors.

WHAT IS BEING TESTED
--------------------
Two Linux privilege-escalation controls used by ``ContainerizedOciSandboxRunner``
on Linux hosts:

1. ``--cap-drop ALL`` (drop_all_capabilities)
   Linux capabilities divide root privileges into fine-grained units. By default,
   a Docker container running as root retains a large set of capabilities including
   CAP_NET_ADMIN, CAP_NET_RAW, CAP_SYS_PTRACE, and others. ``--cap-drop ALL``
   removes every capability, reducing the container to an unprivileged execution
   context even if the process runs as UID 0.

   We test two specific attack vectors:

   (a) CAP_NET_RAW is required to create raw sockets (AF_INET, SOCK_RAW). Raw
       sockets allow crafting arbitrary IP packets, enabling ICMP flooding, port
       scanning, and network sniffing from inside the container. Without CAP_NET_RAW,
       raw socket creation fails with EPERM (Operation not permitted).

   (b) CAP_CHOWN is required to change file ownership via chown(). Without it, a
       plugin cannot take ownership of files it doesn't own, which limits the
       impact of filesystem exploits even if the rootfs were writable.

2. ``--security-opt no-new-privileges`` (no_new_privileges)
   This Linux kernel flag (``PR_SET_NO_NEW_PRIVS``) is a one-way irreversible
   setting that prevents a process and all its descendants from gaining new
   privileges through execve(). Specifically:
   - setuid/setgid binaries execute with the file's permissions, not elevated ones
   - Filesystem capabilities (``getcap``) do not apply
   - seccomp filters added later cannot grant more permissions

   We verify the flag is reflected in the kernel's own view by reading
   ``/proc/self/status`` and checking ``NoNewPrivs: 1``. This is kernel-observable
   evidence, not a self-report.

WHY THESE MATTER
----------------
The combination of ``--cap-drop ALL`` + ``no-new-privileges`` means:
- Even if a plugin achieves RCE (remote code execution) inside the container,
  it cannot escalate to full root
- It cannot create raw sockets for network attacks
- It cannot take ownership of system files
- It cannot spawn a setuid binary to regain dropped capabilities

Together these two controls substantially reduce the blast radius of a container
escape from "root on host" to "unprivileged user in a constrained environment."

PLATFORM NOTES
--------------
These tests require Linux kernel controls (``linux_kernel_controls`` fixture).
On Docker Desktop for Windows/macOS (Linux containers mode), the Linux VM's kernel
enforces the capability dropping and no-new-privileges flag, so these tests run
correctly on all platforms with Linux containers mode active.
"""
from __future__ import annotations

import pytest

from tests.sandbox.conftest import record_result, run_escape_attempt

pytestmark = [pytest.mark.sandbox_escape, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Test 1a — cap-drop ALL blocks raw socket creation (CAP_NET_RAW)
# ---------------------------------------------------------------------------


def test_cap_drop_all_blocks_raw_socket(docker_info, escape_image, linux_kernel_controls, request):
    """
    Attack: plugin creates a raw ICMP socket to perform network attacks.
    Control: docker run --cap-drop ALL
    Expected result: socket() syscall returns EPERM; Python exits non-zero.

    A raw socket with IPPROTO_ICMP requires CAP_NET_RAW. With ``--cap-drop ALL``,
    that capability is removed even when the container process runs as UID 0.
    The kernel returns EPERM (Operation not permitted) and the test PASSES.

    WHY ICMP RAW: ICMP raw sockets are the basis for ping floods, network
    reconnaissance, and ICMP tunneling (exfiltration). They require no other
    syscalls beyond socket() itself, making this the most direct capability test.
    """
    if not linux_kernel_controls:
        pytest.skip("--cap-drop ALL requires Linux kernel controls")

    result = run_escape_attempt(
        docker_args=["--cap-drop", "ALL"],
        image=escape_image,
        cmd=[
            "python", "-c",
            (
                "import sys, socket\n"
                "try:\n"
                "    s = socket.socket(\n"
                "        socket.AF_INET,\n"
                "        socket.SOCK_RAW,\n"
                "        socket.IPPROTO_ICMP,\n"
                "    )\n"
                "    s.close()\n"
                "    sys.exit(0)  # FAIL: raw socket was created\n"
                "except PermissionError:\n"
                "    sys.exit(1)  # PASS: EPERM — CAP_NET_RAW is gone\n"
            ),
        ],
        attack_vector="privilege_escalation",
        hardening_control="drop_all_capabilities",
        docker_flag="--cap-drop ALL",
        description=(
            "Plugin attempts to create a raw ICMP socket (requires CAP_NET_RAW). "
            "With --cap-drop ALL, kernel returns EPERM. Raw socket must not be created."
        ),
        expect_failure=True,
    )
    record_result(request.node.nodeid, result)
    assert result["status"] == "PASS", (
        f"PRIVILEGE ESCALATION — --cap-drop ALL did NOT remove CAP_NET_RAW.\n"
        f"A plugin could create raw sockets for network attacks.\n"
        f"Evidence: {result['evidence']}"
    )


# ---------------------------------------------------------------------------
# Test 1b — cap-drop ALL blocks arbitrary chown (CAP_CHOWN)
# ---------------------------------------------------------------------------


def test_cap_drop_all_blocks_chown(docker_info, escape_image, linux_kernel_controls, request):
    """
    Attack: plugin changes ownership of a file to UID 0 (root) to claim privileged
    access to protected resources.
    Control: docker run --cap-drop ALL --read-only --mount type=tmpfs,dst=/tmp
    Expected result: os.chown() raises PermissionError; plugin exits non-zero.

    CAP_CHOWN is required to change file ownership to an arbitrary UID. Without it,
    a process can only change file ownership to its own effective UID. With
    ``--cap-drop ALL``, even a root-UID process cannot perform arbitrary chowns.

    We create a file in /tmp (writable via tmpfs), then try to chown it to UID 1
    (another user). On a container without CAP_CHOWN this should raise EPERM.
    """
    if not linux_kernel_controls:
        pytest.skip("--cap-drop ALL requires Linux kernel controls")

    result = run_escape_attempt(
        docker_args=[
            "--cap-drop", "ALL",
            "--read-only",
            "--mount", "type=tmpfs,dst=/tmp,tmpfs-size=8m",
        ],
        image=escape_image,
        cmd=[
            "python", "-c",
            (
                "import sys, os\n"
                "path = '/tmp/chown_probe.txt'\n"
                "open(path, 'w').write('probe')\n"
                "try:\n"
                "    os.chown(path, 1, 1)  # try chown to uid=1,gid=1\n"
                "    sys.exit(0)  # FAIL: chown succeeded without CAP_CHOWN\n"
                "except PermissionError:\n"
                "    sys.exit(1)  # PASS: EPERM — CAP_CHOWN is gone\n"
            ),
        ],
        attack_vector="privilege_escalation",
        hardening_control="drop_all_capabilities",
        docker_flag="--cap-drop ALL",
        description=(
            "Plugin attempts to chown a file to uid=1 (requires CAP_CHOWN). "
            "With --cap-drop ALL, kernel returns EPERM. Chown must fail."
        ),
        expect_failure=True,
    )
    record_result(request.node.nodeid, result)
    assert result["status"] == "PASS", (
        f"PRIVILEGE ESCALATION — --cap-drop ALL did NOT remove CAP_CHOWN.\n"
        f"A plugin could change file ownership inside the container.\n"
        f"Evidence: {result['evidence']}"
    )


# ---------------------------------------------------------------------------
# Test 2 — no-new-privileges is kernel-observable via /proc/self/status
# ---------------------------------------------------------------------------


def test_no_new_privileges_in_proc_status(docker_info, escape_image, linux_kernel_controls, request):
    """
    Kernel-observable check: /proc/self/status must show ``NoNewPrivs: 1`` when
    ``--security-opt no-new-privileges`` is active.

    This is a positive-verification test (expect_failure=False): the container
    must exit zero when the check PASSES (NoNewPrivs is 1).

    WHY /proc/self/status: The Linux kernel writes the PR_SET_NO_NEW_PRIVS state
    into the process's status file in procfs. This is not a Docker API claim or
    a container runtime report — it is the kernel's own record of the privilege
    restriction. Reading it provides kernel-observable evidence (the highest
    assurance tier in the sandbox posture documentation).

    A ``NoNewPrivs: 0`` line (or absence of the line) means the flag was not set,
    which would be a misconfiguration: the container runtime accepted the request
    but the kernel did not apply it.
    """
    if not linux_kernel_controls:
        pytest.skip("--security-opt no-new-privileges requires Linux kernel controls")

    result = run_escape_attempt(
        docker_args=["--security-opt", "no-new-privileges"],
        image=escape_image,
        cmd=[
            "python", "-c",
            (
                "import sys\n"
                "try:\n"
                "    status = open('/proc/self/status').read()\n"
                "except OSError as e:\n"
                "    print(f'FAIL: could not read /proc/self/status: {e}', flush=True)\n"
                "    sys.exit(1)\n"
                "for line in status.splitlines():\n"
                "    if line.startswith('NoNewPrivs:'):\n"
                "        value = line.split(':')[1].strip()\n"
                "        print(f'NoNewPrivs: {value}', flush=True)\n"
                "        if value == '1':\n"
                "            sys.exit(0)  # PASS: kernel confirmed no-new-privileges\n"
                "        else:\n"
                "            print('FAIL: NoNewPrivs is not 1', flush=True)\n"
                "            sys.exit(1)\n"
                "print('FAIL: NoNewPrivs line not found in /proc/self/status', flush=True)\n"
                "sys.exit(1)\n"
            ),
        ],
        attack_vector="privilege_escalation",
        hardening_control="no_new_privileges",
        docker_flag="--security-opt no-new-privileges",
        description=(
            "Kernel-observable check: /proc/self/status must show NoNewPrivs: 1 "
            "when --security-opt no-new-privileges is active. Proves the kernel "
            "acknowledged the PR_SET_NO_NEW_PRIVS request."
        ),
        expect_failure=False,
    )
    record_result(request.node.nodeid, result)
    assert result["status"] == "PASS", (
        f"PRIVILEGE EVIDENCE — /proc/self/status does not show NoNewPrivs: 1.\n"
        f"The no-new-privileges flag may not be active at the kernel level.\n"
        f"Evidence: {result['evidence']}"
    )


# ---------------------------------------------------------------------------
# Test 3 — combined: cap-drop ALL + no-new-privileges stack correctly
# ---------------------------------------------------------------------------


def test_cap_drop_and_no_new_privs_combined(
    docker_info, escape_image, linux_kernel_controls, request
):
    """
    Combined control test: ``--cap-drop ALL`` and ``--security-opt no-new-privileges``
    must both be active simultaneously without interfering with each other.

    We verify three properties in a single container run:
    (a) NoNewPrivs: 1 in /proc/self/status
    (b) CAP_NET_RAW is absent (raw socket fails with EPERM)
    (c) CAP_CHOWN is absent (chown fails with EPERM)

    WHY COMBINED: These two controls are always applied together in the
    production-safe container sandbox profile. The individual tests above verify
    each in isolation; this test verifies they compose correctly and neither
    interferes with the other's enforcement.

    The container exits 0 only when ALL THREE checks pass (good state).
    expect_failure=False because PASS requires exit 0.
    """
    if not linux_kernel_controls:
        pytest.skip("cap-drop + no-new-privileges require Linux kernel controls")

    result = run_escape_attempt(
        docker_args=[
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--read-only",
            "--mount", "type=tmpfs,dst=/tmp,tmpfs-size=8m",
        ],
        image=escape_image,
        cmd=[
            "python", "-c",
            (
                "import sys, os, socket\n"
                "errors = []\n"
                "# (a) NoNewPrivs must be 1\n"
                "try:\n"
                "    status = open('/proc/self/status').read()\n"
                "    nnp = {l.split(':')[0]: l.split(':')[1].strip()\n"
                "           for l in status.splitlines() if ':' in l}\n"
                "    if nnp.get('NoNewPrivs') != '1':\n"
                "        errors.append(f'NoNewPrivs={nnp.get(\"NoNewPrivs\")} (expected 1)')\n"
                "except OSError as e:\n"
                "    errors.append(f'proc read failed: {e}')\n"
                "# (b) CAP_NET_RAW must be gone\n"
                "try:\n"
                "    s = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)\n"
                "    s.close()\n"
                "    errors.append('CAP_NET_RAW still present (raw socket succeeded)')\n"
                "except PermissionError:\n"
                "    pass  # expected\n"
                "# (c) CAP_CHOWN must be gone\n"
                "try:\n"
                "    path = '/tmp/chown_probe.txt'\n"
                "    open(path, 'w').write('probe')\n"
                "    os.chown(path, 1, 1)\n"
                "    errors.append('CAP_CHOWN still present (chown succeeded)')\n"
                "except PermissionError:\n"
                "    pass  # expected\n"
                "if errors:\n"
                "    for e in errors: print(f'FAIL: {e}', flush=True)\n"
                "    sys.exit(1)\n"
                "print('PASS: NoNewPrivs=1, CAP_NET_RAW absent, CAP_CHOWN absent', flush=True)\n"
                "sys.exit(0)\n"
            ),
        ],
        attack_vector="privilege_escalation",
        hardening_control="cap_drop_all+no_new_privileges",
        docker_flag="--cap-drop ALL --security-opt no-new-privileges",
        description=(
            "Combined check: --cap-drop ALL + no-new-privileges must both be active. "
            "NoNewPrivs=1 in /proc, CAP_NET_RAW absent, CAP_CHOWN absent."
        ),
        expect_failure=False,
    )
    record_result(request.node.nodeid, result)
    assert result["status"] == "PASS", (
        f"PRIVILEGE ESCALATION — combined cap-drop + no-new-privs controls failed.\n"
        f"Evidence: {result['evidence']}"
    )
