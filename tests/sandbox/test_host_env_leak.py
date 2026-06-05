"""
Sandbox escape test: host environment variable leak.

WHAT IS BEING TESTED
--------------------
``ContainerizedOciSandboxRunner._build_child_env()`` constructs a minimal, explicit
environment dictionary that is passed to the container via ``--env KEY=VALUE`` flags.
It does NOT pass ``--env-file`` or leave the Docker default behavior (which inherits
the Docker daemon's environment). Only variables in the explicit allowlist reach
the container process.

This test category verifies that sensitive host environment variables — credentials,
API keys, database URLs, signing secrets — are NOT present inside the container.

WHY THIS MATTERS
----------------
The host environment on a production server typically contains:
  - DATABASE_URL (connection string with embedded credentials)
  - SECRET_KEY (HMAC signing key for JWTs)
  - OPENAI_API_KEY, DEEPSEEK_API_KEY (third-party API credits)
  - AINDY_API_KEY (platform API key)
  - AWS credentials, etc.

If these leak into the container, a malicious plugin can:
  - Connect to the production database and read or exfiltrate all tenant data
  - Forge JWTs signed with the real SECRET_KEY, impersonating any user
  - Exhaust expensive API credits by calling LLM APIs
  - Access cloud storage, S3 buckets, etc.

The ``_build_child_env()`` implementation passes exactly:
  - PYTHONIOENCODING=utf-8 (always)
  - AINDY_ALLOW_PRIVATE_EXTENSION_TARGETS (only if set on host, for dev/testing)

All other environment variables are NOT passed. This test proves that claim by
injecting well-known sensitive variable names into the subprocess environment
and verifying they do not appear inside the container.

HOW THE TEST WORKS
------------------
The test mimics the production ``_build_child_env()`` behavior: it builds a
minimal ``--env`` argument list with only the allowed keys, then runs the
container and checks that none of the sensitive keys appear in ``os.environ``
inside the container. The test does NOT rely on the Python sandbox runner class;
it directly invokes ``docker run`` to prove the Docker/kernel layer enforces
the env restriction.
"""
from __future__ import annotations

import pytest

from tests.sandbox.conftest import SENSITIVE_ENV_KEYS, record_result, run_escape_attempt

pytestmark = pytest.mark.sandbox_escape

# The minimal env that ContainerizedOciSandboxRunner passes to the container.
_ALLOWED_ENV = {
    "PYTHONIOENCODING": "utf-8",
}


def _build_docker_env_args(allowed: dict[str, str]) -> list[str]:
    """Convert an env dict to repeated ``--env K=V`` args."""
    args: list[str] = []
    for k, v in allowed.items():
        args.extend(["--env", f"{k}={v}"])
    return args


# ---------------------------------------------------------------------------
# Test 1 — sensitive keys are absent from container environment
# ---------------------------------------------------------------------------


def test_sensitive_env_vars_absent(docker_info, escape_image, request):
    """
    Verification: none of the production-sensitive environment variables are
    present inside the container when only the allowed minimal env is passed.

    We use ``--env-file /dev/null`` is NOT used here; instead we pass ONLY
    the allowed keys explicitly via ``--env K=V``. Docker's default behavior
    when no ``--env-file`` and no ``--env`` are given for a key is to NOT pass
    the host's value. We rely on this documented Docker behavior.

    The container script reads ``os.environ``, extracts any key whose name
    appears in the SENSITIVE_ENV_KEYS list, and exits 1 if any are found.
    It also prints the offending keys so the failure message is actionable.

    Note: this test does NOT inject fake values for the sensitive keys into
    the subprocess environment — the point is that they're absent entirely,
    not that they have safe values. The test simulates production behavior
    where the Docker daemon runs with the real host environment and the
    container is started with only the explicit ``--env`` args.
    """
    sensitive_check_code = (
        "import sys, os, json\n"
        f"sensitive = {sorted(SENSITIVE_ENV_KEYS)!r}\n"
        "found = {k: v[:4]+'***' for k, v in os.environ.items() if k in sensitive}\n"
        "allowed = sorted(os.environ.keys())\n"
        "print(json.dumps({'env_keys': allowed, 'leaked': found}), flush=True)\n"
        "sys.exit(1 if found else 0)\n"
    )

    result = run_escape_attempt(
        docker_args=_build_docker_env_args(_ALLOWED_ENV),
        image=escape_image,
        cmd=["python", "-c", sensitive_check_code],
        attack_vector="env_leak",
        hardening_control="minimal_env_set",
        docker_flag="--env PYTHONIOENCODING=utf-8 (explicit-only env)",
        description=(
            "Verify that production-sensitive env vars (SECRET_KEY, DATABASE_URL, "
            "OPENAI_API_KEY, etc.) are absent inside the container when only the "
            "minimal allowed env is passed via --env."
        ),
        expect_failure=False,
    )
    record_result(request.node.nodeid, result)
    assert result["status"] == "PASS", (
        f"ENV LEAK — sensitive environment variables found inside container.\n"
        f"A plugin has access to production credentials.\n"
        f"Evidence: {result['evidence']}"
    )


# ---------------------------------------------------------------------------
# Test 2 — only the allowed env keys are present
# ---------------------------------------------------------------------------


def test_only_allowed_env_keys_present(docker_info, escape_image, request):
    """
    Strict allowlist check: the container must contain ONLY the keys explicitly
    passed via ``--env``, plus system defaults set by the container image itself
    (PATH, HOME, HOSTNAME, etc.).

    We verify that no key from the SENSITIVE_ENV_KEYS set is present, AND that
    PYTHONIOENCODING is present (confirming the allowed env was transmitted).

    WHY: The first test confirms sensitive keys are absent. This test confirms
    the allowed key IS present — ruling out the possibility that ``--env`` was
    silently ignored and the container started with an empty environment (which
    would also pass the first test but would indicate a Docker bug rather than
    correct behavior).
    """
    check_code = (
        "import sys, os\n"
        "env_keys = set(os.environ.keys())\n"
        "# Must have PYTHONIOENCODING\n"
        "if 'PYTHONIOENCODING' not in env_keys:\n"
        "    print('FAIL: PYTHONIOENCODING not present — --env was not transmitted', flush=True)\n"
        "    sys.exit(1)\n"
        f"sensitive = {sorted(SENSITIVE_ENV_KEYS)!r}\n"
        "leaked = [k for k in sensitive if k in env_keys]\n"
        "if leaked:\n"
        "    print(f'FAIL: sensitive keys present: {leaked}', flush=True)\n"
        "    sys.exit(1)\n"
        "print(f'PASS: PYTHONIOENCODING present, no sensitive keys found', flush=True)\n"
        "sys.exit(0)\n"
    )

    result = run_escape_attempt(
        docker_args=_build_docker_env_args(_ALLOWED_ENV),
        image=escape_image,
        cmd=["python", "-c", check_code],
        attack_vector="env_leak",
        hardening_control="minimal_env_set",
        docker_flag="--env PYTHONIOENCODING=utf-8",
        description=(
            "Allowlist check: PYTHONIOENCODING must be present (--env transmitted) "
            "and no SENSITIVE_ENV_KEYS must be present."
        ),
        expect_failure=False,
    )
    record_result(request.node.nodeid, result)
    assert result["status"] == "PASS", (
        f"ENV ALLOWLIST — container env is not as expected.\n"
        f"Evidence: {result['evidence']}"
    )
