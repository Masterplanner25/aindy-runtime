"""Turn a silently skipped MCP suite into a red build (FR-66 phase 1).

Eight test files `pytest.importorskip` the MCP stack, so if it fails to install or import, every
one of them skips and the job stays green. That happened on a Windows host where Application
Control blocked pydantic-core's DLL in a fresh venv. `Runtime Contracts` installs the `[mcp]`
extra and sets `AINDY_REQUIRE_MCP=1`; under it these tests fail instead of skipping. Local runs
leave it unset. The same shape as `AINDY_REQUIRE_NATIVE_BRIDGE`.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.runtime_only


def _required() -> bool:
    return os.getenv("AINDY_REQUIRE_MCP", "").strip().lower() in {"1", "true", "yes", "on"}


def test_the_mcp_stack_imports_when_ci_says_it_must():
    if not _required():
        pytest.skip("AINDY_REQUIRE_MCP not set — local run, the [mcp] extra is optional")

    import mcp  # noqa: F401
    import nodus_mcp_aindy
    from mcp.server.transport_security import TransportSecuritySettings  # noqa: F401

    assert hasattr(nodus_mcp_aindy.NodusServer, "streamable_http_app"), (
        "nodus-mcp is older than 0.1.6: --transport http cannot be served"
    )


def test_the_installed_nodus_mcp_meets_the_floor_when_required():
    if not _required():
        pytest.skip("AINDY_REQUIRE_MCP not set — local run, the [mcp] extra is optional")

    from importlib.metadata import version

    from packaging.version import Version

    assert Version(version("nodus-mcp")) >= Version("0.1.6")
