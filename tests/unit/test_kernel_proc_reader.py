from __future__ import annotations

import os
import platform

import pytest

from AINDY.platform_layer.kernel_proc_reader import (
    compare_namespace_ids,
    read_all_kernel_evidence,
    read_cgroup_membership,
    read_namespace_ids,
    read_seccomp_status,
)
from AINDY.platform_layer.sandbox_runner import (
    VERIFICATION_METHOD_KERNEL_OBSERVABLE,
    VERIFICATION_METHOD_WORKER_SELF_REPORT,
)

pytestmark = pytest.mark.runtime_only


def _is_linux() -> bool:
    return platform.system().lower() == "linux"


def test_read_seccomp_status_for_current_process():
    result = read_seccomp_status(os.getpid())

    assert result["pid"] == os.getpid()
    assert result["source"] == "proc_status"
    assert "error" in result
    if _is_linux():
        assert result["available"] is True
        assert result["seccomp_mode"] in {0, 1, 2}
        assert result["seccomp_mode_label"] in {"none", "strict", "filter", "unknown"}
        assert isinstance(result["seccomp_active"], bool)
    else:
        assert result["available"] is False
        assert result["seccomp_mode"] is None


def test_read_cgroup_membership_for_current_process():
    result = read_cgroup_membership(os.getpid())

    assert result["pid"] == os.getpid()
    assert result["source"] == "proc_cgroup"
    assert "error" in result
    if _is_linux():
        assert result["available"] is True
        assert result["cgroup_version"] in {"v1", "v2"}
        assert isinstance(result["entries"], list)
    else:
        assert result["available"] is False
        assert result["cgroup_version"] is None


def test_read_namespace_ids_for_current_process():
    result = read_namespace_ids(os.getpid())

    assert result["pid"] == os.getpid()
    assert result["source"] == "proc_ns"
    assert "error" in result
    if _is_linux():
        assert result["available"] is True
        assert isinstance(result["namespaces"], dict)
        assert "mnt" in result["namespaces"]
        assert "net" in result["namespaces"]
    else:
        assert result["available"] is False
        assert result["namespaces"] == {}


def test_read_all_kernel_evidence_for_current_process():
    result = read_all_kernel_evidence(os.getpid(), os.getpid())

    assert result["pid"] == os.getpid()
    assert "seccomp" in result
    assert "cgroup" in result
    assert "namespaces" in result
    assert "namespace_comparison" in result
    if _is_linux():
        assert result["kernel_observable"] is True
        assert result["verification_method"] == VERIFICATION_METHOD_KERNEL_OBSERVABLE
    else:
        assert result["kernel_observable"] is False
        assert result["verification_method"] == VERIFICATION_METHOD_WORKER_SELF_REPORT


def test_read_seccomp_status_handles_invalid_pid():
    result = read_seccomp_status(-1)

    assert result["available"] is False
    assert result["pid"] == -1


def test_read_cgroup_membership_handles_invalid_pid():
    result = read_cgroup_membership(-1)

    assert result["available"] is False
    assert result["pid"] == -1


def test_read_namespace_ids_handles_invalid_pid():
    result = read_namespace_ids(-1)

    assert result["available"] is False
    assert result["pid"] == -1


def test_compare_namespace_ids_identical_dicts_are_shared():
    namespaces = {
        "mnt": "mnt:[1]",
        "net": "net:[2]",
        "pid": "pid:[3]",
    }

    result = compare_namespace_ids(namespaces, namespaces)

    assert result["separated_namespaces"] == []
    assert set(result["shared_namespaces"]) == {"mnt", "net", "pid"}
    assert result["comparison_available"] is True
