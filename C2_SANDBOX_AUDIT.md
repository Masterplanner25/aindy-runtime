# C2 Sandbox Audit — Cross-Platform Container-Grade Sandbox

**Date:** 2026-05-24
**Scope:** Close the C2 reopen condition from ISOLATION_MODEL_PLAN.md by
certifying the `containerized_oci` runner at the `container-sandbox-certified`
tier on at least one non-Linux platform. Strong-sandbox cross-platform parity
is out of scope and becomes a separately tracked future gap (C3).
**Reopen condition restatement (from ISOLATION_MODEL_PLAN.md C2):** A non-Linux
host platform produces a sandbox runner type passing the shared worker policy
certification suite with assurance class at or above `container-grade-sandbox`.

---

## Executive Summary

The C2 work is materially smaller than the prior framing suggested. The runtime
already has:

- a clean, platform-neutral runner abstraction (`SandboxRunner` ABC with three
  concrete implementations sharing a `_JsonRpcProcessRunner` base)
- a platform capability matrix that enumerates Windows, macOS, and Other as
  first-class entries with per-platform `available_runner_types`,
  `available_hardening_controls`, and `equivalence_status`
- a certification framework whose evidence fields (backend identity, runtime
  identity, mount mode, resource limit mode) are platform-neutral
- a kernel-evidence reader that degrades cleanly to worker-self-report on
  non-Linux without breaking the verification flow
- an execution-model contract that **already declares cross-platform support**
  for isolated plugin nodes
- a `containerized_oci` runner that **already runs** on Windows and macOS via
  Docker Desktop / Podman; it just declines to claim production-safe status

The Linux-only gate is concentrated in a small, well-defined surface:

1. one constant tuple in `sandbox_runner.py`
2. one bit of platform-matrix-entry logic that derives the
   `production_safe_third_party_plugin_execution` field
3. two validation paths in `deployment_contract.py` that consume that bit

The audit identifies **eight findings** (NF-1 through NF-8), of which five are
code changes scoped to closing C2-Container and three are contract / docs
changes that resolve a mismatch between what the runtime enforces and what the
documentation claims.

---

## Findings

### NF-1 — `PRODUCTION_SAFE_CONTAINER_SUPPORTED_HOST_PLATFORMS` excludes platforms that genuinely deliver Linux container semantics

**Severity:** Core gate — closing this is the central C2-Container change.

**Citation:**
`AINDY/platform_layer/sandbox_runner.py` line 50:
```python
PRODUCTION_SAFE_CONTAINER_SUPPORTED_HOST_PLATFORMS = (PLATFORM_LINUX,)
```

**What the code actually does today:** This constant is read by
`_platform_matrix_entry` (line ~390) to set the `production_safe_third_party_plugin_execution`
field on the published platform matrix entry. It is also referenced in the
`support_contract.production_safe_container_supported_host_platforms` field
exposed through `sandbox_platform_capability_matrix`.

**Where it bites:** `deployment_contract.py` lines ~720-740 in
`validate_external_third_party_plugin_runtime_policy`:

```python
if resolved == RUNNER_CONTAINERIZED_OCI and not bool(
    ((policy.get("platform_matrix") or {}).get("current_environment") or {}).get(
        "production_safe_third_party_plugin_execution",
        False,
    )
) and production_safe_plugin_sandbox_required(profile_name):
    ...
    raise RuntimeError(...)
```

The same check appears in `validate_plugin_sandbox_profile_policy` at startup.
Together these two checks form the runtime enforcement of the Linux-only
policy.

**The contract question this gates (recommended answer below in NF-2):** When
Docker Desktop runs on Windows or macOS, the containers are Linux containers
running on a Linux kernel inside a Hyper-V or HVF virtual machine. The Linux
kernel controls (`no_new_privileges`, `seccomp`, `cap_drop`, `pids_limit`) are
actually in force inside the container — they just aren't reportable through
the host-side `_supports_linux_container_kernel_controls()` check, which
inspects the host kernel, not the container target. The current constant is
conservative to the point of being inaccurate: it says "this platform cannot
provide Linux container semantics," when in fact the platform *does* provide
Linux container semantics through documented, well-established virtualization.

**Done condition:** `PRODUCTION_SAFE_CONTAINER_SUPPORTED_HOST_PLATFORMS`
includes `PLATFORM_WINDOWS` and `PLATFORM_MACOS` when paired with an
appropriate runtime detection check (NF-3) confirming that the host's
configured container runtime is a Linux-containers backend. The
`_platform_matrix_entry` derivation updates accordingly. The two
`deployment_contract.py` validation paths pass on Windows / macOS when the
container runtime is detected and configured.

---

### NF-2 — Contract claim ambiguity: what does `production_safe_third_party_plugin_execution` mean

**Severity:** Contract decision — must be resolved before NF-1 can be merged.

**Citation:**
`AINDY/platform_layer/sandbox_runner.py` line 519, in `_platform_matrix_entry`:
```python
"production_safe_third_party_plugin_execution": bool(linux and runtime_available),
```

**The ambiguity:** The field name suggests "this host platform can run
production-safe third-party plugins." The current implementation tests "is the
host Linux *and* is a container runtime present." Those are two different
claims. On Windows + Docker Desktop, the second condition is true (Docker is
present), but the first is false, so the field is `False`.

The honest reading of the field's intent — given the rest of the matrix
explicitly distinguishes "host kernel hardening" from "container execution" —
is **the former**: can this host produce a sandboxed Linux container
environment with Linux kernel hardening controls active inside the container.
Docker Desktop does deliver this. WSL2 does deliver this. Podman with a Linux
VM does deliver this. The current bit is incorrect when read against that
intent.

**Recommended contract answer (per scope decision):**
`production_safe_third_party_plugin_execution` means **"this host can execute
Linux containers with documented Linux kernel hardening controls active
inside the container."** Under that meaning:

- Linux with a container runtime → True
- Windows with Docker Desktop (Linux container mode) or Podman → True
- macOS with Docker Desktop or Podman (Linux VM backend) → True
- Linux without a container runtime → False
- Windows with Docker in Windows-container mode → False (containers are not
  Linux containers; different kernel hardening surface)
- Any host without a recognized Linux-container backend → False

This shifts the gate from "host platform" to "container backend semantics,"
which is the gate that actually matches what the certification framework
verifies (verified backend identity, verified Linux container runtime
identity, verified Linux read-only mount semantics).

**Done condition:** `EXTENSION_TRUST_MODEL.md` Supported Platform Sandbox
Matrix section is rewritten to express the gate as "Linux container semantics
available" rather than "host platform is Linux." The new SANDBOX_CONTRACT.md
(NF-7) documents the meaning explicitly.

---

### NF-3 — No backend detection for "is this container runtime a Linux-containers backend"

**Severity:** Required to safely close NF-1 without overclaiming.

**Citation:** `AINDY/platform_layer/sandbox_runner.py` lines 525-545, in
`sandbox_platform_capability_matrix`:
```python
runtime_name = str(container_runtime or settings.AINDY_PLUGIN_CONTAINER_RUNTIME or "docker")...
runtime_available = shutil.which(runtime_name) is not None
```

Detection today is "is the binary on PATH." That's sufficient on Linux because
Docker on Linux only does Linux containers. On Windows, `docker` could be
Docker Desktop in Linux-container mode (which we want to support) or in
Windows-container mode (which is a different sandbox surface entirely and is
out of scope). The current check cannot distinguish.

**What we need:** A small detection helper — probably calling
`docker version --format json` or `docker info --format json` once at startup,
parsing `Server.OSType` — that returns whether the configured container
runtime is currently configured for Linux containers. Cache the result on the
matrix entry. On detection failure, fail closed: report `production_safe = False`.

**Done condition:** A new `_detect_linux_container_backend(container_runtime: str)`
helper exists in `sandbox_runner.py`. `_platform_matrix_entry` calls it and
sets `production_safe_third_party_plugin_execution` based on **(host is Linux)
OR (host is Windows/macOS AND backend reports Linux container OSType)**. On
non-Linux hosts where the helper returns `False` or errors, the field stays
`False` and the matrix carries an explanatory entry in `degraded_modes`.

**Out of scope:** Windows-container support. If the helper returns
`OSType=windows`, we report `production_safe = False` with an explanatory
note. Adding a real Windows-container path is a future gap.

---

### NF-4 — `_platform_matrix_entry` hardcodes `linux = platform_name == PLATFORM_LINUX` as the only path to several fields

**Severity:** Must be updated in lockstep with NF-1 / NF-3 to avoid producing
internally inconsistent matrix entries.

**Citation:** `AINDY/platform_layer/sandbox_runner.py` lines 395-470, the body
of `_platform_matrix_entry`. Several derived fields key off `linux`:

```python
linux = platform_name == PLATFORM_LINUX
...
if linux and runtime_available:
    available_hardening_controls["containerized_oci"].extend([
        "no_new_privileges", "drop_all_capabilities", "pids_limit",
        "seccomp_profile", "apparmor_profile", "selinux_label",
    ])
...
container_grade_supported = bool(runtime_available)
strong_sandbox_supported = bool(linux and strong_runtime_available)
...
"production_safe_third_party_plugin_execution": bool(linux and runtime_available),
```

**What changes:** The `linux and runtime_available` test for the production-safe
field needs to become `(linux and runtime_available) or (linux_container_backend_available)`.
The `container_grade_supported` field already correctly uses just
`runtime_available` and doesn't need to change. The `available_hardening_controls`
extension for the Linux-only kernel controls *also* doesn't need to change:
those controls are reported only when `linux and runtime_available` is true,
which is correct — Docker Desktop on Windows can't surface AppArmor profile
selection through the host-side check (the controls *are* active in the VM,
but we can't introspect them from outside). The honest thing to do is keep
those off the `available_hardening_controls` list on non-Linux hosts.

The honesty principle the existing code follows ("we report what we can
actually surface through host-side introspection, not what we suspect is true
inside the container") is preserved.

**Done condition:** `_platform_matrix_entry` cleanly distinguishes "container
runtime present and configured for Linux containers" from "Linux host kernel
controls are introspectable from the host side." The `degraded_modes` list on
non-Linux production-safe entries clearly notes that Linux kernel controls are
in force inside the container but not host-introspectable.

---

### NF-5 — Certification suite has zero Linux-specific checks, but no tests exercise it on a non-Linux platform

**Severity:** Audit-positive — the framework already generalizes. Tests need
to follow.

**Citation:** `AINDY/platform_layer/sandbox_certification.py` lines 230-275,
the `RUNNER_CONTAINERIZED_OCI` branch of `_runner_certification_tier`:

```python
if runner_type == RUNNER_CONTAINERIZED_OCI:
    missing: list[str] = []
    if str((support_levels.get("container_sandbox") or {}).get("support") or "") != "supported":
        missing.append("platform_support.container_sandbox")
    if assurance_class != "container-grade-sandbox":
        missing.append("assurance_class")
    if str(launch_attestation.get("status") or "") != "launch-observed":
        missing.append("launch_attestation.status")
    if str(resource_limits.get("enforcement") or "") != "container-runtime-hard-limits":
        missing.append("resource_limits.enforcement")
    if not bool(runtime_trust_chain.get("accepted_for_production_safe_profiles")):
        missing.append("runtime_identity.trust_chain")
    for field_name in ("backend_identity", "runtime_identity", "mount_mode", "resource_limit_mode"):
        if field_name not in verified_fields:
            missing.append(f"verified.{field_name}")
    if not missing:
        return CERTIFICATION_TIER_CONTAINER_SANDBOX, []
    return None, missing
```

Every check is platform-neutral. None inspect `platform.system()`. None
reference Linux paths. The certification tier `container-sandbox-certified`
is reachable on Windows or macOS the instant the platform matrix's
`container_sandbox.support` field is `"supported"` (set by NF-3 / NF-4) and
the runner produces the four verified attestation fields (which it already
does on every platform).

**What needs to change:** No code change in `sandbox_certification.py`. New
tests are required.

**Done condition:** `tests/unit/test_plugin_sandbox_certification.py` includes
parameterized tests covering Windows and macOS host paths (mocked via
`platform.system` patching and a stubbed container backend detection),
asserting that `sandbox_certification_profile` for a fully-configured
`ContainerizedOciSandboxRunner` returns `tier_status: "certified"` at tier
`container-sandbox-certified`. A negative test confirms the profile remains
uncertified when the container backend is reported as Windows-containers
(NF-3 negative path).

---

### NF-6 — `extension_execution_model.py` already declares cross-platform support that the deployment-contract layer contradicts

**Severity:** Contract / code mismatch. Code changes from NF-1 / NF-3 close
the mismatch.

**Citation:** `AINDY/platform_layer/extension_execution_model.py` lines
197-210, the `dynamic-plugin-node:external-third-party` surface entry:

```python
"platform_support": {
    "supported_host_platforms": list(ALL_CHARACTERIZED_HOST_PLATFORMS),
    "production_safe_host_platforms": list(
        support_contract.get("production_safe_third_party_supported_host_platforms")
        or []
    ),
    "strong_sandbox_supported_host_platforms": list(
        support_contract.get("strong_sandbox_supported_host_platforms") or STRONG_SANDBOX_HOST_PLATFORMS
    ),
    ...
}
```

`ALL_CHARACTERIZED_HOST_PLATFORMS = ["linux", "windows", "darwin", "other"]`.
The contract already says all four platforms are supported host platforms for
the third-party plugin surface. The `production_safe_host_platforms` field is
sourced from `support_contract` — which today is empty for third-party because
`sandbox_runner.py` doesn't expose a `production_safe_third_party_supported_host_platforms`
key (this is a small naming gap; the value is implicit in
`PRODUCTION_SAFE_CONTAINER_SUPPORTED_HOST_PLATFORMS`).

**The contract claim:** "Third-party plugin nodes are supported on Linux,
Windows, macOS, and Other."

**The runtime reality before NF-1:** Third-party plugin admission fails on
Windows and macOS for any production-safe deployment profile because
`production_safe_third_party_plugin_execution` is `False`.

**After NF-1 / NF-3 close:** Contract and runtime agree. No further change
needed in `extension_execution_model.py` — but the support_contract surface
needs a new key (NF-7) so the contract's `production_safe_host_platforms`
field is populated correctly.

**Done condition:** After NF-1 / NF-3 / NF-7 land, querying
`extension_execution_model_contract()["surface_matrix"]` for the third-party
dynamic plugin node surface returns a `production_safe_host_platforms` list
that includes Windows / macOS when those hosts have a Linux-container backend.

---

### NF-7 — `support_contract` exposes some host-platform lists but not the production-safe third-party list

**Severity:** Small inconsistency. Naming gap.

**Citation:** `AINDY/platform_layer/sandbox_runner.py` lines 547-585,
`support_contract` block inside `sandbox_platform_capability_matrix`:

```python
support_contract = {
    "claim_scope": "platform-specific-assurance-contract",
    "contained_process_supported_host_platforms": list(CONTAINED_PROCESS_HOST_PLATFORMS),
    "container_grade_supported_host_platforms": list(CONTAINER_GRADE_HOST_PLATFORMS),
    "production_safe_container_supported_host_platforms": list(
        PRODUCTION_SAFE_CONTAINER_SUPPORTED_HOST_PLATFORMS
    ),
    "strong_sandbox_supported_host_platforms": list(STRONG_SANDBOX_SUPPORTED_HOST_PLATFORMS),
    "hostile_third_party_supported_host_platforms": list(HOSTILE_THIRD_PARTY_SUPPORTED_HOST_PLATFORMS),
    ...
}
```

Note: `production_safe_container_supported_host_platforms` (note `_container_`)
is present, but `extension_execution_model.py` line 200 queries
`support_contract.get("production_safe_third_party_supported_host_platforms")`,
a key that doesn't exist. The result is the field silently resolves to `[]`.

**What changes:** Add `production_safe_third_party_supported_host_platforms`
as a key in `support_contract`, sourced from the same value (the constant
updated by NF-1). It can be an alias of the container key, or — better — the
runtime-resolved set of platforms where third-party plugins can actually run
production-safe given the active backend detection.

**Done condition:** `support_contract` contains both the static
`production_safe_container_supported_host_platforms` list (the declared
support set) and a dynamic `production_safe_third_party_supported_host_platforms`
list (what the running runtime can actually support on this host given backend
detection). `extension_execution_model.py`'s query into that key returns
non-empty on platforms with a configured Linux-container backend.

---

### NF-8 — `EXTENSION_TRUST_MODEL.md` Supported Platform Sandbox Matrix needs honest restatement

**Severity:** Docs / contract clarification. Required to close C2.

**Citation:** `docs/runtime/EXTENSION_TRUST_MODEL.md`, the "Supported Platform
Sandbox Matrix" section. Current Windows and macOS entries say:

> Windows
>   - production-safe third-party plugin sandbox support: no
>   - degraded mode: Linux-only kernel hardening controls are not reported as
>     enforceable on the Windows host

> macOS
>   - production-safe third-party plugin sandbox support: no
>   - degraded mode: container execution depends on host virtualization and
>     does not imply native macOS kernel policy enforcement

**After C2 closes:** Both entries need to read approximately:

> Windows
>   - production-safe third-party plugin sandbox support: yes, when the
>     configured container runtime is in Linux-containers mode (e.g., Docker
>     Desktop, Podman). Linux kernel hardening controls run inside the
>     container's Linux kernel but are not host-introspectable.
>   - degraded mode: Windows-containers mode is not currently supported; the
>     runtime falls back to insecure_dev_subprocess or refuses third-party
>     plugin execution.

> macOS
>   - production-safe third-party plugin sandbox support: yes, when the
>     configured container runtime is in Linux-containers mode (e.g., Docker
>     Desktop, Podman). Linux kernel hardening controls run inside the
>     container's Linux VM kernel but are not host-introspectable; native
>     macOS kernel policy enforcement is not implied.

The "strong sandbox" rows for Windows / macOS stay at "no" — that's the C3
work, not C2.

**Done condition:** EXTENSION_TRUST_MODEL.md matrix accurately describes the
post-NF-1 / NF-3 reality. The "Important implications" section is updated to
remove the "Linux-only" framing for production-safe support and to retain it
only for strong-sandbox support.

---

## Gap Classifications

| Finding | Resolution path | Owner | Depends on |
|---|---|---|---|
| NF-1 | CODE CHANGE | sandbox_runner.py | NF-2 (decided), NF-3 |
| NF-2 | CONTRACT DECISION (resolved per scope) | — | — |
| NF-3 | CODE CHANGE | sandbox_runner.py | — |
| NF-4 | CODE CHANGE | sandbox_runner.py | NF-1, NF-3 |
| NF-5 | TEST ADDITION | test_plugin_sandbox_certification.py | NF-1, NF-3, NF-4 |
| NF-6 | AUTO-RESOLVES (after NF-1 / NF-3 / NF-7) | — | NF-1, NF-3, NF-7 |
| NF-7 | CODE CHANGE | sandbox_runner.py | — |
| NF-8 | DOC CHANGE | EXTENSION_TRUST_MODEL.md | NF-1, NF-3, NF-4 |

---

## What This Audit Does NOT Cover

These are deliberately out of scope per the C2-Container framing:

- **Strong-sandbox cross-platform parity.** Becomes future Gap C3. The
  `STRONG_SANDBOX_SUPPORTED_HOST_PLATFORMS = (PLATFORM_LINUX,)` and
  `HOSTILE_THIRD_PARTY_SUPPORTED_HOST_PLATFORMS = (PLATFORM_LINUX,)` constants
  stay Linux-only. Hostile-third-party deployment profile stays Linux-only.
- **Native Windows containers.** NF-3's detection deliberately treats
  Windows-containers mode as unsupported and falls back. Adding a real
  Windows-containers path is its own future gap.
- **macOS Virtualization.framework runner.** Would be a fourth sandbox runner.
  Out of scope.
- **Kernel-observable verification on non-Linux hosts.** `kernel_proc_reader.py`
  stays Linux-only by design. On non-Linux production-safe paths,
  `verification_method` correctly stays at `worker-self-report-verified` for
  the container runner, which is the same ceiling it has on Linux for
  `containerized_oci`.
- **Existing extension-worker guards.** Unchanged. The Python-level guards
  (`builtins.__import__`, `socket.create_connection`, filesystem
  monkey-patches, env stripping) are already platform-neutral and continue to
  work identically.

---

## Open Operational Questions

These are questions the implementation phase will need to answer; documenting
them now so they don't get lost.

**Q1 — Backend detection cache invalidation.** If `docker info` is called
once at startup and Docker is later switched from Linux-containers to
Windows-containers mode, the runtime would have stale information. How should
this be handled? Options: (a) re-detect on every host start; (b) re-detect
periodically; (c) accept stale information and document it; (d) refuse
host start unless explicitly configured.

**Q2 — Behavior when backend detection fails.** If `docker info` errors out,
hangs, or returns an unparseable response, what does the matrix report? The
safe default is `production_safe = False`. Should there be an operator escape
hatch (`AINDY_PLUGIN_CONTAINER_ASSUME_LINUX_BACKEND=true`) for environments
where detection is impractical?

**Q3 — Should certification distinguish "Linux-on-Linux" from "Linux-on-VM"?**
Currently the certification framework would return the same
`container-sandbox-certified` tier on a Linux host with `seccomp` active and
on a Windows host with Linux containers running under Hyper-V. Both deliver
Linux container semantics, but the host-side introspection differs. Should
the certification profile include a `backend_host_platform` field so
operators can see which path is in use?

**Q4 — Documentation of WSL2 specifically.** WSL2 on Windows is a common
deployment configuration that's neither purely "Linux host" nor purely
"Docker Desktop on Windows" — it's a Linux distro running under Hyper-V with
Docker speaking to a Linux kernel directly. Does it report as `linux` from
`platform.system()` (yes, when AINDY runs *inside* WSL2) or as `windows` (yes,
when AINDY runs on Windows talking to Docker Desktop's WSL2 backend)? Both
cases should be handled; the matrix should document them.

---

## Verification Strategy for the Implementation Phase

Per the idempotency-arc pattern: live verification matters and gets done
before contract documents are finalized.

1. **Unit tier:** test_plugin_sandbox_certification.py parameterized on
   `platform.system()` returning each of `Linux`, `Windows`, `Darwin`, with
   `_detect_linux_container_backend` mocked to return `True`, `False`, and
   error. Asserts certification tier and `production_safe` field behave
   correctly across the matrix.

2. **Integration tier (Linux):** existing tests must continue to pass without
   modification. The Linux path is unchanged by C2.

3. **Live verification (target platform):** Bring up `containerized_oci`
   runner on a non-Linux platform (Windows + Docker Desktop is the
   recommended target — most accessible and most representative of the
   common deployment case). Run a full plugin host lifecycle. Confirm:
   - `sandbox_platform_capability_matrix()` reports
     `production_safe_third_party_plugin_execution: True`
   - `sandbox_certification_profile()` returns
     `tier_status: "certified"` at tier `container-sandbox-certified`
   - `validate_external_third_party_plugin_runtime_policy` succeeds for a
     test plugin under `distributed-api` profile
   - `_verify_post_launch_state` returns with `verification_method:
     "worker-self-report"` (kernel-observable correctly stays unavailable)
   - End-to-end plugin admission, execute, heartbeat, shutdown all succeed

4. **Negative live verification:** Same target platform but with Docker
   switched to Windows-containers mode (Windows only). Confirm:
   - matrix reports `production_safe_third_party_plugin_execution: False`
   - certification stays uncertified
   - third-party plugin admission fails closed with a clear error

---

## After This Audit

Phase 2 of the arc would be `docs/runtime/SANDBOX_CONTRACT.md` — a peer to
`EXECUTION_CONTRACT.md` and `IDEMPOTENCY_CONTRACT.md`. The C2 contract claims
this audit recommends would be encoded as numbered invariants there. The
existing partial contract spread across `EXTENSION_TRUST_MODEL.md`,
`EXTENSION_CAPABILITIES.md`, and the inline module docstrings would be
referenced from the consolidated document. This is a separate task and is
larger than C2-Container alone — it's worth doing once C2 lands so the
contract document reflects the post-C2 reality, not the pre-C2 reality.