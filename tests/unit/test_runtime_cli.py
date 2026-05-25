"""
Tests for the aindy-runtime CLI subcommand surface.

Covers:
  A. Dispatch — sys.argv routing in main()
  B. _run_sandbox_check — output, exit codes, error handling
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import patch

from AINDY.runtime_only import _run_sandbox_check, main

pytestmark = pytest.mark.runtime_only

# ── Fixtures ──────────────────────────────────────────────────────────────────

_SATISFIED_POSTURE = {
    "current": {
        "runner_type": "insecure_dev_subprocess",
        "assurance_class": "insecure-dev",
        "runtime_trust_status": "missing-reference",
        "certification_tier": "contained-process-certified",
        "certification_status": "certified",
    },
    "requirement_status": {
        "assurance_class_satisfied": True,
        "certification_tier_satisfied": True,
    },
}

_UNSATISFIED_POSTURE = {
    "current": {
        "runner_type": "insecure_dev_subprocess",
        "assurance_class": "insecure-dev",
        "runtime_trust_status": "missing-reference",
        "certification_tier": "contained-process-certified",
        "certification_status": "certified",
    },
    "requirement_status": {
        "assurance_class_satisfied": False,
        "certification_tier_satisfied": True,
    },
}

_PLATFORM_MATRIX = {"current_platform": "windows"}
_VERIFICATION = {"verification_method": "runner-metadata-only", "kernel_observable": False, "assurance_ceiling": "container-process-boundary"}
_TRUSTED_PY = {"present": False, "total_count": 0}
_CONDITIONS: list = []


def _patch_sandbox_fns(posture=_SATISFIED_POSTURE):
    return [
        patch(
            "AINDY.platform_layer.deployment_contract.plugin_sandbox_assurance_posture",
            return_value=posture,
        ),
        patch(
            "AINDY.platform_layer.sandbox_runner.sandbox_platform_capability_matrix",
            return_value=_PLATFORM_MATRIX,
        ),
        patch(
            "AINDY.platform_layer.health_service.sandbox_verification_posture",
            return_value=_VERIFICATION,
        ),
        patch(
            "AINDY.platform_layer.extension_runtime_inventory.trusted_python_execution_inventory",
            return_value=_TRUSTED_PY,
        ),
        patch(
            "AINDY.platform_layer.deployment_contract.get_api_runtime_conditions",
            return_value=_CONDITIONS,
        ),
    ]


# ── Group A: Dispatch ─────────────────────────────────────────────────────────

def test_sandbox_argv_dispatches_to_sandbox_check(monkeypatch):
    monkeypatch.setattr("sys.argv", ["aindy-runtime", "sandbox"])
    with patch("AINDY.runtime_only._run_sandbox_check", side_effect=SystemExit(0)) as mock_check:
        with pytest.raises(SystemExit) as exc_info:
            main()
    mock_check.assert_called_once()
    assert exc_info.value.code == 0


def test_no_argv_does_not_dispatch_to_sandbox_check(monkeypatch):
    monkeypatch.setattr("sys.argv", ["aindy-runtime"])
    with patch("AINDY.runtime_only._run_sandbox_check") as mock_check:
        with patch("uvicorn.run"):
            with pytest.raises(SystemExit):
                main()
    mock_check.assert_not_called()


def test_unrecognised_argv_does_not_dispatch_to_sandbox_check(monkeypatch):
    monkeypatch.setattr("sys.argv", ["aindy-runtime", "serve"])
    with patch("AINDY.runtime_only._run_sandbox_check") as mock_check:
        with patch("uvicorn.run"):
            with pytest.raises(SystemExit):
                main()
    mock_check.assert_not_called()


# ── Group B: _run_sandbox_check ───────────────────────────────────────────────

def test_sandbox_check_exits_zero_when_requirements_satisfied(capsys):
    patches = _patch_sandbox_fns(_SATISFIED_POSTURE)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        with pytest.raises(SystemExit) as exc_info:
            _run_sandbox_check()
    assert exc_info.value.code == 0


def test_sandbox_check_exits_one_when_requirements_unsatisfied(capsys):
    patches = _patch_sandbox_fns(_UNSATISFIED_POSTURE)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        with pytest.raises(SystemExit) as exc_info:
            _run_sandbox_check()
    assert exc_info.value.code == 1


def test_sandbox_check_prints_valid_json(capsys):
    patches = _patch_sandbox_fns(_SATISFIED_POSTURE)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        with pytest.raises(SystemExit):
            _run_sandbox_check()
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert "plugin_sandbox_posture" in payload
    assert "plugin_sandbox_platform" in payload
    assert "sandbox_verification_posture" in payload
    assert "trusted_python_execution" in payload
    assert "runtime_conditions" in payload


def test_sandbox_check_payload_contains_posture_values(capsys):
    patches = _patch_sandbox_fns(_SATISFIED_POSTURE)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        with pytest.raises(SystemExit):
            _run_sandbox_check()
    payload = json.loads(capsys.readouterr().out)
    assert payload["plugin_sandbox_posture"]["current"]["runner_type"] == "insecure_dev_subprocess"
    assert payload["plugin_sandbox_posture"]["requirement_status"]["assurance_class_satisfied"] is True
    assert payload["plugin_sandbox_platform"] == _PLATFORM_MATRIX
    assert payload["trusted_python_execution"] == _TRUSTED_PY
    assert payload["runtime_conditions"] == []


def test_sandbox_check_exits_two_on_unexpected_error(capsys):
    with patch(
        "AINDY.platform_layer.deployment_contract.plugin_sandbox_assurance_posture",
        side_effect=RuntimeError("db unavailable"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            _run_sandbox_check()
    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "sandbox check failed" in err
    assert "db unavailable" in err
