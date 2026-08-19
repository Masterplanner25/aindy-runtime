### Added — `ExecutionEnvironmentSpec`: an execution unit can declare the environment it needs (EXEC-ENV-BIND-1, phase 1)

**★ Operators: this release changes the runtime-owned schema, and that has a deployment
consequence you must handle before upgrading.**

`execution_units` gains three additive, nullable columns (`env_spec`, `env_applied`,
`env_evidence_class`; Alembic **`0017`**, schema contract **`2026-08-19`**). Per `FR-14`, an
additive runtime column makes a bare `aindy-runtime bootstrap-schema` exit **3**
(additive-reconcile-required) — and under `set -e` with `restart: unless-stopped` that is a
**crash loop**, not a warning.

**Existing deployments must run `aindy-runtime bootstrap-schema --reconcile`, or branch on exit
code 3.** A fresh database needs nothing; `create_all` produces the columns. This is the first
release since the exit-code work landed where the condition actually fires, so it is also the
first time `Upgrade Path Guard`'s main job is doing real work rather than passing trivially.

#### What it is

The runtime owned a provider abstraction — `SandboxRunner`, three implementations, a
certification ladder — and no vocabulary in which an execution unit could *request* anything from
it. `ExecutionUnit` stored `wall_time_ms` / `memory_bytes` / `syscall_count`, but those are
**measured actuals**. Nothing recorded what an execution was supposed to be allowed to do, so
*"was this the containment you asked for?"* had no answer for any individual run.

`ExecutionEnvironmentSpec` (`AINDY/core/execution_environment.py`) is the request record. Three
orthogonal axes rather than a trust level or a bag of booleans:

| Axis | Fields |
|---|---|
| **visibility** — what it may see | `filesystem {mode, roots}`, `env {mode, allow}` |
| **authority** — what it may do | `network {mode, egress_scope}`, `processes {subprocess}` |
| **resources** — how much it may use | `wall_time_ms`, `memory_bytes`, `syscalls` |
| | `min_assurance`: `insecure-dev` \| `container-grade-sandbox` \| `strong-sandbox-tier` |

Pass `env_spec=` to `require_execution_unit`. The spec is clamped to the host floor, the host's
assurance class is resolved, and the unit is **refused** if the host cannot meet the declared
minimum — raising `ExecutionEnvironmentUnsatisfiable` *and* writing a terminal `refused`
ExecutionUnit row.

#### What it does NOT do

**It confines nothing.** Phase 1 is declare / refuse / record; each seam applies its own
environment in a later phase. **A populated `env_applied` is not evidence that an execution was
confined — `env_evidence_class` is the field that says whether it was**, and on the default dev
runner it reads `insecure-dev/no-isolation-guarantee`.

Nothing changes for existing callers. `env_spec` defaults to `None`, every pre-existing row is
`NULL`, and `NULL` is defined to behave exactly as before these columns existed.

#### Two properties worth knowing

- **A spec may only ever narrow.** The effective spec is the intersection of the declared spec
  and a host floor; a caller may ask for *more* confinement and never for less, because a
  caller-supplied value is attacker-influenced in exactly the way `AUTHORITY-VALUE-1` describes.
  Every widening attempt is logged at WARNING so the exposure is countable. **Unlike that entry's
  clamp this one is not behind a flag** — no caller supplies a spec today, so there is no
  compatibility argument for shipping a security default off.
- **Refusal deliberately breaks the non-fatal contract, and only here.**
  `require_execution_unit` returns `None` on failure and its callers are documented not to block
  on that, so `ExecutionEnvironmentError` gets an explicit re-raise guard placed *before* the
  broad handler — the same shape `SyscallContractViolation` needed in `SyscallDispatcher`. A
  refusal swallowed by a broad handler is worse than no refusal, because the row says `refused`
  while the work ran.

Design and phasing: `docs/runtime/EXECUTION_ENVIRONMENT_SPEC_DESIGN.md`. 32 tests across two
suites, mutation-tested **7/7** including a liveness control that fires if refusal is disabled
entirely.
