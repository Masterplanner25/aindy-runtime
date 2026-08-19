---
title: "ExecutionEnvironmentSpec — Design (EXEC-ENV-BIND-1)"
api_version: "1.0"
last_verified: "2026-08-19"
status: current
owner: "platform-team"
---

# `ExecutionEnvironmentSpec` — design

**Status: PHASE 1 SHIPPED 2026-08-19. Phases 2–4 are still design.** This document settled the
*shape* before anything landed, because a column added under the schema-contract protocol is
expensive to take back and the descriptor is a semi-public surface.

**What exists now:** `AINDY/core/execution_environment.py`, three columns on `execution_units`
(Alembic `0017`, schema contract `2026-08-19`), and an optional `env_spec=` on
`require_execution_unit`. **It confines nothing** — see §9. 32 tests, mutation-tested 7/7.

**One design decision changed during implementation, recorded here rather than silently:** §7
recommended raising `ExecutionEnvironmentUnsatisfiable`. The guard as built catches the **base**
`ExecutionEnvironmentError`, so a *malformed* spec propagates too. Letting `Invalid` fall into the
non-fatal handler would have been the worse of the two outcomes — the work would proceed with no
environment binding **and** no `ExecutionUnit` at all, from a caller actively trying to declare
one.

Tracks `EXEC-ENV-BIND-1` (P1). Read that `TECH_DEBT.md` entry first — this document does not
restate the justification, only the design that follows from it.

**Owner decision taken 2026-08-19: three axes, including resources.** See §4.

---

## 1. The gap, in one line

The runtime owns a provider abstraction (`SandboxRunner`, a 10-method ABC with three
implementations and a certification ladder) and **no vocabulary in which an execution unit can
request anything from it.** The provider half of the contract is built; the requesting half does
not exist.

Concretely: `ExecutionUnit` has 21 columns and not one of them expresses a filesystem, network,
process, resource, secret, or assurance *requirement*. It stores `wall_time_ms`, `memory_bytes`
and `syscall_count` — but those are **measured actuals**, recorded after the fact. Nothing on the
row says what the execution was supposed to be allowed to do.

The consequence is not that confinement is missing everywhere. It is that **"was this the
containment you asked for?" has no answer for any individual run**, because nothing was asked.

---

## 2. Four findings that change the filed proposal

All four were verified against source at `389cdb9`. Each one would have cost real work if
discovered during implementation instead.

### 2.1 The proposed resolution point is dead code

`EXEC-ENV-BIND-1` proposes resolving the descriptor at
`execution_gate.gate_and_dispatch` (`execution_gate.py:294`).

**`gate_and_dispatch` has zero callers, repo-wide.** It is not re-exported from any `__init__.py`
and nothing references it outside its own definition.

**The repo already knew.** `docs/runtime/MEDIATED_EFFECT_BOUNDARY_PROGRAM.md:50` states it
plainly: *"`gate_and_dispatch` (`execution_gate.py:364`) is dead code with no callers."* Two
documents, opposite implications, neither aware of the other — and the debt entry is the one an
implementer would read.

Building resolution there would have produced a mechanism that exists and never runs, which is
`ROUTE-AST-UNWIRED-1` exactly.

**The live seam is `require_execution_unit`** (`execution_gate.py:208`), with three call sites:

| Call site | Covers |
|---|---|
| `core/execution_pipeline/resources.py:12` | every route handler (the pipeline) |
| `routes/flow_router.py:153` | flow starts |
| `runtime/nodus_execution_service.py:1347` | nodus script execution |

### 2.2 A refusal cannot travel through `require_execution_unit`'s return contract

Its docstring is explicit: *"Returns None on failure (non-fatal — **callers must not block on
this**)."* The three call sites are written to that contract.

So a descriptor that refuses by returning `None` **would be ignored by design**. This is not a
detail to discover later; it decides the mechanism. Options in §7.

### 2.3 The gate can *refuse* but cannot *apply*

`gate_and_dispatch` takes `handler_fn: Callable[[], Any]` — a zero-argument closure the caller has
already built. Even if it were live, it could not apply confinement to an opaque closure.

This is not a problem. It is the split the entry already argues for — *the runtime claims
selection and refusal, the host keeps enforcement* — and it is what makes phase 1 safe:

| | Where it happens | Phase |
|---|---|---|
| **Declare** | caller supplies a spec | 1 |
| **Refuse** | before the unit runs, if the host cannot meet `min_assurance` | 1 |
| **Record** | required vs applied vs evidence class, on the row | 1 |
| **Apply** | at each seam separately — tool transform, nodus kwargs, runner selection | 2+ |

**Phase 1 changes no execution path.** Nothing can regress in how work runs, and it still
delivers the accountability the entry names as the actual value.

### 2.4 Runner resolution is host-scoped by construction

`resolve_sandbox_runner_type(explicit=None)` reads `settings.AINDY_PLUGIN_SANDBOX_RUNNER`,
`AINDY_DEPLOYMENT_PROFILE`, and `settings.EXECUTION_MODE`. Its only per-call input is an
`explicit` string. **There is no per-execution input.** That is the binding gap stated as code.

Related shape warning: `SandboxRunner` is a **long-lived JSON-RPC process** ABC
(`start`/`execute`/`probe`/`heartbeat`/`shutdown`/`pid`/`returncode`). `TOOL-SEAM-ISOLATION-1`'s
settled answer is a **command transform**, which is a different shape entirely. **The descriptor
must resolve to a policy, not to that ABC** — otherwise it silently assumes every seam is a
long-lived worker, which the tool seam is not.

---

## 3. What this is not

Stated up front because the entry's own scoping argument is the thing that keeps this from
becoming a framework. **One new type, not five.** Everything it resolves against already exists:

| Abstraction one might reach for | Why it is not needed |
|---|---|
| `IsolationProvider` | that is `SandboxRunner` — an ABC with three implementations |
| `ExecutionHost` | that is `sandbox_platform_capability_matrix()` — already per-OS |
| `CapabilityGrant` | that is the capability token — HMAC-signed, plan-bound |
| `ResourceBudget` | that is `ResourceManager` — already enforced per tenant |

What is missing is **only the request record.**

It is also **not a trust level.** A single enum, or a flat bag of booleans, forecloses the
combinations a caller has not anticipated. See §4.

---

## 4. The shape — three orthogonal axes

Adopted from `LINUX_KERNEL_ARCHITECTURAL_AUDIT.md` §22 Lesson 5: namespaces (visibility) +
cgroups (resources) + seccomp/creds (authority) are composed independently, which is why a Linux
caller can express a combination nobody shipped a preset for. **The decades-tested part is the
orthogonality, not the axis names.**

```
ExecutionEnvironmentSpec

  visibility          what the execution may SEE
    filesystem        { mode: none|readonly|scoped|host, roots: [path...] }
    env               { mode: none|allowlist|inherit,    allow: [name...] }

  authority           what the execution may DO
    network           { mode: none|scoped|open, egress_scope: <existing scope> }
    processes         { subprocess: bool }

  resources           how much the execution may USE
    wall_time_ms      declared ceiling
    memory_bytes      declared ceiling
    syscalls          declared ceiling

  min_assurance       insecure-dev | container-grade-sandbox | strong-sandbox-tier
```

### Why resources is in, and why that is the load-bearing choice

`resource_manager` already enforces a per-execution constraint — `MAX_WALL_TIME_MS` (300 s),
`MAX_MEMORY_BYTES` (256 MiB), `MAX_SYSCALLS_PER_EXECUTION` (100), `MAX_CONCURRENT_PER_TENANT` (5)
— at a *different seam*, in a *different vocabulary*, with its own failure mode (`SYSMAX-*`:
advisory per-EU caps). **A descriptor that answers "what environment does this need?" while
omitting "how much may it consume?" has picked two axes out of three for no reason except which
audit surfaced them.**

Including it also produces a symmetry worth having: the EU already carries the **measured
actuals** for exactly this axis. The descriptor adds the **declared ceilings** beside them, so
required-vs-applied is answerable on all three axes from one row.

`can_execute` already has four live call sites (`execution_pipeline/resources.py:65`,
`scheduler/dispatch.py:57`, `flow_engine/runner_steps.py:157`, plus internal), so the enforcement
points for this axis exist. Phase 1 does **not** rewire them; it records the declared values so a
later phase can.

### Where the other open entries land on the axes

This is the reason to settle the shape first: **four separate open entries are each one axis of
this, and none of them knows about the others.**

| Axis | Field | Entry it belongs to |
|---|---|---|
| visibility | `filesystem.roots` | **`FS-SCOPE-1`** — explicitly *a field on this descriptor*, not a second vocabulary |
| authority | `network.egress_scope` | **`EGRESS-INPROC-1`** — a re-homing, not a build |
| authority | `processes.subprocess` | **`GUEST-CONFINE-1`** residual + `TOOL-SEAM-ISOLATION-1` |
| resources | ceilings | `resource_manager`, `SYSMAX-1/-3/-4` |
| *(future)* | spend | **`COST-GOVERNOR-1`** — a fourth axis, and it will want to live here |

`COST-GOVERNOR-1` is the argument against the two-axis version in miniature: the moment a token
budget exists, it is a per-execution declared ceiling, and it will want exactly this row.

---

## 5. Where it is stored

**A real column, not `extra`.** `ExecutionUnit.extra` is JSONB and already carries `retry_policy`;
it is the right place for incidental payload and the wrong place for a declared requirement,
which must be queryable ("show me every unit that ran below its declared assurance") and must be
guardable by test.

Proposed, additive, all nullable:

| Column | Type | Holds |
|---|---|---|
| `env_spec` | `JSONB` | the declared spec, as above |
| `env_applied` | `JSONB` | what was actually selected/applied |
| `env_evidence_class` | `String(48)` | the resolved `assurance_class` + `assurance_ceiling` of what ran |

Three columns rather than one because they answer three different questions, and collapsing them
is the `register_syscall` / `FR-14` shape — a surface that loses a distinction the record already
had.

**A `NULL` `env_spec` means "declared nothing" and must behave exactly as today.** Every existing
row is `NULL`; the descriptor is opt-in per execution, and an undeclared unit is not refused. This
is the same posture `GUEST-CONFINE-1` took by measuring first that no first-party script used the
capabilities it denied.

**Schema-contract consequences** (per `CLAUDE.md`): bump `SCHEMA_CONTRACT_VERSION`, regenerate the
baseline, update the two assertions in `test_runtime_schema_contract.py`, add Alembic **`0017`**,
and bump `RUNTIME_ALEMBIC_HEAD_REVISION` in `AINDY/db/alembic_head.py`.

**★ CONFIRMED ON IMPLEMENTATION — this is a `FR-14` release**, which the app handoff must say: an additive
runtime column means a bare `bootstrap-schema` exits **3** and, under `set -e` +
`restart: unless-stopped`, crash-loops a container. The handoff must name
`bootstrap-schema --reconcile`. This is the first release since that exit-code work where the
condition actually fires — so it is also the first real test of it, and the `Upgrade Path Guard`'s
main job will finally be doing work rather than passing trivially.

---

## 6. Resolution and refusal

```
require_execution_unit(..., env_spec=<spec|None>)
    |
    +-- spec is None ------------------> today's behaviour, unchanged
    |
    +-- resolve host capability
    |     resolve_sandbox_runner_type() + sandbox_runner_assurance_posture()
    |     -> (assurance_class, assurance_ceiling)
    |
    +-- host class >= spec.min_assurance ?
    |     yes -> create EU, record env_spec / env_applied / env_evidence_class
    |     no  -> REFUSE (see §7)
```

The refusal pattern already exists in the codebase — `deployment_contract.py` refuses unsupported
profiles rather than degrading silently, and `get_deployment_profile_contract` raises on an
unknown profile.

**The invariant:** an execution unit runs only in an environment whose certified assurance class
meets or exceeds its declared minimum. If none is available on this host, the unit does not run.

**Why this is honest rather than overclaiming:** `sandbox_runner_assurance_posture()` already
refuses to overclaim — `insecure_dev_subprocess` reports
`ASSURANCE_CEILING_NO_ISOLATION_GUARANTEE`. Today that is a *report*. The descriptor turns it into
a *gate*, without the runtime claiming any enforcement it does not have.

---

## 7. The refusal mechanism — the one genuinely open question

§2.2 rules out the obvious answer. `require_execution_unit` returns `None` on failure and its
callers are documented not to block on that, so refusal cannot ride the existing return value.

Three candidates, with the objection to each:

**(a) A distinct exception — `ExecutionEnvironmentUnsatisfiable`.**
Loud, unambiguous, and impossible to swallow accidentally. Objection: the three call sites
currently treat this function as non-fatal, so each must be taught to let this one type through —
and `SyscallDispatcher`'s broad `except Exception` is precedent for how easily that goes wrong
(hence its explicit `except SyscallContractViolation: raise` guard placed *before* the broad
handler). **Any implementation must audit for a broad handler between the raise and the caller.**

**(b) Refuse at the seam that admits work, not at EU creation.**
The scheduler's `dispatch.py:57` already calls `can_execute` and handles a refusal. Objection: it
does not cover the route-handler path, which is the busiest of the three.

**(c) Create the EU in a terminal `refused` state and return it.**
Preserves the non-fatal contract exactly and leaves an audit row, which is the point of the whole
entry. Objection: `refused` is a new terminal status on a documented state machine, and callers
that ignore status would proceed to run the handler anyway — a refusal that does not refuse.

**Recommendation: (a) plus (c).** Raise, *and* leave the row. (c) alone is the failure mode the
whole entry is about — a stated posture with no teeth; (a) alone loses the audit record at exactly
the moment it is most interesting. Settle before implementing.

---

## 8. Phasing

| Phase | Content | Blast radius |
|---|---|---|
| **1** | spec type, three columns, resolution + refusal at `require_execution_unit`, per-run record | **no execution path changes** |
| **2** | the guest path asks — descriptor drives `nodus_worker` confinement kwargs; closes `GUEST-CONFINE-1`'s `cwd` residual | changes how guest scripts run; re-run the confinement suite against the real VM |
| **3** | the tool seam asks — `TOOL-SEAM-ISOLATION-1`'s command transform reads the descriptor | the P0 |
| **4** | resources axis becomes enforcing, not just declared; `COST-GOVERNOR-1` adds spend | touches `resource_manager` |

Phase 1 is deliberately the whole accountability story and none of the enforcement story.

---

## 9. What this does not claim

- **It does not confine anything in phase 1.** It records what was asked for and refuses what
  cannot be provided. Anyone reading a phase-1 row must not infer that the environment was
  enforced — `env_evidence_class` is what says whether it was.
- **It does not make Tier 1 safe.** The honest-posture defence still applies to deployed code, and
  still does not transfer to *submitted content* — a `.nd` script arriving through
  `POST /platform/nodus/run` is data from an authenticated session, not something an operator
  placed in a Tier-1 slot.
- **It does not supersede `GUEST-CONFINE-1`.** That fix is three kwargs already shipped; this is
  the vocabulary for asking, which is a different thing from the asking being done.

---

## 10. Verification plan

Written here because the repo's own catalogue says a new check is least proven when it is newest.

- **Mutation-test the guard.** A test that asserts an *absence* — "an undeclared unit is not
  refused" — passes when the whole mechanism is unwired. It needs a **liveness control** that
  proves refusal fires at all, run first.
- **A refusal test must call the caller, not the resolver.** `ROUTE-GUARD-1`: reading the source
  proves the guard was written, not that the caller receives its answer. Assert that a route whose
  EU declares an unsatisfiable `min_assurance` does not execute its handler.
- **The `NULL` path needs its own test**, because it is the path every existing row takes.
- **`Upgrade Path Guard` will finally be non-trivial** — an additive column is exactly the drift
  its main job is built to detect, and this is the first release to contain it. Read the main job,
  not only the `negative-control`.

---

## 11. Open decisions

1. **The refusal mechanism** — §7. Recommendation: raise **and** record.
2. **Who supplies the spec.** Caller-supplied is the honest default, but that means the value is
   attacker-influenced in the same way `AUTHORITY-VALUE-1` describes — a spec the calling frame
   supplies could *widen*. **A spec must only ever narrow against a host-level floor**, mirroring
   `AUTHORITY-VALUE-1`'s clamp. This should be decided before phase 1, not after.
3. **Whether `min_assurance` accepts `assurance_ceiling`** as well as `assurance_class`. They
   measure different things and the provider side already distinguishes them.
4. **Defaults per `eu_type`.** A registered default per kind (`flow`/`agent`/`nodus`/`job`) would
   make the descriptor useful without every caller supplying one — but a default that is not
   `NULL` changes behaviour for existing traffic, so it belongs in a later phase.
