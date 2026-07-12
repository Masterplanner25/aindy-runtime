---
title: "C3 — Non-Linux Strong-Sandbox VM Runner: Preparation Plan"
api_version: "1.0"
last_verified: "2026-07-12"
status: current
owner: "platform-team"
---

# C3 — Non-Linux Strong-Sandbox VM Runner: Preparation Plan

> **This is a preparation document, not scheduled work.** Nothing here is being built now.
> It exists so that either track (Windows-native or macOS) can start on day 1 the moment a
> trigger lands, without re-deriving the architecture. Tracking entry: `TECH_DEBT.md` §C3.

## 1. Where C3 actually stands

All *scoped* C3 phase work (0–5) is complete and CI-gated — the 17-test adversarial escape
suite passes on real Docker, WSL2/macOS backend detection ships, the threat model + posture
function + release gate + macOS CI cert workflow are all live. See `TECH_DEBT.md` §C3 and
`docs/runtime/SANDBOX_ESCAPE_AUDIT.md`.

The **one** remaining gap is a deliberately-deferred capability: a **native strong-sandbox
VM runner on a non-Linux host**. Today:

```python
# AINDY/platform_layer/sandbox_runner.py
STRONG_SANDBOX_SUPPORTED_HOST_PLATFORMS      = (PLATFORM_LINUX,)
HOSTILE_THIRD_PARTY_SUPPORTED_HOST_PLATFORMS = (PLATFORM_LINUX,)
```

Non-Linux hosts reach **`container-sandbox-certified`** (C2 — closed) but not
**`strong-sandbox-certified`**. That is the entire delta this plan addresses.

### Non-goals

- Not flipping `nodus_vm` or any default — orthogonal.
- Not changing the Linux strong-sandbox path — it is closed and certified.
- Not a container-grade improvement — container-grade already works cross-platform (C2).
  This is specifically the **dedicated-VM, hostile-third-party** tier.

## 2. The seam is already built — this is mostly a launcher + a gate widening

The runtime already has a complete, platform-agnostic strong-VM runner:
`StrongSandboxVmRunner` (`sandbox_runner.py:1689`), a `_JsonRpcProcessRunner` subclass with
`execution_boundary = "vm-stdio-json-rpc"`. It spawns an external launcher binary and speaks
stdio JSON-RPC to the extension worker inside the VM. **The runtime side does not care what
the VM technology is** — it only cares that the launcher honors the contract below.

### 2.1 The launcher contract (`aindy-sandbox-vm`) — platform-agnostic

The runner resolves `settings.AINDY_PLUGIN_STRONG_SANDBOX_LAUNCHER` (default
`aindy-sandbox-vm`) on `PATH` and invokes it with an argv the runtime constructs. The
launcher MUST:

| Runtime-supplied flag | Launcher obligation |
|---|---|
| `--mount-readonly` | Mount the plugin root read-only inside the VM at `plugin_mount_path` (`/plugin-root`) |
| `--network-deny-default` | VM has no egress by default (deny-default) |
| `--deny-host-paths` | No host filesystem is reachable from the guest |
| `--memory` / `--cpus` / `--pids-limit` | Enforce as **hard** VM-level limits |
| image reference (pinned digest) | Boot the pinned sandbox runtime image |
| then `python -m AINDY.platform_layer.extension_worker --host` | Run the worker inside the guest, wired to the launcher's stdio |

Transport: **stdio JSON-RPC** between the runtime (host) and the extension worker (guest).
The launcher bridges host stdin/stdout ⇄ guest process. Attestation flags the runtime
inspects post-launch: `--mount-readonly`, `--network-deny-default`, `--deny-host-paths`
(see `_build_launch_attestation`, `sandbox_runner.py:1833`).

All the trust/identity plumbing already exists as settings and needs **no new code**:
`AINDY_PLUGIN_STRONG_SANDBOX_IMAGE` / `_IMAGE_DIGEST` / `_RUNTIME_SOURCE` /
`_RUNTIME_TRUST_ISSUER` / `_RUNTIME_SIGNING_STATUS` / `_TRUSTED_SOURCES` / `_TRUSTED_ISSUERS`
/ `_REQUIRE_SIGNATURE_VERIFICATION` / `_MEMORY_LIMIT` / `_CPU_LIMIT` / `_PIDS_LIMIT` /
`_PLUGIN_MOUNT_PATH` / `_WORKDIR`.

### 2.2 The runtime-side code delta (small, once a launcher exists)

The only runtime changes to *admit* a platform are:

1. **Widen two constants** (`sandbox_runner.py:47-48`) to add `PLATFORM_WINDOWS` and/or
   `PLATFORM_MACOS`.
2. **Widen the `supported` gate** in `StrongSandboxVmRunner.metadata()`
   (`sandbox_runner.py:1734`): `supported = platform_name == PLATFORM_LINUX and
   launcher_available` → include the new platform, and adjust the corresponding
   `unsupported_reasons` branch (`:1736`).
3. **Assurance ceiling** (`sandbox_runner_assurance_posture`, `:89`): today
   `kernel-observable-verified` is Linux-only; extend once the platform's escape suite proves
   kernel-observable evidence, else it correctly stays `worker-self-report-verified`.
4. **Escape-suite kernel gates**: the Linux-only kernel-evidence tests
   (`/proc/self/status` NoNewPrivs, cgroup `pids.max`) need platform equivalents or explicit
   skips with a documented reason (mirror the existing WSL2 skip logic).

**The hard part is not the runtime code — it is producing a launcher that genuinely delivers
VM-boundary isolation on the target OS, and re-certifying against the escape suite.**

## 3. Track A — Windows-native

**Trigger:** a Windows host must run hostile third-party plugins *without* a Linux container
backend (no Docker Desktop/WSL2 Linux mode available or acceptable).

**Candidate isolation technologies** (pick one at trigger time; each is a distinct launcher):

| Option | Boundary | Notes |
|---|---|---|
| **Hyper-V isolated containers** (`docker run --isolation=hyperv` / Windows Sandbox) | Per-container hypervisor VM | Closest to a drop-in; needs Windows Pro/Enterprise + Hyper-V; Windows *container* images, not Linux |
| **WSL2-mediated launcher** | Lightweight utility VM (WSL2) | Reuses the Linux strong-sandbox image; launcher bridges Win host ⇄ WSL2 guest. Blurs "Windows-native" (it *is* a Linux guest) — but is the lowest-effort real VM boundary. See the existing "`aindy-sandbox-vm` binary that bridges to WSL2" note in §C3. |
| **Dedicated Hyper-V VM via HCS** (Host Compute Service API) | Full VM | Most control, most work; bespoke guest agent |

**Recommendation at trigger:** start with the **WSL2-mediated launcher** — it reuses the
already-certified Linux guest image and only requires a Windows host-side bridge binary. Treat
Hyper-V-isolated Windows containers as the "no Linux guest allowed" fallback.

**Launcher work:** build `aindy-sandbox-vm.exe` implementing §2.1. For the WSL2 route this is a
thin bridge (`wsl.exe -d <distro> …` + stdio plumbing + readonly bind + `--network none` inside
the distro). For the Hyper-V route it is a real guest-agent + HCS orchestration effort.

**Certification note:** the escape suite's kernel-evidence tests assume a Linux `/proc` and
cgroup layout. The WSL2 route satisfies them (Linux guest); a native-Windows-container route
needs Windows-equivalent evidence probes or documented skips.

## 4. Track B — macOS

**Trigger:** a macOS host must run hostile third-party plugins at strong-sandbox tier.

**Isolation technology:** **Apple Virtualization.framework** (`Virtualization.framework`,
Apple-silicon and Intel). This is the sanctioned macOS path — a real, fast, per-invocation
Linux guest VM.

| Option | Boundary | Notes |
|---|---|---|
| **Virtualization.framework Linux VM** (direct) | Full hypervisor VM | Boots the certified Linux sandbox image as a guest; launcher is a small Swift/Obj-C host binary + guest agent. The right long-term answer. |
| **`vfkit` / Krunkit / Lima / Colima** wrapper | VF-backed utility VM | Faster to stand up; wrap an existing VF-based VM tool. Colima already used in the Phase-5 macOS CI cert workflow — reuse that muscle memory. |

**Recommendation at trigger:** prototype with a **VF-backed wrapper** (`vfkit`/Lima) to reach
a working boundary fast, then decide whether a direct `Virtualization.framework` launcher is
warranted for perf/attestation control.

**Launcher work:** build `aindy-sandbox-vm` (Mach-O) implementing §2.1 against a VF Linux
guest. Because the guest is Linux, the escape suite's kernel-evidence probes apply directly —
same certification path as Linux, run on macOS hardware (the Phase-5 `macos-sandbox.yml`
workflow is the certification harness; extend it from container-grade to strong-VM).

**Constraint:** VF requires the `com.apple.security.virtualization` entitlement and a signed
binary — fold into `AGENT-HARDEN-10` Ed25519/signing posture and the notarization step.

## 5. Exit criteria — what "closes C3 fully" for a platform

Per `TECH_DEBT.md` §C3 close condition, the platform must reach:

1. A working `aindy-sandbox-vm` launcher on that host satisfying §2.1 (VM boundary, readonly
   mount, deny-default network, host-path denial, hard resource limits).
2. The two constants + `supported` gate widened to admit the platform (§2.2).
3. **Escape suite green on that host** at strong-VM tier (`pytest -m sandbox_escape`), with
   an appended `SANDBOX_ESCAPE_AUDIT.md` entry and updated `sandbox_escape_results.json`.
4. `sandbox_certification_profile` returns `tier_status: certified` at
   `strong-sandbox-certified` (worker policy certification suite), with the four launch
   attestation fields verified (backend identity, runtime identity, mount mode, resource
   limit mode).
5. Assurance ceiling reaches `kernel-observable-verified` on that host (§2.2 item 3), or the
   gap is explicitly documented if it stays self-report.

Only when (1)–(5) hold for a given platform does `HOSTILE_THIRD_PARTY_SUPPORTED_HOST_PLATFORMS`
gain that platform and C3 closes for it. C3 closes *fully* when at least one non-Linux host is
certified (the two tracks are independent — either one is a valid partial close).

## 6. Rough effort & risk (for triage when a trigger lands)

| Track / route | Effort | Primary risk |
|---|---|---|
| Windows · WSL2-mediated | **M** | "Windows-native" is really a Linux guest; ops must accept WSL2 as a dependency |
| Windows · Hyper-V isolated | **L** | Windows container images ≠ Linux; bespoke guest agent + evidence probes |
| macOS · VF wrapper (vfkit/Lima) | **M** | Third-party VM tool as a trust dependency; entitlement/signing |
| macOS · VF direct | **L** | Swift/Obj-C host binary + guest agent; notarization |

Runtime-side glue in all cases is **S** (constants + gate + escape-suite skips) — the cost is
the launcher and its certification, not the AINDY code.

## 7. Cross-references

- Runner + gate + attestation: `AINDY/platform_layer/sandbox_runner.py`
  (`StrongSandboxVmRunner` `:1689`, constants `:47`, `supported` gate `:1734`,
  `sandbox_runner_assurance_posture` `:89`, `_build_launch_attestation` `:1833`).
- Escape suite: `tests/sandbox/` (marker `sandbox_escape`), artifact
  `tests/sandbox/sandbox_escape_results.json`.
- Posture function: `sandbox_escape_test_posture()` in `sandbox_runner.py`.
- Audit log: `docs/runtime/SANDBOX_ESCAPE_AUDIT.md` (append-only).
- macOS policy + CI cert: `docs/runtime/MACOS_CONTAINER_POLICY.md`,
  `.github/workflows/macos-sandbox.yml`.
- WSL2/macOS backend detection: `sandbox_runner.py` `_detect_wsl2()`.
- Tracking: `TECH_DEBT.md` §C3.
