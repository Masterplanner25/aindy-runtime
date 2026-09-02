---
title: "Sandbox Escape Audit Log"
api_version: "1.0"
last_verified: "2026-08-20"
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

### Entry 008 — 2026-07-19

`sandbox-escape-linux.yml` release gate, fired automatically on the `v1.10.0` tag
**Host OS:** Ubuntu (GitHub-hosted `ubuntu-latest`), native Linux kernel — Docker uses a native
Linux-containers backend, not a Docker Desktop VM
**Container image:** `python:3.11-alpine` (same image as Entries 002–007; exact digest recorded in
the run artifact)
**Test command:** `pytest -m sandbox_escape` (via workflow; `SANDBOX_ESCAPE_IMAGE=python:3.11-alpine`)
**Commit:** `70b8244` (release tag `v1.10.0`)
**Operator:** CI (release gate, run 29704109300)

**Results by category:**

| Category | Tests | Pass | Fail | Skip | Notes |
|---|---|---|---|---|---|
| Filesystem escape | 3 | 3 | 0 | 0 | Read-only rootfs, read-only bind mount, scoped tmpfs all verified |
| Network escape | 3 | 3 | 0 | 0 | TCP, UDP, kernel interface evidence all blocked (`--network none`) |
| Process / pids | 2 | 2 | 0 | 0 | pids limit hit; cgroup `pids.max=10` — ran natively, **0 skips** |
| Privilege escalation | 4 | 4 | 0 | 0 | CAP_NET_RAW, CAP_CHOWN removed; NoNewPrivs=1 in /proc; combined-controls check |
| Host env leak | 2 | 2 | 0 | 0 | No production secrets present; PYTHONIOENCODING transmitted |
| Path boundary | 3 | 3 | 0 | 0 | Canary not reachable; plugin root accessible; traversal contained |

**Summary:** 17 / 17 PASS — 0 FAIL — 0 SKIP (`17 passed … in 5.85s`)
**Artifact:** `linux-sandbox-escape-results` (`sandbox_escape_results.json`, run 29704109300) — holds
exact per-test durations and the image digest.

**Platform notes:**
Identical 17-vector result on the same native-Linux gate and image as Entries 002–007, for the
`v1.10.0` release commit published to PyPI as `aindy-runtime==1.10.0`. v1.10.0 closes
NODUS-WARMPOOL-1 (warm worker pool Phases 1–3, opt-in behind `AINDY_NODUS_WARM_POOL`) and fixes
RT-MEMTXN-LEAK-1 (memory recall no longer pins a DB connection across the embedding API call).
**Sandbox-relevant note:** the warm pool reuses worker *processes* but each execution still runs
in a separate subprocess with the same controls — the sandbox runner and its controls are
unchanged, so the container-grade posture is unchanged from v1.9.x.

**Claim supported:** `container-grade-sandbox` tier for `ContainerizedOciSandboxRunner` on
native Linux, certified for the `v1.10.0` release commit.

---

### Entry 009 — 2026-07-19

`sandbox-escape-linux.yml` release gate, fired automatically on the `v1.10.1` tag
**Host OS:** Ubuntu (GitHub-hosted `ubuntu-latest`), native Linux kernel — Docker uses a native
Linux-containers backend, not a Docker Desktop VM
**Container image:** `python:3.11-alpine` (same image as Entries 002–008; exact digest recorded in
the run artifact)
**Test command:** `pytest -m sandbox_escape` (via workflow; `SANDBOX_ESCAPE_IMAGE=python:3.11-alpine`)
**Commit:** `6d5e367` (release tag `v1.10.1`)
**Operator:** CI (release gate, run 29707937509)

**Results by category:**

| Category | Tests | Pass | Fail | Skip | Notes |
|---|---|---|---|---|---|
| Filesystem escape | 3 | 3 | 0 | 0 | Read-only rootfs, read-only bind mount, scoped tmpfs all verified |
| Network escape | 3 | 3 | 0 | 0 | TCP, UDP, kernel interface evidence all blocked (`--network none`) |
| Process / pids | 2 | 2 | 0 | 0 | pids limit hit; cgroup `pids.max=10` — ran natively, **0 skips** |
| Privilege escalation | 4 | 4 | 0 | 0 | CAP_NET_RAW, CAP_CHOWN removed; NoNewPrivs=1 in /proc; combined-controls check |
| Host env leak | 2 | 2 | 0 | 0 | No production secrets present; PYTHONIOENCODING transmitted |
| Path boundary | 3 | 3 | 0 | 0 | Canary not reachable; plugin root accessible; traversal contained |

**Summary:** 17 / 17 PASS — 0 FAIL — 0 SKIP (`17 passed … in 4.66s`)
**Artifact:** `linux-sandbox-escape-results` (`sandbox_escape_results.json`, run 29707937509) — holds
exact per-test durations and the image digest.

**Platform notes:**
Identical 17-vector result on the same native-Linux gate and image as Entries 002–008, for the
`v1.10.1` release commit published to PyPI as `aindy-runtime==1.10.1`. v1.10.1 is a patch that
completes RT-MEMTXN-LEAK-1 (the embedding-job connection fan-out: commit to release the DB
connection before the embedding API call). DB-session hygiene only — no change to the sandbox
runner or its controls, so the container-grade posture is unchanged from v1.10.0.

**Claim supported:** `container-grade-sandbox` tier for `ContainerizedOciSandboxRunner` on
native Linux, certified for the `v1.10.1` release commit.

---

### Entry 010 — 2026-07-19

`sandbox-escape-linux.yml` release gate, fired automatically on the `v1.10.2` tag
**Host OS:** Ubuntu (GitHub-hosted `ubuntu-latest`), native Linux kernel — Docker uses a native
Linux-containers backend, not a Docker Desktop VM
**Container image:** `python:3.11-alpine` (same image as Entries 002–009; exact digest recorded in
the run artifact)
**Test command:** `pytest -m sandbox_escape` (via workflow; `SANDBOX_ESCAPE_IMAGE=python:3.11-alpine`)
**Commit:** `0ee164b` (release tag `v1.10.2`)
**Operator:** CI (release gate, run 29711399597)

**Results by category:**

| Category | Tests | Pass | Fail | Skip | Notes |
|---|---|---|---|---|---|
| Filesystem escape | 3 | 3 | 0 | 0 | Read-only rootfs, read-only bind mount, scoped tmpfs all verified |
| Network escape | 3 | 3 | 0 | 0 | TCP, UDP, kernel interface evidence all blocked (`--network none`) |
| Process / pids | 2 | 2 | 0 | 0 | pids limit hit; cgroup `pids.max=10` — ran natively, **0 skips** |
| Privilege escalation | 4 | 4 | 0 | 0 | CAP_NET_RAW, CAP_CHOWN removed; NoNewPrivs=1 in /proc; combined-controls check |
| Host env leak | 2 | 2 | 0 | 0 | No production secrets present; PYTHONIOENCODING transmitted |
| Path boundary | 3 | 3 | 0 | 0 | Canary not reachable; plugin root accessible; traversal contained |

**Summary:** 17 / 17 PASS — 0 FAIL — 0 SKIP (`17 passed, 5 warnings in 5.55s`)
**Artifact:** `linux-sandbox-escape-results` (`sandbox_escape_results.json`, run 29711399597) — holds
exact per-test durations and the image digest.

**Platform notes:**
Identical 17-vector result on the same native-Linux gate and image as Entries 002–009, for the
`v1.10.2` release commit published to PyPI as `aindy-runtime==1.10.2`. v1.10.2 is a patch that
closes RT-MEMTXN-LEAK-1 by breaking the capture → job → capture cascade (runtime-internal
memory-maintenance jobs are no longer captured as memory; async-submission nesting is
depth-bounded; global-node dedup repaired). Memory-capture and DB-session hygiene only — no
change to the sandbox runner or its controls, so the container-grade posture is unchanged from
v1.10.1.

**Claim supported:** `container-grade-sandbox` tier for `ContainerizedOciSandboxRunner` on
native Linux, certified for the `v1.10.2` release commit.

---

### Entry 011 — 2026-08-01

`sandbox-escape-linux.yml` release gate, fired automatically on the `v1.11.0` tag
**Host OS:** Ubuntu (GitHub-hosted `ubuntu-latest`), native Linux kernel — Docker uses a native
Linux-containers backend, not a Docker Desktop VM
**Container image:** `python:3.11-alpine` (same image as Entries 002–010; exact digest recorded in
the run artifact)
**Test command:** `pytest -m sandbox_escape` (via workflow; `SANDBOX_ESCAPE_IMAGE=python:3.11-alpine`)
**Commit:** `4e8f917` (release tag `v1.11.0`)
**Operator:** CI (release gate, run 30713164605)

**Results by category:**

| Category | Tests | Pass | Fail | Skip | Notes |
|---|---|---|---|---|---|
| Filesystem escape | 3 | 3 | 0 | 0 | Read-only rootfs, read-only bind mount, scoped tmpfs all verified |
| Network escape | 3 | 3 | 0 | 0 | TCP, UDP, kernel interface evidence all blocked (`--network none`) |
| Process / pids | 2 | 2 | 0 | 0 | pids limit hit; cgroup `pids.max=10` — ran natively, **0 skips** |
| Privilege escalation | 4 | 4 | 0 | 0 | CAP_NET_RAW, CAP_CHOWN removed; NoNewPrivs=1 in /proc; combined-controls check |
| Host env leak | 2 | 2 | 0 | 0 | No production secrets present; PYTHONIOENCODING transmitted |
| Path boundary | 3 | 3 | 0 | 0 | Canary not reachable; plugin root accessible; traversal contained |

**Summary:** 17 / 17 PASS — 0 FAIL — 0 SKIP (`17 passed, 5 warnings in 6.31s`)
**Artifact:** `linux-sandbox-escape-results` (`sandbox_escape_results.json`, run 30713164605) — holds
exact per-test durations and the image digest.

**Platform notes:**
Identical 17-vector result on the same native-Linux gate and image as Entries 002–010, for the
`v1.11.0` release commit published to PyPI as `aindy-runtime==1.11.0`. v1.11.0 is a minor release
(new public endpoint `POST /auth/password/change`, FR-6 item 1) that also raises the
`DB_IDLE_IN_TRANSACTION_TIMEOUT_MS` default above the nodus execution ceiling (DB-NODUS-BUDGET-1)
and caps the optional `[mcp]` extra at `mcp<2` (MCP-SDK-2X-1).

**None of those touch the sandbox runner or its controls.** The auth endpoint is an HTTP route, the
timeout change is a DB connection parameter, and the mcp cap is an optional-extra version bound —
no change to `sandbox_runner.py`, the OCI flags, or the capability set. Container-grade posture is
therefore unchanged from v1.10.2, and this entry records that the gate re-verified it rather than
assuming it.

**Claim supported:** `container-grade-sandbox` tier for `ContainerizedOciSandboxRunner` on
native Linux, certified for the `v1.11.0` release commit.

---

### Entry 012 — 2026-08-02

`sandbox-escape-linux.yml` release gate, fired automatically on the `v2.0.0` tag
**Host OS:** Ubuntu (GitHub-hosted `ubuntu-latest`), native Linux kernel — Docker uses a native
Linux-containers backend, not a Docker Desktop VM
**Container image:** `python:3.11-alpine` (same image as Entries 002–011; exact digest recorded in
the run artifact)
**Test command:** `pytest -m sandbox_escape` (via workflow; `SANDBOX_ESCAPE_IMAGE=python:3.11-alpine`)
**Commit:** `bd8f352` (release tag `v2.0.0`)
**Operator:** CI (release gate, run 30769596562)

**Results by category:**

| Category | Tests | Pass | Fail | Skip | Notes |
|---|---|---|---|---|---|
| Filesystem escape | 3 | 3 | 0 | 0 | Read-only rootfs, read-only bind mount, scoped tmpfs all verified |
| Network escape | 3 | 3 | 0 | 0 | TCP, UDP, kernel interface evidence all blocked (`--network none`) |
| Process / pids | 2 | 2 | 0 | 0 | pids limit hit; cgroup `pids.max=10` — ran natively, **0 skips** |
| Privilege escalation | 4 | 4 | 0 | 0 | CAP_NET_RAW, CAP_CHOWN removed; NoNewPrivs=1 in /proc; combined-controls check |
| Host env leak | 2 | 2 | 0 | 0 | No production secrets present; PYTHONIOENCODING transmitted |
| Path boundary | 3 | 3 | 0 | 0 | Canary not reachable; plugin root accessible; traversal contained |

**Summary:** 17 / 17 PASS — 0 FAIL — 0 SKIP (`17 passed, 4 warnings in 6.32s`)
**Artifact:** `linux-sandbox-escape-results` (`sandbox_escape_results.json`, run 30769596562) — holds
exact per-test durations and the image digest.

**Platform notes:**
Identical 17-vector result on the same native-Linux gate and image as Entries 002–011, for the
`v2.0.0` release commit published to PyPI as `aindy-runtime==2.0.0`.

**v2.0.0 is the first MAJOR release covered by this log**, so the "no change to the sandbox"
claim deserves more than the usual sentence. The release is a breaking one, but the breaks are
confined to auth and memory: registration returns 202 without a token, access tokens require a
`purpose` claim, `MIN_PASSWORD_LENGTH` applies to registration, plus the FR-6 recovery endpoints,
the FR-7 memory-capture fixes, and a fix for plaintext passwords reaching `input_payload`.

**None of that touches the sandbox boundary.** No change to `sandbox_runner.py`, the OCI flags,
the capability set, or the container image. The one addition adjacent to execution is the new
`Native Crate Build (Rust)` CI job, which compiles the optional memory scorer — it does not run
inside the sandbox and does not alter how extension code is confined. So this entry records that
the gate **re-verified** the container-grade posture across a major version boundary, rather than
the release having changed anything about it.

**Claim supported:** `container-grade-sandbox` tier for `ContainerizedOciSandboxRunner` on
native Linux, certified for the `v2.0.0` release commit.

---

### Entry 013 — 2026-08-05

`sandbox-escape-linux.yml` release gate, fired automatically on the `v2.0.1` tag
**Host OS:** Ubuntu (GitHub-hosted `ubuntu-latest`), native Linux kernel — Docker uses a native
Linux-containers backend, not a Docker Desktop VM
**Container image:** `python:3.11-alpine` (same image as Entries 002–012; exact digest recorded in
the run artifact)
**Test command:** `pytest -m sandbox_escape` (via workflow; `SANDBOX_ESCAPE_IMAGE=python:3.11-alpine`)
**Commit:** `8b149d5` (release tag `v2.0.1`)
**Operator:** CI (release gate, run 31041619455)

**Results by category:**

| Category | Tests | Pass | Fail | Skip | Notes |
|---|---|---|---|---|---|
| Filesystem escape | 3 | 3 | 0 | 0 | Read-only rootfs, read-only bind mount, scoped tmpfs all verified |
| Network escape | 3 | 3 | 0 | 0 | TCP, UDP, kernel interface evidence all blocked (`--network none`) |
| Process / pids | 2 | 2 | 0 | 0 | pids limit hit; cgroup `pids.max=10` — ran natively, **0 skips** |
| Privilege escalation | 4 | 4 | 0 | 0 | CAP_NET_RAW, CAP_CHOWN removed; NoNewPrivs=1 in /proc; combined-controls check |
| Host env leak | 2 | 2 | 0 | 0 | No production secrets present; PYTHONIOENCODING transmitted |
| Path boundary | 3 | 3 | 0 | 0 | Canary not reachable; plugin root accessible; traversal contained |

**Summary:** 17 / 17 PASS — 0 FAIL — 0 SKIP (`17 passed, 4 warnings in 6.23s`)
**Artifact:** `linux-sandbox-escape-results` (`sandbox_escape_results.json`, run 31041619455) — holds
exact per-test durations and the image digest.

**Platform notes:**
Identical 17-vector result on the same native-Linux gate and image as Entries 002–012, for the
`v2.0.1` release commit published to PyPI as `aindy-runtime==2.0.1`.

**v2.0.1 is a patch that fixes the 2.0.0 upgrade path, and none of it touches the sandbox
boundary.** The three defects are an empty-environment-variable crash loop at module import
(FR-10), a schema reconcile that failed to grandfather rows predating a new column (FR-8), and a
connector-type collision that let an app's `email` handler swallow runtime transactional mail
(FR-9). No change to `sandbox_runner.py`, the OCI flags, the capability set, or the container
image.

Two release changes are adjacent enough to name explicitly rather than leave to inference:

- **`cryptography` 49.0.0 → 50.0.0** (CVE-2026-69247, a Bleichenbacher oracle in PKCS7
  decryption). It is a dependency of the *extension-signing* path, not of the sandbox. The gate
  run confirms the shipped tree resolves `cryptography-50.0.0`, so the patched version is what
  the certified artifact carries.
- **The Platform UI toolchain major** (vite 8 / tailwind 4). Build-time only; nothing from that
  chain executes inside a sandboxed container.

So this entry records that the gate **re-verified** the container-grade posture for the patch
release, rather than the release having changed anything about it.

**Claim supported:** `container-grade-sandbox` tier for `ContainerizedOciSandboxRunner` on
native Linux, certified for the `v2.0.1` release commit.

### Entry 014 — 2026-08-15 (gate ran 2026-08-16 UTC)

`sandbox-escape-linux.yml` release gate, fired automatically on the `v2.1.0` tag
**Host OS:** Ubuntu (GitHub-hosted `ubuntu-latest`), native Linux kernel — Docker uses a native
Linux-containers backend, not a Docker Desktop VM
**Container image:** `python:3.11-alpine`. **Note:** the gate log records
`Status: Downloaded newer image for python:3.11-alpine`, so the tag resolved to a *freshly pulled*
image rather than a cached one — the exact digest is in the run artifact and is not assumed
identical to Entries 002–013.
**Test command:** `pytest -m sandbox_escape` (via workflow; `SANDBOX_ESCAPE_IMAGE=python:3.11-alpine`)
**Commit:** `ea988d1` (release tag `v2.1.0`)
**Operator:** CI (release gate, run 31918622619)

**Results by category:**

| Category | Tests | Pass | Fail | Skip | Notes |
|---|---|---|---|---|---|
| Filesystem escape | 3 | 3 | 0 | 0 | Read-only rootfs, read-only bind mount, scoped tmpfs all verified |
| Network escape | 3 | 3 | 0 | 0 | TCP, UDP, kernel interface evidence all blocked (`--network none`) |
| Process / pids | 2 | 2 | 0 | 0 | pids limit hit; cgroup `pids.max=10` — ran natively, **0 skips** |
| Privilege escalation | 4 | 4 | 0 | 0 | CAP_NET_RAW, CAP_CHOWN removed; NoNewPrivs=1 in /proc; combined-controls check |
| Host env leak | 2 | 2 | 0 | 0 | No production secrets present; PYTHONIOENCODING transmitted |
| Path boundary | 3 | 3 | 0 | 0 | Canary not reachable; plugin root accessible; traversal contained |

**Summary:** 17 / 17 PASS — 0 FAIL — 0 SKIP (`17 passed, 4 warnings in 6.15s`)
**Artifact:** `linux-sandbox-escape-results` (`sandbox_escape_results.json`, run 31918622619) —
holds exact per-test durations and the image digest.

**Platform notes:**
Seventeen-vector result on the same native-Linux gate as Entries 002–013, for the `v2.1.0`
release commit published to PyPI as `aindy-runtime==2.1.0`.

**Nothing in this release touches the certified boundary — verified, not assumed.** `git diff
v2.0.1..v2.1.0` over `sandbox_runner.py`, `plugin_host.py`, `sandbox_certification.py` and
`tests/sandbox/` is **empty**; the only Dockerfile change is the builder-stage pin moving to
`2.1.0`. No change to the OCI flags, the capability set, or the runner selection path.

Dependency movement in this release is `greenlet` 3.5.3 → 3.5.4, `alembic` 1.17.0 → 1.19.0,
`pydantic-settings` 2.14.2 → 2.15.0 and `uvicorn` 0.52.0 → 0.52.1 — none of which is a sandbox
dependency, and unlike `v2.0.1` there is no security-relevant crypto bump to name.

**★ A distinction this entry must make explicit, because 2.1.0 is the release that documented
several isolation findings.** `TECH_DEBT.md` gained `GUEST-CONFINE-1`, `TOOL-SEAM-ISOLATION-1`
and `EXEC-ENV-BIND-1` in this cycle, one of which is demonstrated. **None of them is a finding
against the boundary this gate certifies.** The gate certifies the **Tier-2 extension sandbox** —
the `ContainerizedOciSandboxRunner` path reached through `plugin_host.py`. The open findings are
that the *same provider is not bound to two other seams*: the Nodus guest VM, and the in-process
tool seam. Those seams have never been inside this gate's scope and this entry does not claim
otherwise.

Concretely, so a future reader cannot conflate them:

| Boundary | Certified here? | Status |
|---|---|---|
| Tier-2 extension sandbox (OCI runner) | **Yes** | 17/17, container-grade |
| Nodus guest VM | No — out of scope | `GUEST-CONFINE-1`, open, P0 |
| In-process tool seam | No — out of scope | `TOOL-SEAM-ISOLATION-1`, open, P0 |

So this entry records that the gate **re-verified** the container-grade posture for a minor
release that did not modify it — and, deliberately, that the release's other isolation findings
sit outside what a green gate here proves.

**Claim supported:** `container-grade-sandbox` tier for `ContainerizedOciSandboxRunner` on
native Linux, certified for the `v2.1.0` release commit.

### Entry 015 — 2026-08-16

**Trigger:** `v2.2.0` release tag (`sandbox-escape-linux.yml`, run `31935889083`).
**Commit:** `c09b01f7458d57bb78117f6c5d47257f6fa33f11`
**Platform:** GitHub `ubuntu-latest`, native Linux containers backend.
**Image:** `python:3.11-alpine` (`SANDBOX_ESCAPE_IMAGE`).
**Summary:** 17 / 17 PASS — 0 FAIL — 0 SKIP (`17 passed, 4 warnings in 6.19s`)
**Artifact:** `linux-sandbox-escape-results` (`sandbox_escape_results.json`, run `31935889083`).

**Platform notes:**
Seventeen-vector result on the same native-Linux gate as Entries 002–014, for the `v2.2.0`
release commit.

**Nothing in this release touches the certified boundary — verified, not assumed.**
`git diff v2.1.0..v2.2.0` over `sandbox_runner.py`, `plugin_host.py`, `sandbox_certification.py`
and `tests/sandbox/` is **empty**. The only Dockerfile change is the builder-stage pin moving to
`2.2.0`, and there is **no dependency movement at all** in this release — so unlike Entry 014
there is not even a transitive bump to rule out.

**★ This is the release that CLOSED `GUEST-CONFINE-1`, and that must not be read as this gate
certifying it.** Entry 014 published a scope table listing the Nodus guest VM as out of scope
with `GUEST-CONFINE-1` open at P0. That finding is now fixed — but **the fix is not the thing
this gate tests, and the guest VM has still never been inside this gate's scope.**

The distinction, stated so a future reader cannot collapse it:

| Boundary | Certified by this gate? | Status after `v2.2.0` |
|---|---|---|
| Tier-2 extension sandbox (OCI runner) | **Yes** — 17/17, container-grade | unchanged this release |
| Nodus guest VM | **No** — out of scope, still | `GUEST-CONFINE-1` **CLOSED** — by VM confinement arguments (`allow_subprocess/network/env=False`), *not* by container isolation. Covered by `tests/unit/test_guest_confinement.py`, not by this suite. |
| In-process tool seam | **No** — out of scope | `TOOL-SEAM-ISOLATION-1`, open, P0 |

So: a green gate here says the **container-grade posture of the Tier-2 extension runner is
unchanged**. It says nothing about the guest VM in either direction — it did not detect
`GUEST-CONFINE-1` when that hole was open, and it does not verify the fix now that it is closed.
Two different boundaries, two different mechanisms, two different test suites.

**Claim supported:** `container-grade-sandbox` tier for `ContainerizedOciSandboxRunner` on
native Linux, certified for the `v2.2.0` release commit.

### Entry 016 — 2026-08-16

**Trigger:** `v2.3.0` release tag (`sandbox-escape-linux.yml`, run `31968265851`).
**Commit:** `c911312fcd75950592f9af99d3173c959c619bf1`
**Platform:** GitHub `ubuntu-latest`, native Linux containers backend.
**Image:** `python:3.11-alpine` (`SANDBOX_ESCAPE_IMAGE`).
**Summary:** 17 / 17 PASS — 0 FAIL — 0 SKIP (`17 passed, 4 warnings in 6.04s`)
**Artifact:** `linux-sandbox-escape-results` (`sandbox_escape_results.json`, run `31968265851`).

**Platform notes:**
Seventeen-vector result on the same native-Linux gate as Entries 002–015, for the `v2.3.0`
release commit.

**Nothing in this release touches the certified boundary — verified, not assumed.**
`git diff v2.2.0..v2.3.0` over `sandbox_runner.py`, `plugin_host.py`,
`sandbox_certification.py` and `tests/sandbox/` is **empty**.

**★ There IS dependency movement this time, and it is the one worth naming: `nodus-lang`
4.1.0 → 4.2.0** — the only pin change in the release. It does not touch this gate's boundary
(the Tier-2 OCI runner does not embed the Nodus VM), but it *does* touch the **guest** boundary
that `GUEST-CONFINE-1` closed, because confinement is expressed as VM constructor arguments.

That was checked before the bump landed rather than inferred from a green gate here: all three
flags (`allow_subprocess`, `allow_network`, `allow_env`) are present on 4.2.0 with identical
defaults, and **all 31 gated builtins are still refused**, verified against the real VM. Had
one been renamed, the guest would run unconfined while this suite still reported 17/17 — the
two boundaries are independent, and a green gate here would not have noticed.

**The scope table, restated because it is the thing most likely to be collapsed:**

| Boundary | Certified by this gate? | Status after `v2.3.0` |
|---|---|---|
| Tier-2 extension sandbox (OCI runner) | **Yes** — 17/17 | unchanged this release |
| Nodus guest VM | **No** — out of scope | `GUEST-CONFINE-1` closed; re-verified against nodus-lang 4.2.0 by `tests/unit/test_guest_confinement.py`, **not** by this suite |
| In-process tool seam | **No** | `TOOL-SEAM-ISOLATION-1`, open, P0 |

**Claim supported:** `container-grade-sandbox` tier for `ContainerizedOciSandboxRunner` on
native Linux, certified for the `v2.3.0` release commit.

### Entry 017 — 2026-08-18

**Trigger:** `v2.4.0` release tag (`sandbox-escape-linux.yml`, run `32085467838`).
**Commit:** `d5a6bcd7af00475c9a9724b68c4bc076326bfb7e`
**Platform:** GitHub `ubuntu-latest`, native Linux containers backend.
**Image:** `python:3.11-alpine` (`SANDBOX_ESCAPE_IMAGE`).
**Summary:** 17 / 17 PASS — 0 FAIL — 0 SKIP (`17 passed, 4 warnings in 6.92s`)
**Artifact:** `linux-sandbox-escape-results` (`sandbox_escape_results.json`, run `32085467838`).

**Platform notes:**
Seventeen-vector result on the same native-Linux gate as Entries 002–016, for the `v2.4.0`
release commit.

**Nothing in this release touches the certified boundary — verified, not assumed.**
`git diff v2.3.0..v2.4.0` over `sandbox_runner.py`, `plugin_host.py`,
`sandbox_certification.py` and `tests/sandbox/` is **empty**.

**★ The dependency movement is a MAJOR one this time: `nodus-lang` 4.2.0 → 5.0.1** (with
`nodus-mcp` 0.1.2 → 0.1.3). As in Entry 016, it does not touch this gate's boundary — the Tier-2
OCI runner does not embed the Nodus VM — but it lands squarely on the **guest** boundary
`GUEST-CONFINE-1` closed, and a major release is exactly where that boundary is most likely to
move.

It did move, and in the safe direction: **nodus 5.0.0 made `NodusRuntime` deny-by-default**, so
the confinement this runtime had been applying by hand since `GUEST-CONFINE-1` is now also the
library default. Verified against the real VM rather than inferred from the release notes: **all
31 gated builtins are still refused** (7 subprocess / 18 network / 6 env — unchanged from the
count recorded in Entry 016), and the three constructor flags are still accepted and still
keyword-only.

**★ What a green gate here would NOT have caught, and nearly hid:** four confinement tests went
red on the bump and **none was a regression** — 5.0.0 rephrased its denial messages, and the
test that enumerates gated builtins broke twice on registry restructuring. This suite reported
17/17 throughout. The two boundaries are independent; had the guest actually been unconfined,
this gate would still have said 17/17.

**The scope table, restated because it is the thing most likely to be collapsed:**

| Boundary | Certified by this gate? | Status after `v2.4.0` |
|---|---|---|
| Tier-2 extension sandbox (OCI runner) | **Yes** — 17/17 | unchanged this release |
| Nodus guest VM | **No** — out of scope | `GUEST-CONFINE-1` closed; re-verified against nodus-lang **5.0.1** by `tests/unit/test_guest_confinement.py` and `test_nodus_upgrade_contract.py`, **not** by this suite |
| In-process tool seam | **No** | `TOOL-SEAM-ISOLATION-1`, open, P0 |

**Also worth recording, though outside this gate's boundary:** `v2.4.0` is an authorization
release — scope enforcement went from 29 of 126 registered routes to 91, closing two
demonstrated API-key escalations (`KEY-SCOPE-ESCALATION-1`). Those are HTTP-surface boundaries,
certified by neither this suite nor any sandbox tier, and are recorded here only so a reader of
this log does not infer from "17/17" that the release's security work was covered by it.

**Claim supported:** `container-grade-sandbox` tier for `ContainerizedOciSandboxRunner` on
native Linux, certified for the `v2.4.0` release commit.

---

## Entry 018 — 2026-08-19

**Trigger:** `v2.4.1` release tag (`sandbox-escape-linux.yml`, run `32282549887`).
**Commit:** `d6c64d9a6f4b7525f05b0a10af809f80b5066bf1`
**Platform:** GitHub `ubuntu-latest`, native Linux containers backend.
**Image:** `python:3.11-alpine` (`SANDBOX_ESCAPE_IMAGE`), digest
`sha256:6857d2dae63e052057f2db389a7061188ac9a92a3fa8d402bde68f36df6fada1`.
**Summary:** 17 / 17 PASS — 0 FAIL — 0 SKIP (`17 passed, 4 warnings in 6.81s`)
**Artifact:** `linux-sandbox-escape-results` (`sandbox_escape_results.json`, run `32282549887`).
Six attack vectors: `env_leak`, `filesystem_escape`, `network_escape`, `path_boundary`,
`privilege_escalation`, `process_escape`.

**This entry is short because the release is small, and that is the finding.**
`git diff v2.4.0..v2.4.1` over `sandbox_runner.py`, `plugin_host.py`, `sandbox_certification.py`
and `tests/sandbox/` is **empty**. Twenty files changed in total; the only non-doc source change
is a docstring correction in `nodus_worker_pool.py`.

**★ But read Entry 017's warning again before reading this 17/17 as reassurance.** `v2.4.1`
exists *because* of a security fix — `nodus-lang` 5.0.1 → 5.0.4, closing a cross-runtime guest
memory disclosure — **and this gate would have reported 17/17 either way.** Entry 017 said the
same thing prospectively about the 4.2.0 → 5.0.1 major: the two boundaries are independent, and
the Tier-2 OCI runner does not embed the Nodus VM. One release later that stopped being a
hypothetical. The guest boundary was actually wrong on the pin `v2.4.0` shipped, and every green
check in this log was green throughout.

The guest-side verification is `tests/unit/test_nodus_upgrade_contract.py::test_two_runtimes_in_one_process_do_not_share_guest_memory`
(mutation-tested 2/11 against 5.0.1) and `tests/unit/test_guest_confinement.py` — **not this
suite**, which is why the scope table below is restated rather than assumed.

| Boundary | Certified by this gate? | Status after `v2.4.1` |
|---|---|---|
| Tier-2 extension sandbox (OCI runner) | **Yes** — 17/17 | unchanged this release |
| Nodus guest VM | **No** — out of scope | `GUEST-CONFINE-1` closed; guest memory isolation newly pinned against nodus-lang **5.0.4**. Residual open: nothing sets the guest's `cwd`, so `allowed_paths` still defaults to an inherited directory |
| In-process tool seam | **No** | `TOOL-SEAM-ISOLATION-1`, open, P0 — unchanged |

**Claim supported:** `container-grade-sandbox` tier for `ContainerizedOciSandboxRunner` on
native Linux, certified for the `v2.4.1` release commit.

---

## Entry 019 — 2026-08-20

**Trigger:** `v2.5.0` release tag (`sandbox-escape-linux.yml`, run `32336533588`).
**Commit:** `89d5fcd7ff45a5be13884baa7f9d06b7f053a9db`
**Platform:** GitHub `ubuntu-latest`, native Linux containers backend.
**Image:** `python:3.11-alpine` (`SANDBOX_ESCAPE_IMAGE`), digest
`sha256:6857d2dae63e052057f2db389a7061188ac9a92a3fa8d402bde68f36df6fada1`.
**Summary:** 17 / 17 PASS — 0 FAIL — 0 SKIP (`17 passed, 4 warnings in 6.02s`)
**Artifact:** `linux-sandbox-escape-results` (`sandbox_escape_results.json`, run `32336533588`).
Six attack vectors: `env_leak`, `filesystem_escape`, `network_escape`, `path_boundary`,
`privilege_escalation`, `process_escape`.

**The certified boundary is untouched.** `git diff v2.4.1..v2.5.0` over `sandbox_runner.py`,
`plugin_host.py`, `sandbox_certification.py` and `tests/sandbox/` is **empty**.

**★ And yet this is the release where isolation changed the most — which is exactly the point
this log has been making since Entry 017.** `v2.5.0` shipped `TOOL-SEAM-ISOLATION-1` end to end
(a declared tool now runs in a worker subprocess) and `EXEC-ENV-BIND-1` phases 1–2 (an execution
unit declares the environment it needs; the Nodus guest asks for one). **This suite covers none of
it, and would have reported 17/17 either way.**

Entry 017 raised that prospectively about a dependency bump; Entry 018 recorded it after the fact
when `v2.4.1` fixed a guest-boundary bug this gate could not see. **Entry 019 is the third
consecutive release where the number is honest and uninformative about the release's actual
security work.** Read the scope table, not the number.

| Boundary | Certified by this gate? | Status after `v2.5.0` |
|---|---|---|
| Tier-2 extension sandbox (OCI runner) | **Yes** — 17/17 | unchanged this release |
| Nodus guest VM | **No** — out of scope | `GUEST-CONFINE-1` **fully closed**: `allowed_paths` is now an explicit per-execution scratch root rather than the server's inherited cwd, and `NODUS_ALLOWED_PATHS` is inert. Verified by `tests/unit/test_guest_environment_binding.py`, **not** by this suite |
| In-process tool seam | **No** — out of scope | `TOOL-SEAM-ISOLATION-1` **A–C2 shipped**: a tool declaring an isolation class runs out of process with no fallback. Verified by `tests/unit/test_tool_isolation_enforcement.py`, **not** by this suite. **Undeclared tools still run in-process** — the deliberate remaining gap |

**★ A note for whoever certifies the next release.** Two of the three rows above moved this cycle
while this gate's number did not change at all. If that keeps happening, the honest conclusion is
not that the gate is weak — it certifies exactly what it claims — but that **the audit log's value
is now mostly in the table rather than the count**, and a reader who scans only the summary line
is getting less information each release.

**Claim supported:** `container-grade-sandbox` tier for `ContainerizedOciSandboxRunner` on
native Linux, certified for the `v2.5.0` release commit.

---

## Entry 020 — 2026-08-22

**Trigger:** `v2.6.0` release tag (`sandbox-escape-linux.yml`, run `32613267546`).
**Commit:** `71391fa578c52a3e7e5e425d493019c067e2b6ef`
**Platform:** GitHub `ubuntu-latest`, native Linux containers backend.
**Image:** `python:3.11-alpine` (`SANDBOX_ESCAPE_IMAGE`), digest
`sha256:6857d2dae63e052057f2db389a7061188ac9a92a3fa8d402bde68f36df6fada1`.
**Summary:** 17 / 17 PASS — 0 FAIL — 0 SKIP (`17 passed, 4 warnings in 6.48s`)
**Artifact:** `linux-sandbox-escape-results` (`sandbox_escape_results.json`, run `32613267546`).
Six attack vectors: `env_leak`, `filesystem_escape`, `network_escape`, `path_boundary`,
`privilege_escalation`, `process_escape`.

**The certified boundary is untouched.** `git diff v2.5.0..v2.6.0` over `sandbox_runner.py`,
`plugin_host.py`, `sandbox_certification.py` and `tests/sandbox/` is **empty**. Same image digest
as Entry 019, so the environment is identical too.

**★ This time the number being uninformative is the correct outcome, not a warning.** Entries
017–019 each flagged that the count stayed at 17/17 while the release's real isolation work
happened outside this gate's scope. `v2.6.0` is different in kind: it is six app-team feature
requests — an observability payload, an execution-contract gate, a response header, two operator
panels, a published route inventory — and **none of them touch an isolation boundary at all**.
The gate is reporting "nothing changed here" about a release where nothing here changed.

Worth stating plainly because the run of three made the opposite reading tempting: a flat number
is evidence when the diff is empty *and* the release did no isolation work. It is only
uninformative when those two come apart, which is what Entries 017–019 recorded.

| Boundary | Certified by this gate? | Status after `v2.6.0` |
|---|---|---|
| Tier-2 extension sandbox (OCI runner) | **Yes** — 17/17 | unchanged this release |
| Nodus guest VM | **No** — out of scope | unchanged this release (`GUEST-CONFINE-1` remains fully closed as of `v2.5.0`) |
| In-process tool seam | **No** — out of scope | unchanged this release. **Undeclared tools still run in-process** — the deliberate remaining gap, carried forward from Entry 019 |

**★ One thing this release DID add that no boundary row covers: `AINDY/route_inventory.json`
publishes the runtime's full HTTP surface, including its 43 `/platform/*` operator routes.** That
is an information-disclosure surface by construction — it ships inside the wheel, so anyone who
can install the package can enumerate the admin route paths. Deliberate, and not a weakening:
those paths were already discoverable from `/openapi.json` on any running instance, and every one
of them is scope-gated at request time (`HTTP-SCOPE-GAP-1`'s census: 91 scope-gated, 12 admin,
21 public of 126). Recorded here so the next auditor does not rediscover it as a finding.

**Claim supported:** `container-grade-sandbox` tier for `ContainerizedOciSandboxRunner` on
native Linux, certified for the `v2.6.0` release commit.

---

## Entry 021 — 2026-09-02

**Trigger:** `v2.7.0` release tag (`sandbox-escape-linux.yml`, run `33648471784`).
**Commit:** `f02f40c1292ded1664416f74166ce4f530c2a25a`
**Platform:** GitHub `ubuntu-latest`, native Linux containers backend.
**Image:** `python:3.11-alpine` (`SANDBOX_ESCAPE_IMAGE`), digest
`sha256:0d55920083f1ce1e38ac292e2772f924b4f8bb4188d336c79bf66963039e6146`.
**Summary:** 17 / 17 PASS — 0 FAIL — 0 SKIP (`17 passed, 4 warnings in 6.21s`)
**Artifact:** `linux-sandbox-escape-results` (`sandbox_escape_results.json`, run `33648471784`).
Six attack vectors: `env_leak`, `filesystem_escape`, `network_escape`, `path_boundary`,
`privilege_escalation`, `process_escape`.

**The certified boundary is untouched.** `git diff v2.6.0..v2.7.0` over `sandbox_runner.py`,
`plugin_host.py`, `sandbox_certification.py` and `tests/sandbox/` is **empty**.

**★ Unlike Entries 019 and 020, the environment is NOT identical — the image digest moved.**
Those entries could discount a repeated 17/17 partly on the grounds that the base image was
byte-for-byte the same, so the run was closer to a re-execution than a re-test.
`python:3.11-alpine` has been rebuilt since (`6857d2da…` → `0d559200…`), so this is the *first*
pass in the run of four that holds across a changed Alpine base. That is a slightly stronger
claim than its predecessors, not a weaker one, and worth recording because the natural reading
of an unchanged number is the opposite.

**★★ The Nodus guest VM row changed this release, and this gate cannot see it.** Entries 019 and
020 both recorded that row as *unchanged*. It is not unchanged in `v2.7.0`: `nodus-lang` moved
5.1.0 → 5.9.0, and three of the eight releases in that span fix security issues **inside the
guest boundary** — a capability policy bypassable by writing `agent_call_async` instead of
`agent_call` (the async spelling carried no capability at all, so a `DenyList` refused one and
permitted the other), a relocated workflow store falling outside `DEFAULT_FLOOR`, and a graph
response that could return another request's graph state including step return values.

None of that is certified here, because this gate certifies the **Tier-2 OCI extension sandbox**
and the guest VM is a different boundary. The point of writing it down is that a reader comparing
Entry 021 against 020 sees the same 17/17 and the same six vectors, and could reasonably conclude
nothing about isolation moved in this release. Something did — **upstream, in a dependency, on a
boundary this suite does not test.** Verification for it is in that release's changelog entry.

| Boundary | Certified by this gate? | Status after `v2.7.0` |
|---|---|---|
| Tier-2 extension sandbox (OCI runner) | **Yes** — 17/17 | unchanged this release |
| Nodus guest VM | **No** — out of scope | **CHANGED** — three upstream security fixes via `nodus-lang` 5.9.0; `GUEST-CONFINE-1` remains closed |
| In-process tool seam | **No** — out of scope | unchanged this release. **Undeclared tools still run in-process** — the deliberate remaining gap, carried forward from Entries 019–020 |

**★ One release change that no boundary row covers, recorded so it is not rediscovered as a
finding:** the scheduler now dispatches drained work to a shared thread pool by default on
thread-mode deployments (`FR-15` (a)). That moves *where* runtime-owned work executes; it does
not move a trust boundary — the pool runs first-party runtime code under the same process
identity the heartbeat tick already had, and no guest or extension code reaches it. It has no
effect at all on the production overlay, where `EXECUTION_MODE=distributed` and the gate refuses.

**Claim supported:** `container-grade-sandbox` tier for `ContainerizedOciSandboxRunner` on
native Linux, certified for the `v2.7.0` release commit.

---
---

*To add a new entry: run `pytest -m sandbox_escape -v`, note the summary line, and append
a new entry following the format above. Do not edit prior entries.*
