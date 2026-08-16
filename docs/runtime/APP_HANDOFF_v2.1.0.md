---
title: "App Handoff — aindy-runtime v2.1.0"
api_version: "1.0"
last_verified: "2026-08-15"
status: current
owner: "platform-team"
---
# App Handoff — `aindy-runtime` v2.1.0

**Published to PyPI 2026-08-15 as `aindy-runtime==2.1.0`** (verified against the package
index). Release commit `ea988d1`, tag `v2.1.0`, full pipeline green, Linux sandbox-escape gate
17/17 PASS (`SANDBOX_ESCAPE_AUDIT.md` Entry 014).

**Minor — and the thing to be careful about is that nothing forces you to notice it.**
Supersedes `APP_HANDOFF_v2.0.0.md`.

---

## 1. This one arrives passively. That is the whole difference from 2.0.0

`recommended_runtime_requirement` still reports `>=2.0,<3.0`, and your pin is already
`aindy-runtime>=2.0.0,<3.0` — **so 2.1.0 resolves on your next install with no coordination and
no pin move.**

2.0.0 forced a conversation by moving the pin. This one does not. The work below is therefore
*checking behaviour changes*, not performing an upgrade.

**Run migrations.** Alembic head **`0016`**, schema contract `2026-08-15.1`. Migration `0016`
only *widens* what is accepted — it replaces a global `UNIQUE (agents.name)` with two partial
unique indexes — so there is nothing to backfill and no data to prepare. Verified against real
PostgreSQL on nine properties, including blank-database safety and an idempotent downgrade.

---

## 2. One behaviour change that lands directly on your code

**`memory_agents_list` is now owner-scoped.** You wire it at `apps/memory/bootstrap.py:96`
(`"memory_agents_list": "memory_agents_list_result"`), so this is not hypothetical.

| | |
|---|---|
| Before | every active agent, to every caller |
| After | `owner_user_id IS NULL OR owner_user_id = <caller>` |

**While every agent row is un-owned, the output is identical** — which is exactly why the old
behaviour survived this long. It diverges the moment anything writes `owner_user_id`, and 2.1.0
is the release that makes that possible for the first time (§4, FR-12/FR-12b). If your UI
expects a global agent roster, that assumption becomes wrong as soon as your users own agents.

---

## 3. Other behaviour changes, in order of likelihood of biting

**`/health/deep` reports the event bus `degraded` while publishing is suspended.** It previously
reported it *disabled*. If you alert or dashboard on that field, the string changed. The cause is
a genuine fix: three consecutive failed publishes used to latch the bus off permanently, so a
transient Redis blip ended cross-instance WAIT/RESUME for the life of the process. It now
suspends and recovers on a circuit breaker.

**Several admin routes return their real status codes.** `POST /platform/admin/agents/register`
with a reserved namespace is now `409`; `DELETE /platform/admin/agents/{missing}` is now `404`.
Both previously returned **`500`** with `{"error": "internal_error"}`. If you have retry or
alerting keyed on 500s from these endpoints, revisit it — a 500 was previously indistinguishable
from a real server fault.

**`TenantContext.capability_scope` is a `tuple`.** `in`, `len`, iteration and `has_capability`
are unchanged; only mutation now raises `AttributeError`. If you were appending to it, that was
never sound — the dataclass is `frozen=True` and documented immutable.

---

## 4. Your FRs — what landed

| FR | Status | What you need to do |
|---|---|---|
| **FR-11** | Shipped | Nothing. Optionally tune the new env var |
| **FR-12** | Shipped | Use `register_agent` if you want declarative identity |
| **FR-12b** | Shipped | New user-facing surface — see below |
| **FR-13** | Shipped | Note the attribute-vs-column gotcha |

### FR-11 — configurable runtime-callback budget

`AINDY_RUNTIME_CALLBACK_TIMEOUT_SECS`, default **30s**, replacing a hardcoded 10s that no call
site could override. **Resolved at call time**, so it needs no restart to change.

Sized on measurement rather than taste: ~3.85s median cold start on the *lightest* profile is
only ~2.6× headroom at 10s, while the sibling nodus subprocess budgets 15s for boot alone on top
of a 30s script clock.

**Your filed mechanism was wrong and the record says so.** `bootstrap_register` fires only for
`runtime_agent_defaults`, not a 16-app bootstrap; the real cost is a fresh subprocess
`import_module` pulling an app's transitive graph. This also turned out to be the cause of
`FLAKY-1`, a ~50% test failure that had been blocking merges at random — closed by this change.

### FR-12 — `registry.register_agent`, the identity hook

Declarative: it records a spec and touches no database, because plugin load happens long before
a session exists. `startup._apply_registered_agents()` upserts by `memory_namespace` at boot and
*updates* an existing row, so changing an agent's display name or metadata between boots needs no
manual edit.

**Your filed premise was wrong, and one part matters operationally.** The entry said *"the only
ways to add a row are a runtime code change or a raw INSERT"* — but
`POST /platform/admin/agents/register` already existed and was mounted. The real gaps were
narrower: no *hook*, no path ever wrote `owner_user_id`, reads were unscoped, and —

> **the seven platform system namespaces were unreserved.** Registering with
> `memory_namespace: "runtime"` took the route's *idempotent-update* branch and silently rewrote
> the platform's own Runtime agent row — name, type and description — for **anyone with admin on
> your deployment**. The next boot did not repair it, because the seed only inserted when the row
> was absent.

Both the hook and the route now reject the seven reserved namespaces from one shared set. Boot
also repairs a drifted system row, and `POST /platform/admin/agents/{namespace}/restore`
reactivates one without a restart. **Worth checking your `agents` table** for a system row whose
name or description does not match the platform spec.

### FR-12b — user-owned agents

`GET|POST /platform/agents`, `PATCH|DELETE /platform/agents/{slug}`,
`POST /platform/agents/{slug}/restore`. Contract details you will hit immediately:

- **`memory_namespace` is derived, not accepted** — `u:<user_id>:<slug>`. You supply a `slug`
  matching `^[a-z0-9][a-z0-9._-]{0,63}$`. A caller-chosen namespace would have to 409 on a row
  the caller cannot see, which is a cross-tenant existence oracle.
- **`agent_type` is forced to `custom`** and is not caller-settable.
- **`POST` is not idempotent** (unlike the admin route) — a repeated slug is `409`. Use `PATCH`
  to change an existing agent.
- **Another user's agent is `404`, never `403`.**
- **`slug` is immutable on `PATCH`** — it is the tag already written onto that agent's memory
  nodes, so changing it would orphan its history.

### FR-13 — `agents.metadata` and `agents.updated_at`

Both nullable, purely additive, no backfill.

> **★ Gotcha: the ORM attribute is `Agent.agent_metadata`; the COLUMN is `metadata`.** `metadata`
> is reserved on a SQLAlchemy declarative class (`Base.metadata`). Raw SQL and JSONB queries see
> `metadata` — `WHERE metadata->>'workspace' = 'w1'` works as expected.

---

## 5. New settings

| | |
|---|---|
| `AINDY_RUNTIME_CALLBACK_TIMEOUT_SECS` | **30** — resolved at call time, so no restart to change |
| `AINDY_EVENT_BUS_PUBLISH_RECOVERY_SECS` | **60** — half-open probe interval for the publish breaker |

---

## 6. Decisions taken that will reach you in a later release

**A JWT will stop bypassing scopes.** Today `enforce_api_key_scope` gates API-key callers only —
its own docstring says *"JWT users carry full trust and are never gated by this check"* — so an
interactive session is currently *more* privileged than any API key. That is being changed.

**The prerequisite is not decided yet, and it affects you:** a JWT carries no scopes at all today
(`create_access_token` encodes `tv`, `purpose`, `exp`). The likely approach is deriving authority
from the user row rather than adding a claim, precisely so existing sessions keep working — 2.0.0
already ended every session once for the `purpose` claim. Expect the rollout to start permissive
and narrow. **If you have a view on which scopes your UI actually needs, now is the useful time
to say so.**

**An admin may deactivate a platform system agent.** Decided deliberately. Boot does *not*
re-enable it — silently reversing an operator action would be worse — but it logs a warning and
`POST /platform/admin/agents/{namespace}/restore` is the way back without a restart.

---

## 7. Still open on our side — one of which is yours to know about

**`GUEST-CONFINE-1` (P0, demonstrated) — relevant to you because you run Nodus scripts.** A
guest script executed through the runtime reaches `subprocess`, network and the host environment
**without passing the syscall dispatcher, the capability token, the effect ledger or the egress
guard.** Demonstrated, not inferred: a guest script created a file on the host filesystem.

**The sandbox escape gate does not cover this.** That suite certifies the *Tier-2 extension
sandbox* reached through `plugin_host.py`. The guest VM is a different seam and has never been in
its scope, so "17/17 PASS" and "the guest runs unconfined" are both true and not in conflict.

The immediate fix is three keyword arguments and no first-party script in either repository uses
the affected modules, so the change should break nothing. **In the meantime, treat Nodus script
content as trusted input** — it is submitted through an authenticated route, but it is data, not
deployed code.

Also open, and worth knowing rather than acting on:

- **`IDEM-11`** — the at-most-once effect gate is off by default, and only one of the registered
  syscalls declares its execution guarantee. Duplicate-effect exposure in default configuration
  is real.
- **`HTTP-SCOPE-GAP-1`** — scope checks reach a small minority of HTTP routes. See §6.
- **Your `FR-7` status is stale** (flagged previously, repeated because it has not moved): all
  four defects shipped in 2.0.0 and are in source. You run 2.1.0, so only the document is behind.

**Next available FR number: `FR-14`.**
