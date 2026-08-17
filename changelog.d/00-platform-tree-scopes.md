### Fixed — a narrow API key could reach the whole `/platform` tree, including signing-key rotation (`KEY-SCOPE-ESCALATION-1`, `HTTP-SCOPE-GAP-1`, #465)

**Security. Operators should read this and audit issued keys before upgrading.**

`require_platform_admin_access`, the dependency on the `/platform` parent router, returns **any**
authenticated API key unconditionally — its docstring justifies that with *"scope enforcement
happens per-endpoint or per-syscall"*. For **46 of 53** routes it did not. Demonstrated from a key
holding the single scope `flow.read`, owned by a non-admin user:

| Route | Before | After |
|---|---|---|
| `GET /platform/keys`, `/nodes`, `/webhooks`, `/nodus/*`, `/queue/*`, `/observability/*`, `/flows/runs` | **200** | 403 |
| `POST /platform/queue/dead-letters/drain` | **200** — drained the queue | 403 |
| `POST /platform/ops/rotate-secret-key` | **200 — rotated the platform signing key** | 403 |

**★ The rotation is worse than destructive.** The caller supplies the new key, so afterwards they
know the signing secret and can mint tokens that verify — every user impersonable, admin
included. `KEY-SCOPE-ESCALATION-1`'s delegation rule does not touch this: that rule bounds what a
key may *grant*, not what it may *do*.

**Scopes now required**, per endpoint:

| Routes | Scope |
|---|---|
| `/platform/keys` (4), `/queue/*` (5), `/nodes` (4), `/observability/*` (11), `/flows/runs*` + `/flows/registry` (5), `POST`+`DELETE /platform/flows` (2), `/ops/rotate-secret-key` | `platform.admin` |
| `/platform/webhooks` (4) | `webhook.manage` |
| `/platform/nodus/*` — run, upload, list, schedule, flow (7) | `flow.execute` |
| `/platform/tenants/{id}/usage` | `execution.read` |

**Interactive users are unaffected — not "mostly", at all.** The parent gate already required
`is_admin` for JWT callers, and an admin session derives both `platform.admin` and
`webhook.manage`. Only API keys are newly constrained, which is the entire point.

**★ One first-party consumer is affected, and it is ours.** `aindy-runtime nodus run` and
`aindy-runtime nodus upload` (`AINDY/cli.py`) post to `/platform/nodus/run` and
`/platform/nodus/upload`. A platform key (`aindy_…`) used with the CLI now needs **`flow.execute`**;
before, any key worked. A Bearer JWT for an admin is unaffected — admin sessions derive
`flow.execute`. Nothing else in this repo, the SDK, or the app monolith sends `X-Platform-Key`:
the SDK's `client.memory.*` is `MemoryAPI(self.syscalls)`, i.e. `POST /platform/syscall`, which is
one of the two routes deliberately left ungated below.

**Audit advice:** a platform API key issued with narrow scopes could, until now, do anything on
this tree. Review `users.is_admin` for accounts you did not promote, and rotate `SECRET_KEY`
yourself if you cannot account for every key that has existed.

**Why the router gate was not simply tightened.** `POST /platform/syscall` is the SDK's entire
surface and is used with narrow scopes like `memory.read`; requiring `platform.admin` there would
break every SDK caller. The fix is the per-endpoint enforcement the docstring already assumed.

**Two routes stay ungated at the route level, deliberately:** `POST /platform/syscall` and
`GET /platform/syscalls`. Their authority is resolved **per syscall** by
`_resolve_dispatch_capabilities`, which grants only the requested syscall's own capability and
scope-checks API-key callers there. A route-level scope would either have to be one every SDK key
holds — no constraint at all — or break the SDK. A test pins that set by equality so a 47th
ungated route fails CI rather than shipping.

**The safety guard was rewritten, and strengthened.**
`test_every_enforced_scope_is_held_by_an_ordinary_session` required every gate to be satisfiable
by an *ordinary* session. That was right when every gated route was one an ordinary user should
reach, and would now have been an argument for weakening `platform.admin`. It is replaced by
`test_no_route_enforces_a_scope_nobody_can_satisfy`, which is route-derived and allows two
branches: satisfiable by an ordinary session, **or** the route is admin-gated and the scope is one
an admin session derives. A gate failing both is a permission nobody can hold — a 403 the caller
cannot fix — and that is now what fails CI.

**Interaction with `KEY-SCOPE-ESCALATION-1`'s delegation rule.** `POST /platform/keys` now requires
`platform.admin`, and a `platform.admin` holder may grant any scope — so the delegation rule added
in #463 is currently **unreachable over HTTP**: no principal both passes the gate and is bounded
by the rule. It stays anyway, and its test now says so explicitly rather than pretending to be a
route test. The two controls answer different questions — the gate asks *"may you manage keys"*,
the rule asks *"may you grant **this**"* — and if the gate is ever loosened to let a narrower key
manage its own keys, the rule is the only thing standing between that and a `flow.read` key
minting `platform.admin` again.

**The invariant now pinned in CI**, rather than a route count that will drift: the runtime has two
admin dependencies that do different things — `require_admin_principal` demands `platform.admin`
on an API key, while `require_platform_admin_access` admits any key unconditionally — and **no
route may rely on the second one alone**. That distinction is how this survived review; a test
now asserts no route depends on the permissive guard by itself, with the two SDK routes as named
exceptions.

Census across 126 registered routes: **91 scope-gated, 12 admin-gated, 21 public, 2 identity-only**
(was 47 / 56 / 21 / 2).
