---
title: "Sandbox Contract"
last_verified: "2026-09-16"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Sandbox Contract

## Purpose and Scope

This document is the single canonical reference for **what the runtime guarantees about code it
runs on someone else's behalf** — where the process boundary is, what crosses it, what refuses
to start, and how much of each claim is verified rather than asserted. It is a peer to
`EXECUTION_CONTRACT.md` (how work is structured and persisted) and `IDEMPOTENCY_CONTRACT.md`
(how effects are prevented from happening twice); this one answers *how far can that work reach*.

It was recommended by the C2 audit on 2026-05-24 (`docs/archive/C2_SANDBOX_AUDIT.md`, *After
This Audit*) and written 2026-09-13. Until then these guarantees were prose spread across
`EXTENSION_TRUST_MODEL.md`, `SECURITY_MATRIX.md`, `OS_ISOLATION_LAYER.md` and the threat model in
`SANDBOX_ESCAPE_AUDIT.md`. Those documents remain; this one numbers the invariants and names the
enforcement point and the pin for each. **Every invariant below was checked against source on
the `last_verified` date. Where the honest answer is "asserted, not enforced", the invariant
says so — see §7.**

Scope: the three seams where the runtime executes code it did not author in its own process
(§2). Tenant isolation, quotas and scheduling are `OS_ISOLATION_LAYER.md`; capability and
scope enforcement on the syscall path is `SECURITY_MATRIX.md`. The assurance *vocabulary* —
assurance class, attestation, verification method, assurance ceiling, certification tier — is
defined once in `EXTENSION_TRUST_MODEL.md` §Assurance Reporting and used here without
re-definition.

---

## 1. The Architectural Decision

The runtime operates a **two-tier isolation contract**, settled 2026-05-23
(`docs/archive/ISOLATION_MODEL_PLAN.md`) and published as exactly two execution-model classes in
`AINDY/platform_layer/extension_execution_model.py`:

| Class | Meaning | Boundary |
|---|---|---|
| `kernel-resident` | Runs in the main interpreter, unsandboxed | **None.** Trusted because the operator deployed it, not because it is confined |
| `isolated-externalized` | Runs behind a runtime-owned worker, plugin host, or network contract | A process (or container, or VM) the runtime spawned and can kill |

There is deliberately **no third class**. A "capability-confined in-process" tier was removed
because in-process confinement of Python is not a boundary the runtime can defend; the
registration-time capability checks that Tier 1 code passes are *gates on what gets registered*,
not confinement of what runs. `test_runtime_public_contract.py` pins the count at two.

**Tier 1** (`runtime-built-in`, `first-party-app` bootstrap and kernel callables) is
kernel-resident and outside every guarantee in this document. **Tier 2**
(`external-third-party`, plus every surface that *chooses* externalization) is what §3 governs.

---

## 2. The Three Seams

The runtime spawns foreign code from exactly three places. Each has its own floor, its own
enforcement point and its own tests; they share the `ExecutionEnvironmentSpec` vocabulary
(`AINDY/core/execution_environment.py`) but not a runner.

| Seam | What runs | Spawned by | Runner | Floor |
|---|---|---|---|---|
| **Plugin host** | Dynamic plugin nodes (`first-party-app`, `external-third-party`) | `node_registry._make_isolated_plugin_node` → `plugin_host.start_plugin_host` | `create_sandbox_runner()` — the **only** execution call sites are `plugin_host.py:346` and `:816` | Per runner (§4) |
| **Guest VM** | `.nd` / `.nodus` scripts | `nodus_worker.py` (fresh subprocess or warm pool) | `NodusRuntime` with derived deny flags | `GUEST_FLOOR` — scoped filesystem, no env, no network, no subprocess |
| **Tool worker** | A registered tool that declared `isolation=` | `tool_registry.execute_tool` → `tool_worker.py` | One-shot subprocess, stdin/stdout JSON | `TOOL_FLOOR` — **permissive** (inherits the server env unless the tool narrows it; §7.2) |

Two surfaces that *look* like seams are not: **webhook nodes / subscriptions** are
`isolated-externalized` by *network contract* (the runtime makes an outbound HTTP call; no code
runs), and **runtime-callback workers** (`runtime_callback_host.py`) externalize
first-party callbacks for crash containment, not for trust — see `CLAUDE.md`
§`_maybe_wrap_runtime_callback`.

---

## 3. Required Invariants

Each invariant names where it is enforced and what pins it. "Pinned by" is a test that goes
red if the invariant is broken, per the repo's rule that a job name is not evidence.

### Boundary

1. **`external-third-party` Python never executes in the main interpreter.** Bootstrap Python
   for that owner class is `supported: False` in the execution-model contract; plugin nodes for
   it are built only through `_make_isolated_plugin_node`, never `_load_plugin_node`.
   *Enforced:* `extension_execution_model.py` (surface `manifest-bootstrap:external-third-party`),
   `node_registry.py:520`. *Pinned by:* `test_runtime_public_contract.py`,
   `test_plugin_host.py`.

2. **`external-third-party` plugin code is admitted only as a hashed artifact, never as a
   loose source-tree handler.** `admit_plugin_artifact()` derives `integrity_hash`,
   `extension_id`, `version` and `publisher`; a registration without `artifact_path` is refused.
   *Enforced:* `node_registry.py:520-530`. *Pinned by:* `test_plugin_host.py`.

3. **`first-party-app` plugin nodes are externalized too.** This is stricter than the trust model
   requires — first-party code *may* run kernel-resident — and it is the runtime's choice for
   the dynamic-plugin-node surface specifically: `dynamic-plugin-node:first-party-app` is
   `isolated-externalized` while `manifest-bootstrap:first-party-app` and
   `registry-kernel-callable:first-party-app` stay `kernel-resident`.
   *Enforced:* `node_registry.py:557`. *Pinned by:* `test_runtime_public_contract.py`.

4. **The guest VM is constructed with all three denials and an explicit filesystem bound.**
   `allow_subprocess=False`, `allow_network=False`, `allow_env=False`, and `allowed_paths` set
   to a **per-execution scratch root** — never `os.getcwd()`, which nodus would otherwise
   default to and which is `/home/aindy` (holding `alembic/`) in Docker. Passing it explicitly
   also makes `NODUS_ALLOWED_PATHS` inert, so an operator cannot widen the bound out-of-band.
   *Enforced:* `execution_environment.nodus_runtime_kwargs()`, `nodus_worker.py:439-444`.
   *Pinned by:* `test_guest_confinement.py` (`test_worker_vm_is_constructed_with_all_three_denials`,
   `test_subprocess_is_denied_and_writes_no_host_file`, `test_network_is_denied`,
   `test_host_env_is_denied`), `test_guest_environment_binding.py`.

5. **A declared environment may only narrow its floor, never widen it, and every widening
   attempt is counted.** `clamp_to_floor()` intersects the declared spec with the seam's floor
   and returns the list of fields it had to clamp; `resolve_environment()` logs them. Silent
   clamping is specifically what this module was written to remove.
   *Enforced:* `execution_environment.clamp_to_floor()`. *Pinned by:*
   `test_execution_environment_spec.py`.

6. **A tool that declares `isolation=` runs out of process, and if its worker cannot run, the
   tool does not run.** There is **no fallback** to in-process execution — the opposite of the
   nodus adapter, which spills to a fresh subprocess because both paths give the same guarantee.
   Here a fallback would run a tool that asked to be confined *unconfined*. A crashed, timed-out
   (`_TOOL_WORKER_TIMEOUT_S = 120`) or unstartable worker is a tool failure, logged at ERROR.
   *Enforced:* `tool_registry.py:481-490`. *Pinned by:* `test_tool_isolation_enforcement.py`.

7. **Authority is decided in the parent, never in the worker.** `execute_tool` checks token,
   granted tools, capabilities, policy, rate limit, egress and secret scope *before* delegating;
   `tool_worker.run_one` resolves the function and runs it. Re-evaluating authority inside the
   process the boundary exists to distrust would be a contradiction; calling `execute_tool`
   there would recurse. The worker receives `db=None` — all 18 tool functions take it, none
   use it, and a session cannot cross a process boundary anyway.
   *Enforced:* `tool_worker.py` (module docstring is the contract; no auth code exists there
   to remove). *Pinned by:* `test_tool_isolation_enforcement.py`, `test_exec_env_tool_seam.py`.

### Admission (what refuses to start)

8. **An unknown assurance class is refused at declaration, not downgraded.**
   `register_tool(isolation="…")` with a value outside `ASSURANCE_ORDER` raises; a declaration
   the host cannot satisfy (`isolation="container-grade-sandbox"` on an `insecure-dev` host) is
   refused fail-closed at registration, before first call.
   *Enforced:* `tool_registry.py:200-212`, `execution_environment.resolve_environment()`.
   *Pinned by:* `test_tool_isolation_declaration.py`.

9. **A spec whose `min_assurance` exceeds what the host provides is unsatisfiable and the unit
   must not run.** `_host_assurance()` reports the **weakest** class on any resolution failure,
   so an error cannot make a strict requirement pass — failing toward refusal is the only safe
   direction. *Enforced:* `execution_environment.py:600-607`, `:522-547`. *Pinned by:*
   `test_execution_environment_spec.py`.

10. **Under a production-safe profile, an `external-third-party` plugin cannot start on the
    development runner, on `auto`, without a pinned runtime identity, or without an accepted
    trust chain.** `validate_external_third_party_plugin_runtime_policy()` raises for each,
    with an operator-actionable message naming the setting to change.
    *Enforced:* `deployment_contract.py:762-830`, called from `plugin_host.start_plugin_host`.
    *Pinned by:* `test_deployment_profiles.py`.

11. **Under `hostile-third-party`, only `strong_sandbox_vm` is admitted, and the host is killed
    if its live attestation does not verify.** Admission is checked twice: before spawn (invariant
    10) and *after* launch against `hostile_third_party_attestation_violations()`; on any
    violation the process is force-terminated and the host marked `contract_violation`.
    `/health/deep` then reports `plugin_sandbox_attestation` as a failure (503).
    *Enforced:* `plugin_host.py::_start_record` (one failure path for every post-launch
    check: mark with the failure's kind, force-kill, raise), `health_service.py:876-883`.
    *Pinned by:* `test_plugin_host.py` (`…rejects_container_runner_in_hostile_profile`) and
    `test_deployment_profiles.py` for the **pre-spawn** refusal; **the post-launch kill by
    `test_plugin_host_attestation_kill.py`** (2026-09-16, `SANDBOX-EVIDENCE-1` closed) — a
    strong runner whose argv-derived launch attestation and live probe are the real code over a
    fake process, one field broken, the process asserted dead through both `start_plugin_host`
    and `restart_plugin_host`. **The same guarantee now holds for a failed strong-sandbox live
    verification** (`_verify_post_launch_state` ≠ passed): it used to raise without marking or
    killing, so through `restart_plugin_host` an unverified worker stayed alive as `running`.

12. **A container or strong runner will not launch without a configured image, a valid runtime
    identity, and — for strong — a Linux host and a launcher on `PATH`.**
    `_ensure_container_runtime_ready()` / `_ensure_strong_sandbox_ready()` raise `RuntimeError`
    before building argv. *Enforced:* `sandbox_runner.py:1592-1628`, `:1964-2005`. *Pinned by:*
    `test_sandbox_runner.py`.

### Evidence

13. **Certification is computed from verified evidence, never configured.**
    `sandbox_certification_profile()` returns `tier_status: certified` only when every
    requirement of the tier *for the selected runner* is met (§Assurance Reporting in the trust
    model lists them). A selected-but-unverified runner is `not_certified_for_runner`.
    *Enforced:* `sandbox_certification.py:187-290`. *Pinned by:*
    `test_plugin_sandbox_certification.py`.

14. **The assurance ceiling reflects evidence, not the selected runner.** The strong runner's
    ceiling rises to `kernel-observable-verified` only after `/proc/<pid>` evidence has been
    collected for a *live* worker on Linux; before that it is `worker-self-report-verified`, and
    a non-Linux host stays there. The container runner runs **no** post-launch probe at all —
    its `verification_method` is `none` by design, and its ceiling describes what it could
    produce if probed. *Enforced:* `sandbox_runner.sandbox_runner_assurance_posture()`,
    `plugin_host._verify_post_launch_state()`. *Pinned by:* `test_sandbox_verification_posture.py`.

15. **`production_safe_third_party_plugin_execution` is decided by the container backend, not
    the host OS.** A Windows or macOS host with Docker Desktop in Linux-containers mode
    (`docker info` → `OSType: linux`) reports `True`; detection failure reports `False` with an
    `operator_note`; there is **no** operator override. Re-detected on every matrix call.
    *Enforced:* `sandbox_runner._detect_linux_container_backend()`, `_platform_matrix_entry()`.
    *Pinned by:* `test_sandbox_runner.py` (`test_windows_host_docker_info_reports_linux` /
    `_reports_windows`, `test_sandbox_platform_matrix_reports_windows_degraded_support`).

16. **The escape posture is a read artifact, and "not run" is a distinct state from "passed".**
    `sandbox_escape_test_posture()` reads `tests/sandbox/sandbox_escape_results.json` and reports
    `not_run | all_pass | has_failures | has_errors`; an installed wheel has no `tests/` and
    honestly reports `not_run`. *Enforced:* `sandbox_runner.py:699`. *Pinned by:*
    `test_sandbox_verification_posture.py`; the release gate in `RELEASE_CHECKLIST.md` requires
    `all_pass` and `SANDBOX_ESCAPE_AUDIT.md` records every gate run.

---

## 4. Runner Matrix

What each plugin-host runner actually delivers. Defaults are `AINDY/config.py` values; every
`AINDY_PLUGIN_CONTAINER_*` flag is an operator setting and turning one off is visible in the
launch attestation, not silent.

| | `insecure_dev_subprocess` | `containerized_oci` | `strong_sandbox_vm` |
|---|---|---|---|
| Assurance class | `insecure-dev` | `container-grade-sandbox` | `strong-sandbox-tier` |
| Boundary | OS process | OCI container | VM via `aindy-sandbox-vm` launcher |
| Host platforms | any | any with a Linux-containers backend | **Linux only** (`C3`) |
| Child environment | **`DATABASE_URL`, `AINDY_ALLOW_SQLITE`, `ENV`, `TESTING`, `TEST_MODE`** and PATH-class vars pass through (`sandbox_runner.py:1257`) | `PYTHONIOENCODING` (+ `AINDY_ALLOW_PRIVATE_EXTENSION_TARGETS` if set) | `PYTHONIOENCODING` only |
| Plugin root | host path, as-is | bind-mounted **read-only** at `/plugin-root` | `--mount-readonly` (launcher flag) |
| Filesystem | worker-side guard only | `--read-only` rootfs + tmpfs `/tmp` (64m) | `--deny-host-paths`, `--tmpfs` (launcher flags) |
| Network | worker-side socket guard only | `--network none` | `--network-deny-default` (launcher flag) |
| Privileges | none dropped | `--cap-drop ALL`, `--security-opt no-new-privileges` | launcher-enforced; verified via `/proc` post-launch |
| Resource limits | none | `--pids-limit 64`, `--memory 256m` (+ cpu if set) | `sandbox-runtime-hard-limits` |
| Kernel controls | n/a | seccomp / AppArmor / SELinux **if** profile configured and backend supports | required |
| Runtime identity | n/a | digest-pinned reference required under production-safe profiles | signed + pinned; trusted source required |
| Post-launch probe | none | **none** | worker self-report + `/proc` evidence |
| Reachable certification | `contained-process-certified` | `container-sandbox-certified` | `strong-sandbox-certified` |

Every runner also installs the **worker-side guards** in `extension_worker.py` at session start:
an import guard (only `AINDY.platform_layer.extension_runtime_api` is importable from plugin
code), a filesystem guard (plugin root read-only, writes only under the temp scope), a network
guard (deny-by-default outbound; opt-in HTTP), an environment strip (everything but PATH-class
vars), and a module prune (runtime modules removed from `sys.modules`). **These are the
`insecure-dev` runner's entire boundary**, which is why that class is named what it is: a guard
inside the process it guards is bypassable by the code it guards.

The **plugin → runtime bridge** (`extension_runtime_api`: `memory.read`, `memory.write`,
`flow.run`, `event.emit`, `tool.invoke`) is capability-gated against the `granted_capabilities`
the host supplied, tenant-bound, and every operation ends at `dispatch_syscall` or
`execute_tool` — a plugin cannot reach a syscall the host did not grant. Note that the bridge
executes **inside the worker process** under the worker's own environment: in the container and
strong runners no database URL reaches it, so those operations are structurally unavailable
there; in `insecure-dev` the worker holds `DATABASE_URL` (§7.1).

---

## 5. Deployment Profile Requirements

From `deployment_contract.plugin_sandbox_profile_requirements()`; enforced by invariants 10–11.

| Profile | Required runner | Required assurance class | Required certification at startup |
|---|---|---|---|
| `single-instance` | none | none | none |
| `distributed-api` / `distributed-worker` | `containerized_oci` | `container-grade-sandbox` | none |
| `hostile-third-party` | `strong_sandbox_vm` | `strong-sandbox-tier` | `strong-sandbox-certified` |

`resolve_sandbox_runner_type()` selects `containerized_oci` automatically under the distributed
profiles or `EXECUTION_MODE=distributed`, and `insecure_dev_subprocess` otherwise. Under a
production-safe profile `auto` is **not accepted** for third-party plugins — the operator must
name the runner (invariant 10), so a misconfigured host fails at plugin start rather than
running a third-party plugin in the development runner.

---

## 6. Interaction with the Other Contracts

- **Execution Contract.** A sandboxed unit is still an execution unit: it has one terminal
  state, produces traceable output, and its externalization is a first-class execution fact
  (`execution_model_class` on the surface matrix, `runner_type` and attestation on the plugin
  host record). Crash containment is a sandbox property; crash *continuation* is
  `../design/DURABLE_EXECUTION_PROGRAM.md`'s.
- **Idempotency Contract.** The boundary carries no effect semantics. A plugin's `tool.invoke`
  reaches `execute_tool` and therefore the `EffectRecord` chokepoint; a guest's `sys()` reaches
  `dispatch_syscall` and the gate. Nothing a sandboxed unit does directly (a file under its
  scratch root, a computation) is an effect the ledger knows about — by design, since the
  boundary is what makes those actions unable to reach the world.
- **`ExecutionEnvironmentSpec`** (`../design/EXECUTION_ENVIRONMENT_SPEC_DESIGN.md`) is the shared
  vocabulary: what an execution *requires* (`min_assurance`, visibility, authority, resources),
  clamped to the seam's floor. This contract is about what each seam *delivers*; the spec is how
  a unit asks. Resource ceilings on the spec (`wall_time_ms`, `syscalls`, `tokens`) are
  enforced by `ResourceManager`; **memory is declared and recorded, not enforced** (`SYSMAX-3`).

---

## 7. What This Contract Does Not Guarantee

Stated so that a reader cannot infer them from silence. Each is either a design decision with a
recorded reason or an open item with a `TECH_DEBT.md` entry.

### 7.1 Known, by design

- **Tier 1 code is unconfined.** Manifest bootstrap and kernel callables for `runtime-built-in`
  and `first-party-app` can execute arbitrary Python, mutate process state, and violate any
  invariant in this document. They are trusted because the operator deployed them.
- **The `insecure-dev` runner is not a sandbox.** Its worker receives `DATABASE_URL` and the
  test-mode variables, its guards live inside the process they guard, and its assurance ceiling
  is `no-isolation-guarantee`. Its name is the contract.
- **An undeclared tool runs in-process.** `TOOL-SEAM-ISOLATION-1` closed with this gap on
  purpose: isolation is opt-in per tool via `isolation=`, and a tool that does not declare is
  first-party code the operator registered.
- **The container runner runs no post-launch probe.** Only `strong_sandbox_vm` does. The
  container runner's launch attestation (backend identity, digest, mount mode, resource-limit
  mode) is what `container-sandbox-certified` rests on.
- **Cancellation reach is a function of the isolation class.** A cancelled run refuses its
  *next* tool call and its *next* syscall (both chokepoints, `CANCEL-REACH-1`). An in-process
  tool already executing is never interrupted — cooperative by construction. An **isolated**
  tool's worker IS killed: the parent polls the cancel predicate while the worker runs and
  terminates → kills it (`aindy_run_cancel_observed_total{surface="tool_worker"}`). Until
  2026-09-15 that kill existed only as the timeout and the contract over-claimed it.
- **`AINDY_TOOL_ISOLATION=0` reverts declared tools to in-process.** It is a deployment-wide
  switch, visible in one place, never per-call. Declarations are still validated and still
  refused when unsatisfiable.

### 7.2 Open — tracked

- **The tool worker inherits the entire server environment unless the tool narrows it**
  (`TOOL_FLOOR` is permissive; `SECRET_KEY`, `DATABASE_URL` and every provider key are visible to
  an isolated tool by default). `EXEC-ENV-BIND-1` phase 3 made this *declarable*; it did not
  change the default. The floor is deliberately today's behaviour written down.
- **Filesystem `roots` are enforced on the guest seam only.** At the tool seam, a declared
  scoped filesystem sets `cwd` to a scratch root and nothing more — a bare subprocess can still
  open any path the OS allows. `cwd` is a default location, not a boundary. Enforcement needs
  the container runner at that seam, not another spawn argument (`FS-SCOPE-1`).
- **`strong-sandbox-certified` and `hostile-third-party` are Linux-host-only** (`C3`;
  preparation plan in `../design/C3_NON_LINUX_STRONG_SANDBOX_PLAN.md`).
- **The container and strong runners are unreachable from inside a container.** The distributed
  profiles require `containerized_oci`, but a runtime that is itself the container cannot
  launch one — so no shipped compose satisfies distributed mode's sandbox chain, and
  `AINDY_DEPLOYMENT_PROFILE` offers no escape (`FR-15`, evidence attempt 2026-09-08).
- **The egress guard is off by default and its own docstring names two bypasses**
  (`EGRESS-INPROC-1`). Network denial in the container and strong runners is `--network none`
  at the kernel, which does not depend on it; the guard matters for in-process tools.
- **`create_sandbox_runner` is reachable from one seam.** The guest VM and the tool worker do
  not use the plugin-host runners; each has its own weaker mechanism (deny flags; a bare
  subprocess). Re-homing the provider so all three seams can ask for a runner is the shared root
  of `FS-SCOPE-1` and the closed `EXEC-ENV-BIND-1` / `TOOL-SEAM-ISOLATION-1`.
- **Strong-sandbox verification is unprivileged.** `/proc` evidence is kernel-observable but
  read without a privileged launcher; BPF filter introspection (ISOLATION plan C1 Scope B2) has
  a stated reopen condition and no current trigger.

---

## 8. Enforcement and Verification

| Guarantee | Test | Job |
|---|---|---|
| Two execution-model classes; surface matrix | `tests/unit/test_runtime_public_contract.py` | `Runtime Contracts` |
| Runner readiness, argv, child env, platform matrix, backend detection | `tests/unit/test_sandbox_runner.py` | `Runtime Contracts` |
| Certification tiers computed from evidence | `tests/unit/test_plugin_sandbox_certification.py` | `Runtime Contracts` |
| Assurance ceiling / verification method / escape posture | `tests/unit/test_sandbox_verification_posture.py` | `Runtime Contracts` |
| Plugin host lifecycle, hostile-profile admission and kill | `tests/unit/test_plugin_host.py` | `Runtime Contracts` |
| Profile requirements and refusals | `tests/unit/test_deployment_profiles.py` | `Runtime Contracts` |
| Guest VM denials and filesystem bound | `tests/unit/test_guest_confinement.py`, `test_guest_environment_binding.py` | `Runtime Contracts` |
| Spec clamp, unsatisfiable refusal, resources | `tests/unit/test_execution_environment_spec.py`, `test_exec_env_resources_enforcing.py` | `Runtime Contracts` |
| Tool isolation declare / refuse / out-of-process / no-fallback | `tests/unit/test_tool_isolation_declaration.py`, `test_tool_isolation_enforcement.py`, `test_exec_env_tool_seam.py` | `Runtime Contracts` |
| **Container escape attempts against a real Docker backend** (17 tests: filesystem, path traversal, env leak, network, capabilities, no-new-privileges, pids) | `tests/sandbox/` — marker `sandbox_escape`, **never** `integration` | `sandbox-escape-linux.yml`; release gate |

**Reading these results.** The escape suite is the only row that exercises a real boundary
rather than the runtime's *reporting* of one, and it runs against `containerized_oci` only —
there is no escape suite for `strong_sandbox_vm` (its launcher is out-of-tree) or for the tool
worker (which has no container to escape). A green `Runtime Contracts` proves the runtime
*says* the right thing about its sandbox; a green escape run proves the container backend
*does* it. Both are needed and they are not substitutes. Per `CLAUDE.md` §*Trusting a green
check*: the escape suite skips silently when Docker is absent, so its result is meaningful only
when `sandbox_escape_test_posture()` reports `all_pass` with a recent `last_run`.

**Coverage gaps in this table, stated so they are not inferred as covered:** the strong runner's argv is
asserted by `test_sandbox_runner.py` but its launcher (`aindy-sandbox-vm`) is out-of-tree, and its
launch attestation marks fields verified by checking that argv, so what those flags *do* is
verified only by the post-launch `/proc` probe on a live Linux host, never in CI
(`SANDBOX-EVIDENCE-2`).

**Before changing anything in §4:** mutation-test it. The sandbox tests are heavy on source and
posture assertions; a hardening flag removed from argv should turn `test_sandbox_runner.py` red
*and* an escape test red. If only the first happens, the escape suite was not covering that
flag.

---

## 9. Open Operational Questions

- **Should the container runner run a post-launch probe?** It could — the worker implements the
  probe protocol for every runner — but its ceiling would still be `worker-self-report-verified`
  (no `/proc` evidence for a shared-kernel container from outside it). The question is whether
  self-report evidence is worth its per-launch cost for a tier whose certification rests on
  launch attestation. Not decided.
- **Should `TOOL_FLOOR` tighten `visibility.env` by default?** Today's default leaks every
  server secret into every isolated tool; the fix (default `allowlist`, tools declare what they
  read) breaks every tool that reads a credential without declaring it. It is a migration, not
  a flag flip. Not scheduled.
- **When the guest and tool seams can ask for a container runner** (the re-homing in §7.2),
  which floor do they get? The guest floor is already stricter than the container runner's
  defaults in one dimension (no filesystem at all outside scratch) and weaker in another (no
  kernel controls). The intersection is the honest answer; whether it is the useful one is open.
