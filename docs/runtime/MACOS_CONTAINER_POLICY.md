# macOS Container Sandbox Policy

**Status:** Policy defined 2026-06-06. Escape suite certification pending (see Verification section).

---

## Context

Docker Desktop on macOS runs Linux containers inside a Linux VM. On macOS 12 (Monterey) and
later, the VM uses **Apple Virtualization Framework**. On older macOS, it uses **HyperKit**
(a Type 2 hypervisor). In either case, the containers run inside a genuine Linux kernel, not
a macOS process namespace. This makes the security semantics equivalent to Docker Desktop on
Windows (WSL2/Hyper-V) and to a native Linux host for the controls listed below.

---

## What IS available on macOS + Docker Desktop Linux containers

These controls are enforced by the Linux VM kernel and are safe to use:

| Control | Docker flag | What it enforces |
|---|---|---|
| Network isolation | `--network none` | Blocks all outbound connections inside the container |
| Read-only rootfs | `--read-only` | Prevents any write to the container filesystem |
| Read-only bind mount | `--mount type=bind,...,readonly` | Plugin root is read-only |
| Drop all capabilities | `--cap-drop ALL` | Removes all Linux capabilities; blocks raw sockets, chown, etc. |
| No new privileges | `--security-opt no-new-privileges` | Prevents privilege escalation via setuid or file capabilities |
| PID limit | `--pids-limit N` | Hard cap on number of processes; blocks fork bombs |

These are the same six categories proved by the Phase 0 adversarial escape suite.

---

## What is NOT claimed on macOS + Docker Desktop

| Control | Reason |
|---|---|
| `seccomp` profile injection | Not tested; Docker Desktop may apply a default profile but custom profile injection is not verified |
| AppArmor profiles | AppArmor is a Linux MAC framework; macOS does not implement it |
| SELinux labels | SELinux is a Linux MAC framework; macOS does not implement it |

These controls are gated on `_normalized_platform_system() == "linux"` in
`inspect_container_kernel_controls()` and will not be reported as available on macOS.

---

## Assurance tier

macOS + Docker Desktop in Linux containers mode reaches **container-grade-sandbox**
(`ASSURANCE_CLASS_CONTAINER`) — the same tier as:

- Windows + Docker Desktop (WSL2 or Hyper-V backend)
- Native Linux + Docker

This is the `RUNNER_CONTAINERIZED_OCI` runner. The **strong sandbox VM**
(`RUNNER_STRONG_SANDBOX_VM`) requires native Linux or an explicit macOS integration via
`aindy-sandbox-vm`. That launcher does not exist yet; macOS is not in
`STRONG_SANDBOX_SUPPORTED_HOST_PLATFORMS`.

---

## Runtime detection

`_detect_wsl2(container_runtime)` in `sandbox_runner.py` detects macOS Docker Desktop:

```python
result = _detect_wsl2("docker")
# On macOS + Docker Desktop Linux containers mode:
assert result["docker_macos_backend"] is True
assert result["wsl2_kernel_available"] is True   # covers all Linux VM backends
```

`ContainerizedOciSandboxRunner` calls `_detect_linux_container_backend()` at construction
time and passes the result through to `inspect_container_kernel_controls()`, so runner
metadata is accurate on macOS.

---

## Verification requirement

**The Phase 0 escape suite must pass on a macOS host before certifying a macOS deployment.**

```bash
pytest -m sandbox_escape -v
# Expected: 17/17 PASS (or all non-skipped tests PASS)
```

After the run, append an entry to `docs/runtime/SANDBOX_ESCAPE_AUDIT.md` following the
existing Entry 001 format. Record:

- macOS version and chip (Intel/Apple Silicon)
- Docker Desktop version
- Container image digest (`docker image inspect --format '{{.Id}}' python:3.11-alpine`)
- Commit hash
- Per-category result breakdown

**Current certification status:**

| Platform | Run date | Result |
|---|---|---|
| Windows 11 + Docker Desktop 29.2.1 (WSL2) | 2026-06-05 | 17/17 PASS — Entry 001 |
| macOS | Not yet run | Certification pending |

---

## Traceability

| Artifact | Location |
|---|---|
| Phase 0 escape test suite | `tests/sandbox/` |
| Phase 0 results artifact | `tests/sandbox/sandbox_escape_results.json` |
| Audit log (Entry 001) | `docs/runtime/SANDBOX_ESCAPE_AUDIT.md` |
| macOS detection in code | `sandbox_runner.py` — `_detect_wsl2()`, `docker_macos_backend` field |
| Release gate (Step 16) | `docs/runtime/RELEASE_CHECKLIST.md` |
