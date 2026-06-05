"""
Sandbox escape test: network vectors.

WHAT IS BEING TESTED
--------------------
The ``--network none`` Docker flag (``disable_network`` control in
``ContainerizedOciSandboxRunner``) removes all network interfaces from the
container except loopback. A plugin inside such a container must not be able
to make any outbound TCP or UDP connection to external hosts.

WHY THIS MATTERS
----------------
Without network isolation, a malicious or compromised plugin could:
  - Exfiltrate tenant data to a remote attacker-controlled server
  - Phone home to a C2 (command-and-control) server to receive further instructions
  - Perform port-scanning against internal services on the same Docker network
  - Leak secrets (API keys, embedding data, memory contents) via HTTP POST

``--network none`` is the only reliable way to prevent all of these. Firewall
rules are fragile (iptables can be modified from a privileged container) and
application-level network restrictions can be bypassed by plugins that control
their own code. Removing the network interface entirely is the correct approach.

HOW ``--network none`` WORKS
-----------------------------
Docker creates a network namespace for the container with no veth pair and no
bridge connection. The container's ``ip link show`` returns only ``lo`` (loopback).
Any syscall that tries to route a packet to a non-loopback address immediately
receives ENETUNREACH (Network unreachable) or ECONNREFUSED.

This test invokes ``socket.connect()`` from inside the container and verifies
the Python process exits non-zero (connection failed). The exact errno (ENETUNREACH
vs ECONNREFUSED vs ETIMEDOUT) is recorded in the evidence field and varies by
host Docker version and Linux kernel; the important property is exit non-zero.
"""
from __future__ import annotations

import pytest

from tests.sandbox.conftest import record_result, run_escape_attempt

pytestmark = pytest.mark.sandbox_escape


# ---------------------------------------------------------------------------
# Test 1 — --network none blocks outbound TCP
# ---------------------------------------------------------------------------


def test_network_none_blocks_tcp_outbound(docker_info, escape_image, request):
    """
    Attack: plugin opens a TCP socket and connects to 8.8.8.8:53 (Google DNS).
    Control: docker run --network none
    Expected result: connect() raises an exception (ENETUNREACH / ECONNREFUSED /
    ETIMEDOUT); the Python process exits non-zero.

    8.8.8.8:53 is a widely available public host. If --network none is working
    correctly, the TCP SYN packet never leaves the container's network namespace
    and connect() raises immediately with Network unreachable (errno 101 on Linux).

    The 2-second socket timeout prevents the test from hanging if, for some reason,
    the packet is queued rather than immediately rejected — though with --network none
    this should not occur.
    """
    result = run_escape_attempt(
        docker_args=["--network", "none"],
        image=escape_image,
        cmd=[
            "python", "-c",
            (
                "import sys, socket\n"
                "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
                "s.settimeout(2)\n"
                "try:\n"
                "    s.connect(('8.8.8.8', 53))\n"
                "    s.close()\n"
                "    sys.exit(0)  # FAIL: connection succeeded despite --network none\n"
                "except OSError:\n"
                "    sys.exit(1)  # PASS: connection was blocked\n"
            ),
        ],
        attack_vector="network_escape",
        hardening_control="disable_network",
        docker_flag="--network none",
        description=(
            "Plugin attempts TCP connect to 8.8.8.8:53 with --network none active. "
            "Network unreachable / connection refused expected; connection must not succeed."
        ),
        expect_failure=True,
        timeout=15.0,
    )
    record_result(request.node.nodeid, result)
    assert result["status"] == "PASS", (
        f"NETWORK ESCAPE — --network none did NOT block outbound TCP.\n"
        f"A plugin connected to an external host despite network isolation.\n"
        f"Evidence: {result['evidence']}"
    )


# ---------------------------------------------------------------------------
# Test 2 — --network none blocks outbound UDP
# ---------------------------------------------------------------------------


def test_network_none_blocks_udp_outbound(docker_info, escape_image, request):
    """
    Attack: plugin sends a UDP packet to 8.8.8.8:53 (DNS query target).
    Control: docker run --network none
    Expected result: sendto() or the subsequent recvfrom() raises an exception;
    the Python process exits non-zero.

    UDP is connectionless so the socket.connect() model differs slightly: we use
    sendto() followed by a short recvfrom(). With --network none:
    - sendto() may succeed (kernel accepts the datagram) but the packet is immediately
      dropped by the network namespace with no veth
    - recvfrom() with a 1-second timeout should raise socket.timeout or OSError
      (ENETUNREACH) before the data could return

    We treat either a sendto() OSError or a recvfrom() timeout/OSError as PASS,
    because both confirm the packet did not leave the container.
    """
    result = run_escape_attempt(
        docker_args=["--network", "none"],
        image=escape_image,
        cmd=[
            "python", "-c",
            (
                "import sys, socket\n"
                "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
                "s.settimeout(1)\n"
                "try:\n"
                "    # Minimal DNS query header (12 bytes)\n"
                "    s.sendto(b'\\x00\\x01' + b'\\x00' * 10, ('8.8.8.8', 53))\n"
                "    s.recvfrom(512)  # should not receive a reply\n"
                "    sys.exit(0)  # FAIL: received a reply from the network\n"
                "except OSError:\n"
                "    sys.exit(1)  # PASS: blocked by network isolation\n"
            ),
        ],
        attack_vector="network_escape",
        hardening_control="disable_network",
        docker_flag="--network none",
        description=(
            "Plugin sends a UDP datagram to 8.8.8.8:53 with --network none active. "
            "No reply should be receivable; ENETUNREACH or socket.timeout expected."
        ),
        expect_failure=True,
        timeout=10.0,
    )
    record_result(request.node.nodeid, result)
    assert result["status"] == "PASS", (
        f"NETWORK ESCAPE — --network none did NOT block outbound UDP.\n"
        f"A plugin received a UDP reply from an external host.\n"
        f"Evidence: {result['evidence']}"
    )


# ---------------------------------------------------------------------------
# Test 3 — kernel evidence: only loopback interface present
# ---------------------------------------------------------------------------


def test_network_none_only_loopback_interface(docker_info, escape_image, request):
    """
    Kernel-observable check: enumerate network interfaces inside a --network none
    container and verify that only the loopback (lo) interface is present.

    This is a complementary evidence test, not an attack test (expect_failure=False).
    It proves at the kernel level that the network namespace genuinely has no data-plane
    interface — not just that connect() happens to fail. This is the kind of kernel-
    observable evidence described in the assurance posture documentation.

    The script uses Python's ``socket.if_nameindex()`` (available in Python 3.3+)
    which calls getifaddrs() via the C stdlib. We filter out 'lo' and assert the
    remaining list is empty. If any additional interface (eth0, veth0, docker0, etc.)
    appears, the test fails.
    """
    result = run_escape_attempt(
        docker_args=["--network", "none"],
        image=escape_image,
        cmd=[
            "python", "-c",
            (
                "import sys, socket\n"
                "interfaces = [name for _, name in socket.if_nameindex()]\n"
                "non_lo = [i for i in interfaces if i != 'lo']\n"
                "print(f'interfaces: {interfaces}', flush=True)\n"
                "if non_lo:\n"
                "    print(f'FAIL: non-loopback interfaces present: {non_lo}', flush=True)\n"
                "    sys.exit(1)\n"
                "print('PASS: only lo present', flush=True)\n"
                "sys.exit(0)\n"
            ),
        ],
        attack_vector="network_escape",
        hardening_control="disable_network",
        docker_flag="--network none",
        description=(
            "Kernel-observable check: only the loopback interface (lo) must be present "
            "in the container's network namespace when --network none is active."
        ),
        expect_failure=False,
        timeout=10.0,
    )
    record_result(request.node.nodeid, result)
    assert result["status"] == "PASS", (
        f"NETWORK EVIDENCE — unexpected network interfaces found inside --network none container.\n"
        f"Evidence: {result['evidence']}"
    )
