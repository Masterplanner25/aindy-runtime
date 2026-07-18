---
title: "Sandbox Escape Audit Log"
api_version: "1.0"
last_verified: "2026-07-13"
schema_version: "2026-06-04"
status: current
owner: "platform-team"
append_only: true
---
# Sandbox Escape Audit Log

This document is **append-only**. Each new audit entry is added at the bottom of the
[Audit Log](#audit-log) section. Entries are never edited or deleted.

Its two purposes:
1. **Threat model** — documents exactly what each escape vector tests and why it matters,
   so the rationale is permanently attached to the test suite, not just the test file comments.
2. **Audit trail** — records every run of `pytest -m sandbox_escape`, which platform it ran
   on, and what the results were, so platform claims can be traced to specific evidence.

---

## Threat Model

Each entry below maps to a test file in `tests/sandbox/`. For every attack vector the entry
states: what an attacker could do if the control were absent, which Docker/kernel flag prevents
it, and what a test failure would mean for the platform's security claim.

---

### Vector 1 — Filesystem Escape

**File:** `tests/sandbox/test_filesystem_escape.py`

**Threats blocked:**

| Threat | If unblocked | Hardening control | Docker flag |
|---|---|---|---|
| Write to system paths | Plugin installs backdoor, persists data across restarts | Read-only rootfs | `--read-only` |
| Write back to plugin source | Plugin replaces its own handler.py for next-cycle code injection | Read-only bind mount | `--mount ...,readonly` |
| Broad writable surface via tmpfs | Tempfs mount inadvertently relaxes rootfs restriction | Scoped tmpfs | `--mount type=tmpfs,dst=/tmp` |

**What each test proves:**

- `test_readonly_rootfs_blocks_write` — container running `echo > /etc/forbidden.txt` exits
  non-zero (EROFS). Proves `--read-only` applies to the entire root filesystem, not just
  specific paths.

- `test_plugin_mount_read_only` — container running `open('/plugin-root/handler.py', 'w')`
  exits non-zero (EROFS). Proves the bind mount `readonly` flag is enforced at the kernel
  level, not just the Docker API level.

- `test_tmpfs_writable_rootfs_read_only` — single container verifies both: `/tmp` is writable
  (plugin gets scratch space), `/etc` is not writable (rootfs restriction is not relaxed by
  the tmpfs mount). Exit 0 = both conditions hold.

**Failure interpretation:** If any of these tests fails, a plugin can write to the host
filesystem or its own source tree. The platform's isolation-from-persistence claim is void.

---

### Vector 2 — Network Escape

**File:** `tests/sandbox/test_network_escape.py`

**Threats blocked:**

| Threat | If unblocked | Hardening control | Docker flag |
|---|---|---|---|
| Data exfiltration | Plugin sends tenant data, secrets, memory contents to external server | No outbound TCP | `--network none` |
| C2 callback | Plugin phones home to receive further instructions | No outbound UDP | `--network none` |
| Internal network scan | Plugin probes other services on the Docker network | No non-loopback interface | `--network none` |

**What each test proves:**

- `test_network_none_blocks_tcp_outbound` — `socket.connect(('8.8.8.8', 53))` raises OSError
  inside the container. Proves `--network none` removes the routing path for TCP at the
  kernel level; ENETUNREACH is returned before SYN is sent.

- `test_network_none_blocks_udp_outbound` — UDP sendto + recvfrom to 8.8.8.8:53 fails with
  OSError or timeout. Proves connectionless datagrams are also blocked (no veth pair exists).

- `test_network_none_only_loopback_interface` — `socket.if_nameindex()` returns only `lo`.
  This is kernel-observable evidence: the network namespace genuinely has no data-plane
  interface, independently of whether connect() would fail.

**Failure interpretation:** If these tests fail, a plugin can reach external hosts. The
platform cannot claim data-confidentiality or execution-isolation for extension code.

---

### Vector 3 — Process / PID Exhaustion

**File:** `tests/sandbox/test_process_escape.py`  
**Platform requirement:** Linux kernel controls (Linux host or Docker Desktop Linux containers mode)

**Threats blocked:**

| Threat | If unblocked | Hardening control | Docker flag |
|---|---|---|---|
| Fork bomb | Plugin exhausts host PID table; no new processes can start system-wide | PID cgroup ceiling | `--pids-limit N` |
| Stealth child processes | Plugin spawns hidden workers to continue after timeout | PID cgroup ceiling | `--pids-limit N` |

**What each test proves:**

- `test_pids_limit_blocks_fork_bomb` — spawns `sleep 60` in a tight loop until
  `BlockingIOError` (EAGAIN, errno 11) is raised. Exit 1 = limit was enforced (PASS).
  Exit 0 = all 30 spawns succeeded (FAIL). Confirms the cgroups v1/v2 `pids` controller
  is active and EAGAIN surfaces correctly in Python's `subprocess.Popen`.

- `test_pids_limit_visible_in_cgroup` — reads `/sys/fs/cgroup/pids/pids.max` or
  `/sys/fs/cgroup/pids.max` or falls back to checking `/proc/self/cgroup` for a pids entry.
  Kernel-observable evidence independent of the Popen behavior.

**Failure interpretation:** If these tests fail, a plugin can fork-bomb the host, causing a
denial-of-service that takes down all processes on the shared kernel, including the runtime.

---

### Vector 4 — Privilege Escalation

**File:** `tests/sandbox/test_privilege_escalation.py`  
**Platform requirement:** Linux kernel controls (Linux host or Docker Desktop Linux containers mode)

**Threats blocked:**

| Threat | If unblocked | Hardening control | Docker flag |
|---|---|---|---|
| Raw socket / network attack | Plugin crafts ICMP floods, port-scans, network sniffing | Remove CAP_NET_RAW | `--cap-drop ALL` |
| File ownership takeover | Plugin claims ownership of protected files | Remove CAP_CHOWN | `--cap-drop ALL` |
| Setuid binary escalation | Plugin executes a setuid binary to regain dropped caps | Block new privileges | `--security-opt no-new-privileges` |
| seccomp bypass via exec | Later seccomp filters cannot grant more permissions | Block new privileges | `--security-opt no-new-privileges` |

**What each test proves:**

- `test_cap_drop_all_blocks_raw_socket` — `socket.socket(AF_INET, SOCK_RAW, IPPROTO_ICMP)`
  raises `PermissionError` (EPERM). Proves CAP_NET_RAW is absent even when the container
  process runs as UID 0. The kernel denies the capability check.

- `test_cap_drop_all_blocks_chown` — `os.chown('/tmp/probe', 1, 1)` raises `PermissionError`
  (EPERM). Proves CAP_CHOWN is absent; file ownership changes are denied.

- `test_no_new_privileges_in_proc_status` — reads `/proc/self/status` and asserts
  `NoNewPrivs: 1`. This is kernel-observable (not a Docker self-report): the kernel
  records the `PR_SET_NO_NEW_PRIVS` flag in procfs. Proves the flag was acknowledged
  by the kernel, not just requested at the Docker API level.

- `test_cap_drop_and_no_new_privs_combined` — runs all three checks (NoNewPrivs, raw socket,
  chown) in a single container with both flags active simultaneously. Proves the controls
  compose correctly and neither interferes with the other's enforcement.

**Failure interpretation:** If cap_drop tests fail, a plugin can perform network attacks or
claim file ownership even as an unprivileged process. If no-new-privileges fails, a plugin
could use a setuid binary to regain root capabilities and undo the cap-drop. Together these
would reduce the sandbox boundary from "constrained non-root" to "effectively root."

---

### Vector 5 — Host Environment Leak

**File:** `tests/sandbox/test_host_env_leak.py`

**Threats blocked:**

| Threat | If unblocked | Hardening control | Docker flag |
|---|---|---|---|
| Credential theft | Plugin reads DATABASE_URL, SECRET_KEY, API keys | Minimal explicit env | `--env K=V` (allowlist only) |
| JWT forgery | Plugin uses SECRET_KEY to forge tokens for any user | Minimal explicit env | `--env K=V` |
| LLM credit exhaustion | Plugin uses OPENAI_API_KEY to run arbitrary API calls | Minimal explicit env | `--env K=V` |

**What each test proves:**

- `test_sensitive_env_vars_absent` — reads `os.environ` inside the container; asserts that
  none of `SECRET_KEY`, `DATABASE_URL`, `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `AINDY_API_KEY`,
  `PERMISSION_SECRET`, `AWS_SECRET_ACCESS_KEY`, `AWS_ACCESS_KEY_ID` are present.
  Mirrors the production `_build_child_env()` behavior: only `PYTHONIOENCODING=utf-8` is
  passed via explicit `--env`.

- `test_only_allowed_env_keys_present` — confirms `PYTHONIOENCODING` IS present (proving
  `--env` was transmitted) and no sensitive key is present. Rules out a broken mount where
  both allowed and sensitive keys are absent.

**Failure interpretation:** If sensitive keys appear in the container, a plugin has access to
production credentials. One call to an LLM API or the database URL is enough to exfiltrate
all tenant data or forge tokens for every user.

---

### Vector 6 — Allowed Path Boundary

**File:** `tests/sandbox/test_allowed_path_boundary.py`

**Threats blocked:**

| Threat | If unblocked | Hardening control | Mechanism |
|---|---|---|---|
| Host filesystem read | Plugin reads SSH keys, secrets, service account tokens | Single bind mount only | No ambient host paths mounted |
| Ambient secret access | Plugin reads /run/secrets/, Kubernetes service account tokens | Single bind mount only | No ambient host paths mounted |
| Path traversal escape | Plugin reaches outside bind mount via relative paths | OCI namespace | Bind mount isolation |

**What each test proves:**

- `test_unmounted_host_dir_inaccessible` — creates a canary file in a host temp directory
  that is NOT mounted into the container. Inside the container, searches `/host`, `/hostfs`,
  `/proc/1/root/tmp`, `/proc/1/root/etc` for the canary sentinel. If the sentinel is NOT
  found, the test PASSES. Confirms host paths outside the plugin root cannot be reached.

- `test_plugin_root_accessible` — verifies the plugin root IS accessible at `/plugin-root`.
  Positive-verification counterpart: a completely broken mount (where nothing is accessible)
  would also pass Test 1 but fail Test 2, catching a misconfigured mount.

- `test_path_traversal_stays_in_container` — reads `/plugin-root/../../../etc/passwd` and
  `/etc/passwd` and asserts they are identical (same container file). Proves that symlink/
  relative path traversal from the bind mount does not escape the container's filesystem
  namespace into the host.

**Failure interpretation:** If the canary is reachable, a plugin can read arbitrary host files
including secrets, SSH keys, Kubernetes service account tokens, and the runtime's own `.env`.
The "no ambient host paths" isolation claim is void.

---

## Audit Log

Entries are added in chronological order. Each entry records: the platform, Docker version,
image, test command, commit, and per-category results. No entry is ever edited or removed.

---

### Entry 001 — 2026-06-05

**Platform:** Windows 11 (host) + Docker Desktop 29.2.1, Linux containers mode (WSL2/Hyper-V backend)  
**Host OS:** Windows 11 Home 10.0.26200  
**Container image:** `python:3.11-alpine` (sha256:8b5bfdb1fd2d78aa94e21c4d61be52487693f54be7f1021647751ff365795703)  
**Test command:** `pytest -m sandbox_escape -v`  
**Commit:** `6b5edcd`  
**Operator:** Masterplanner25

**Results by category:**

| Category | Tests | Pass | Fail | Skip | Notes |
|---|---|---|---|---|---|
| Filesystem escape | 3 | 3 | 0 | 0 | All three controls verified |
| Network escape | 3 | 3 | 0 | 0 | TCP, UDP, kernel interface evidence all blocked |
| Process / pids | 2 | 2 | 0 | 0 | pids limit + cgroup evidence confirmed via Docker Desktop Linux VM |
| Privilege escalation | 4 | 4 | 0 | 0 | CAP_NET_RAW, CAP_CHOWN removed; NoNewPrivs=1 in /proc (kernel-observable) |
| Host env leak | 2 | 2 | 0 | 0 | No production secrets present; PYTHONIOENCODING transmitted |
| Path boundary | 3 | 3 | 0 | 0 | Canary not reachable; plugin root accessible; traversal contained |

**Summary:** 17 / 17 PASS — 0 FAIL — 0 SKIP  
**Total runtime:** 33.85 seconds  
**Artifact:** `tests/sandbox/sandbox_escape_results.json`

**Platform notes:**  
Linux kernel controls (pids limit, cap drop, no-new-privileges) verified active through
Docker Desktop's Linux VM on Windows. The Linux VM kernel enforces cgroup, capability, and
no-new-privileges semantics identically to a native Linux host. This result confirms the
container-grade sandbox claim holds on Windows with Docker Desktop Linux containers mode.

**Claim supported:** `container-grade-sandbox` tier for `ContainerizedOciSandboxRunner`
on Windows + Docker Desktop Linux containers mode.

---

### Entry 002 — 2026-07-09

**Platform:** GitHub Actions `ubuntu-latest` runner (native Linux Docker daemon) — the
`sandbox-escape-linux.yml` release gate, fired automatically on the `v1.6.2` tag  
**Host OS:** Ubuntu (GitHub-hosted `ubuntu-latest`), native Linux kernel — Docker uses a native
Linux-containers backend, not a Docker Desktop VM  
**Container image:** `python:3.11-alpine` (sha256:25976e9d34a0fab1f278cae931f34c8303d97bf0c0d7f85b6b4dcf641d7702a4)  
**Test command:** `pytest -m sandbox_escape` (via workflow; `SANDBOX_ESCAPE_IMAGE=python:3.11-alpine`)  
**Commit:** `02fc620` (release tag `v1.6.2`)  
**Operator:** CI (release gate, run 29058111003)

**Results by category:**

| Category | Tests | Pass | Fail | Skip | Notes |
|---|---|---|---|---|---|
| Filesystem escape | 3 | 3 | 0 | 0 | Read-only rootfs, read-only bind mount, scoped tmpfs all verified |
| Network escape | 3 | 3 | 0 | 0 | TCP, UDP, kernel interface evidence all blocked (`--network none`) |
| Process / pids | 2 | 2 | 0 | 0 | pids limit + cgroup evidence — ran natively on the runner kernel, **0 skips** |
| Privilege escalation | 4 | 4 | 0 | 0 | CAP_NET_RAW, CAP_CHOWN removed; NoNewPrivs=1 in /proc; combined-controls check |
| Host env leak | 2 | 2 | 0 | 0 | No production secrets present; PYTHONIOENCODING transmitted |
| Path boundary | 3 | 3 | 0 | 0 | Canary not reachable; plugin root accessible; traversal contained |

**Summary:** 17 / 17 PASS — 0 FAIL — 0 SKIP  
**Total runtime:** 4.39 seconds (sum of per-test container durations; `tested_at` 2026-07-09T23:43:52Z)  
**Artifact:** `linux-sandbox-escape-results` (`sandbox_escape_results.json`)

**Platform notes:**  
This is the **primary release gate** — it runs on every `v*` tag on a native Linux runner where
Docker uses a real Linux-containers backend, so the Linux-kernel-only controls (pids cgroup,
capability drop, no-new-privileges) execute natively and **cannot skip**. Entry 001 verified the
same 17 vectors on Windows + Docker Desktop (Linux VM backend); this entry confirms them on a
native Linux kernel for the exact commit being published to PyPI as `aindy-runtime==1.6.2`.

**Claim supported:** `container-grade-sandbox` tier for `ContainerizedOciSandboxRunner` on
native Linux, certified for the `v1.6.2` release commit.

---

### Entry 003 — 2026-07-09 (back-filled 2026-07-11)

> **Back-fill note.** This records the `v1.6.0` Linux release-gate run, which fired on
> 2026-07-09T00:25Z — *before* Entry 002 (`v1.6.2`, same day 23:43Z) — but was not
> written up at the time. Per the append-only rule it is added here at the bottom rather
> than inserted in date order; the header date is the actual run date, and the parenthetical
> is when the entry was authored. Reconstructed from the run's `linux-sandbox-escape-results`
> artifact (run 28985157820), not a re-run.

**Platform:** GitHub Actions `ubuntu-latest` runner (native Linux Docker daemon) — the
`sandbox-escape-linux.yml` release gate, fired automatically on the `v1.6.0` tag  
**Host OS:** Ubuntu (GitHub-hosted `ubuntu-latest`), native Linux kernel — Docker uses a native
Linux-containers backend, not a Docker Desktop VM  
**Container image:** `python:3.11-alpine` (sha256:25976e9d34a0fab1f278cae931f34c8303d97bf0c0d7f85b6b4dcf641d7702a4)  
**Test command:** `pytest -m sandbox_escape` (via workflow; `SANDBOX_ESCAPE_IMAGE=python:3.11-alpine`)  
**Commit:** `f148fcb` (release tag `v1.6.0`)  
**Operator:** CI (release gate, run 28985157820)

**Results by category:**

| Category | Tests | Pass | Fail | Skip | Notes |
|---|---|---|---|---|---|
| Filesystem escape | 3 | 3 | 0 | 0 | Read-only rootfs, read-only bind mount, scoped tmpfs all verified |
| Network escape | 3 | 3 | 0 | 0 | TCP, UDP, kernel interface evidence all blocked (`--network none`) |
| Process / pids | 2 | 2 | 0 | 0 | pids limit hit after 9 spawns; cgroup `pids.max=10` — ran natively, **0 skips** |
| Privilege escalation | 4 | 4 | 0 | 0 | CAP_NET_RAW, CAP_CHOWN removed; NoNewPrivs=1 in /proc; combined-controls check |
| Host env leak | 2 | 2 | 0 | 0 | No production secrets present; PYTHONIOENCODING transmitted |
| Path boundary | 3 | 3 | 0 | 0 | Canary not reachable; plugin root accessible; traversal contained |

**Summary:** 17 / 17 PASS — 0 FAIL — 0 SKIP  
**Total runtime:** 4.01 seconds (sum of per-test container durations; `tested_at` 2026-07-09T00:25:34Z)  
**Artifact:** `linux-sandbox-escape-results` (`sandbox_escape_results.json`)

**Platform notes:**  
First `v*` tag to fire the `sandbox-escape-linux.yml` gate after it was added in the v1.6.0
release PR. Native Linux runner, so the Linux-kernel-only controls (pids cgroup, capability
drop, no-new-privileges) executed natively and could not skip. Same 17 vectors, same image
digest as Entries 002 and 004.

**Claim supported:** `container-grade-sandbox` tier for `ContainerizedOciSandboxRunner` on
native Linux, certified for the `v1.6.0` release commit.

---

### Entry 004 — 2026-07-09 (back-filled 2026-07-11)

> **Back-fill note.** This records the `v1.6.1` Linux release-gate run (2026-07-09T13:21Z),
> which also predates the already-recorded Entry 002 (`v1.6.2`, 23:43Z). Added at the bottom
> per the append-only rule; reconstructed from the run's artifact (run 29020836863), not a
> re-run. See Entry 003's note for the back-fill rationale.

**Platform:** GitHub Actions `ubuntu-latest` runner (native Linux Docker daemon) — the
`sandbox-escape-linux.yml` release gate, fired automatically on the `v1.6.1` tag  
**Host OS:** Ubuntu (GitHub-hosted `ubuntu-latest`), native Linux kernel — Docker uses a native
Linux-containers backend, not a Docker Desktop VM  
**Container image:** `python:3.11-alpine` (sha256:25976e9d34a0fab1f278cae931f34c8303d97bf0c0d7f85b6b4dcf641d7702a4)  
**Test command:** `pytest -m sandbox_escape` (via workflow; `SANDBOX_ESCAPE_IMAGE=python:3.11-alpine`)  
**Commit:** `9e8b8d2` (release tag `v1.6.1`)  
**Operator:** CI (release gate, run 29020836863)

**Results by category:**

| Category | Tests | Pass | Fail | Skip | Notes |
|---|---|---|---|---|---|
| Filesystem escape | 3 | 3 | 0 | 0 | Read-only rootfs, read-only bind mount, scoped tmpfs all verified |
| Network escape | 3 | 3 | 0 | 0 | TCP, UDP, kernel interface evidence all blocked (`--network none`) |
| Process / pids | 2 | 2 | 0 | 0 | pids limit hit after 9 spawns; cgroup `pids.max=10` — ran natively, **0 skips** |
| Privilege escalation | 4 | 4 | 0 | 0 | CAP_NET_RAW, CAP_CHOWN removed; NoNewPrivs=1 in /proc; combined-controls check |
| Host env leak | 2 | 2 | 0 | 0 | No production secrets present; PYTHONIOENCODING transmitted |
| Path boundary | 3 | 3 | 0 | 0 | Canary not reachable; plugin root accessible; traversal contained |

**Summary:** 17 / 17 PASS — 0 FAIL — 0 SKIP  
**Total runtime:** 4.11 seconds (sum of per-test container durations; `tested_at` 2026-07-09T13:21:04Z)  
**Artifact:** `linux-sandbox-escape-results` (`sandbox_escape_results.json`)

**Platform notes:**  
Identical 17-vector result on the same native-Linux gate and same image digest as Entries 002
and 003, for the `v1.6.1` commit published to PyPI as `aindy-runtime==1.6.1`. With Entries 002
and 003 this completes the audit trail for all three v1.6.x releases (v1.6.0, v1.6.1, v1.6.2).

**Claim supported:** `container-grade-sandbox` tier for `ContainerizedOciSandboxRunner` on
native Linux, certified for the `v1.6.1` release commit.

---

### Entry 005 — 2026-07-13

`sandbox-escape-linux.yml` release gate, fired automatically on the `v1.7.0` tag
**Host OS:** Ubuntu (GitHub-hosted `ubuntu-latest`), native Linux kernel — Docker uses a native
Linux-containers backend, not a Docker Desktop VM
**Container image:** `python:3.11-alpine` (same image as Entries 002–004; exact digest recorded in
the run artifact)
**Test command:** `pytest -m sandbox_escape` (via workflow; `SANDBOX_ESCAPE_IMAGE=python:3.11-alpine`)
**Commit:** `e0a0ad4` (release tag `v1.7.0`)
**Operator:** CI (release gate, run 29248612153)

**Results by category:**

| Category | Tests | Pass | Fail | Skip | Notes |
|---|---|---|---|---|---|
| Filesystem escape | 3 | 3 | 0 | 0 | Read-only rootfs, read-only bind mount, scoped tmpfs all verified |
| Network escape | 3 | 3 | 0 | 0 | TCP, UDP, kernel interface evidence all blocked (`--network none`) |
| Process / pids | 2 | 2 | 0 | 0 | pids limit hit; cgroup `pids.max=10` — ran natively, **0 skips** |
| Privilege escalation | 4 | 4 | 0 | 0 | CAP_NET_RAW, CAP_CHOWN removed; NoNewPrivs=1 in /proc; combined-controls check |
| Host env leak | 2 | 2 | 0 | 0 | No production secrets present; PYTHONIOENCODING transmitted |
| Path boundary | 3 | 3 | 0 | 0 | Canary not reachable; plugin root accessible; traversal contained |

**Summary:** 17 / 17 PASS — 0 FAIL — 0 SKIP
**Artifact:** `linux-sandbox-escape-results` (`sandbox_escape_results.json`, run 29248612153) — holds
exact per-test durations and the image digest.

**Platform notes:**
Identical 17-vector result on the same native-Linux gate and image as Entries 002–004, for the
`v1.7.0` release commit published to PyPI as `aindy-runtime==1.7.0`. v1.7.0 is a large but wholly
additive/opt-in release (Durable Execution DUR-1..4, the Mediated Effect Boundary program, MCP
client+server, ECOGAP-3/5/6, RTR-4 gap (c) delegation-scoped memory) — no change to the sandbox
runner or its controls, so the container-grade posture is unchanged from v1.6.x.

**Claim supported:** `container-grade-sandbox` tier for `ContainerizedOciSandboxRunner` on
native Linux, certified for the `v1.7.0` release commit.

---

### Entry 006 — 2026-07-17

`sandbox-escape-linux.yml` release gate, fired automatically on the `v1.8.0` tag
**Host OS:** Ubuntu (GitHub-hosted `ubuntu-latest`), native Linux kernel — Docker uses a native
Linux-containers backend, not a Docker Desktop VM
**Container image:** `python:3.11-alpine` (same image as Entries 002–005; exact digest recorded in
the run artifact)
**Test command:** `pytest -m sandbox_escape` (via workflow; `SANDBOX_ESCAPE_IMAGE=python:3.11-alpine`)
**Commit:** `f326efc` (release tag `v1.8.0`)
**Operator:** CI (release gate, run 29634303846)

**Results by category:**

| Category | Tests | Pass | Fail | Skip | Notes |
|---|---|---|---|---|---|
| Filesystem escape | 3 | 3 | 0 | 0 | Read-only rootfs, read-only bind mount, scoped tmpfs all verified |
| Network escape | 3 | 3 | 0 | 0 | TCP, UDP, kernel interface evidence all blocked (`--network none`) |
| Process / pids | 2 | 2 | 0 | 0 | pids limit hit; cgroup `pids.max=10` — ran natively, **0 skips** |
| Privilege escalation | 4 | 4 | 0 | 0 | CAP_NET_RAW, CAP_CHOWN removed; NoNewPrivs=1 in /proc; combined-controls check |
| Host env leak | 2 | 2 | 0 | 0 | No production secrets present; PYTHONIOENCODING transmitted |
| Path boundary | 3 | 3 | 0 | 0 | Canary not reachable; plugin root accessible; traversal contained |

**Summary:** 17 / 17 PASS — 0 FAIL — 0 SKIP (`17 passed … in 5.90s`)
**Artifact:** `linux-sandbox-escape-results` (`sandbox_escape_results.json`, run 29634303846) — holds
exact per-test durations and the image digest.

**Platform notes:**
Identical 17-vector result on the same native-Linux gate and image as Entries 002–005, for the
`v1.8.0` release commit published to PyPI as `aindy-runtime==1.8.0`. v1.8.0 is a wholly
additive/opt-in release (FR-1 connector registration + capability-enforced outbound boundary,
FR-3 `NEXT_ACTION_DISPATCHED` dispatch-outcome contract, FR-4/DOCS-BUCKET-A-1 doc split,
`setuptools>=83.0.0` security pin, `nodus-lang` 4.1.0) — no change to the sandbox runner or its
controls, so the container-grade posture is unchanged from v1.7.x.

**Claim supported:** `container-grade-sandbox` tier for `ContainerizedOciSandboxRunner` on
native Linux, certified for the `v1.8.0` release commit.

---

### Entry 007 — 2026-07-18

`sandbox-escape-linux.yml` release gate, fired automatically on the `v1.9.0` tag
**Host OS:** Ubuntu (GitHub-hosted `ubuntu-latest`), native Linux kernel — Docker uses a native
Linux-containers backend, not a Docker Desktop VM
**Container image:** `python:3.11-alpine` (same image as Entries 002–006; exact digest recorded in
the run artifact)
**Test command:** `pytest -m sandbox_escape` (via workflow; `SANDBOX_ESCAPE_IMAGE=python:3.11-alpine`)
**Commit:** `323a1af` (release tag `v1.9.0`)
**Operator:** CI (release gate, run 29656265451)

**Results by category:**

| Category | Tests | Pass | Fail | Skip | Notes |
|---|---|---|---|---|---|
| Filesystem escape | 3 | 3 | 0 | 0 | Read-only rootfs, read-only bind mount, scoped tmpfs all verified |
| Network escape | 3 | 3 | 0 | 0 | TCP, UDP, kernel interface evidence all blocked (`--network none`) |
| Process / pids | 2 | 2 | 0 | 0 | pids limit hit; cgroup `pids.max=10` — ran natively, **0 skips** |
| Privilege escalation | 4 | 4 | 0 | 0 | CAP_NET_RAW, CAP_CHOWN removed; NoNewPrivs=1 in /proc; combined-controls check |
| Host env leak | 2 | 2 | 0 | 0 | No production secrets present; PYTHONIOENCODING transmitted |
| Path boundary | 3 | 3 | 0 | 0 | Canary not reachable; plugin root accessible; traversal contained |

**Summary:** 17 / 17 PASS — 0 FAIL — 0 SKIP (`17 passed … in 5.82s`)
**Artifact:** `linux-sandbox-escape-results` (`sandbox_escape_results.json`, run 29656265451) — holds
exact per-test durations and the image digest.

**Platform notes:**
Identical 17-vector result on the same native-Linux gate and image as Entries 002–006, for the
`v1.9.0` release commit published to PyPI as `aindy-runtime==1.9.0`. v1.9.0 is a wholly
additive/opt-in release (FR-5 native-workflow app-logic reach — `call_tool`+`capability_token`
and `sys()`+app-syscalls; NODUS-WARMPOOL-1 Option A cold-start/script-budget clock split) — no
change to the sandbox runner or its controls, so the container-grade posture is unchanged from
v1.8.x.

**Claim supported:** `container-grade-sandbox` tier for `ContainerizedOciSandboxRunner` on
native Linux, certified for the `v1.9.0` release commit.

---

*To add a new entry: run `pytest -m sandbox_escape -v`, note the summary line, and append
a new entry following the format above. Do not edit prior entries.*
