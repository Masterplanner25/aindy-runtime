---
title: "Scope — Routing a Real Consumer Through the LLM Seam"
api_version: "1.0"
last_verified: "2026-09-03"
status: current
owner: "platform-team"
---

# Scope — routing a real consumer through the LLM seam

**Read this before building `COST-GOVERNOR-1`'s governor half.** It is the reason that work is
not started, and it is not a design objection — the design is settled.

---

## 1. The finding: the seam has no consumer

`COST-GOVERNOR-1`'s meter half shipped in #563: `aindy_llm_tokens_total` and
`aindy_llm_usage_unreadable_total`, recorded in the OpenAI, Azure and Anthropic clients.

**Those counters will read zero indefinitely, because nothing calls those clients.**

| | verified at `672f4c7` |
|---|---|
| `AINDY/` outside `platform_layer` importing an LLM client | **zero** |
| runtime planner backends | `disabled_planner_backend`, `runtime_local_planner_backend` — the latter requires registered *tools*, not an LLM |
| `aindy-apps-monolith` using `get_llm_client*` | **zero** (only hits are inside its own installed `AINDY/` package) |

The app constructs SDK clients directly:

```
apps/agent/agents/planner_anthropic.py:64        anthropic.Anthropic()
apps/arm/services/deepseek/…:576,578             OpenAI(api_key=…) / OpenAI()
apps/rippletrace/services/mention_search.py:15   "Same pattern as planner_anthropic.py"
```

**This is `SUBSTRATE-WITNESS-1` in a new subsystem** — a capability the runtime *has* and nothing
*uses*. It also means a budget governor built at this seam would refuse zero calls while passing
every test written for it: `ROUTE-AST-UNWIRED-1`, knowingly repeated. That is the whole reason to
scope adoption first.

**★ An honest correction to #563's framing.** That PR was described as making spend measurable,
and it does. It was *also* described in a way that implied spend is now measured. It is not, and
will not be until something routes through the seam. The meter is correct and necessary; it is
not yet load-bearing.

---

## 2. The obvious integration does not work, and the reason is structural

`planner_anthropic.py` does not want a chat completion. It wants a **forced tool call**:

```python
message = client.messages.create(
    model=model, max_tokens=4096, system=request.system_prompt or "",
    tools=[_plan_tool(tool_names)],
    tool_choice={"type": "tool", "name": "submit_plan"},
    messages=[{"role": "user", "content": f"Objective: {request.objective}"}],
)
```

The runtime's `LLMClient.chat()` returns **a string** — `_extract_message_text(response)`, which
reads `response.content[...]` text and discards everything else. **Routing the planner through
`chat()` would throw away the tool block it exists to obtain.** The seam is text-shaped; its one
real candidate consumer is structure-shaped.

This is worth stating plainly because "adopt the seam" sounds like a one-line change and is not.

---

## 3. The path that does work

`AnthropicClient.messages_create(...)` returns the **raw response** and takes `**kwargs`, so
`tools` and `tool_choice` pass through untouched. `CircuitBreakerLLMClient.call_method` routes an
arbitrary method through the breaker:

```python
client = get_llm_client("anthropic")            # already breaker-wrapped
message = client.call_method(
    "messages_create",
    model=model, max_tokens=4096, system=request.system_prompt or "",
    messages=[...], tools=[...], tool_choice={...},
)
```

The planner keeps its structured response. No runtime API change is required for this half.

---

## 4. ★★ The gap this exposes, and it must be closed first

**`observe_llm_usage` is wired into `chat()` only.** `messages_create` and OpenAI's
`chat_completion_response` — the raw paths — are **not metered**.

So the integration as described in §3 would route the one real consumer through the seam and
**still measure nothing**. The meter would remain at zero for the same reason as before, with the
adoption work done and no signal to show for it.

**Fix before adoption, not after:** meter the raw response paths too. It is the same one-line
call at the same kind of seam, and the omission is mine — I metered the path the runtime's own
`LLMClient` protocol exposes and not the escape hatch a real caller needs.

**★ Do not meter inside `chat()` *and* rely on `chat()` calling `messages_create`** — it does not
in every client, and double-counting a call is worse than not counting it: a fabricated
measurement is the one failure mode the meter's design explicitly rejects.

---

## 5. The regression this trades for, stated up front

The app's error handling is deliberately provider-specific, and its own comment says why:

```python
except Exception as exc:  # surface the real cause — the runtime wraps this in a generic 500
    if isinstance(exc, anthropic.APIStatusError):
        detail = f"Anthropic API {exc.status_code} ({exc.type}) … [request_id={exc.request_id}]"
```

The runtime wraps: `raise LLMCallError("anthropic messages.create failed") from exc`. **Status
code, error type and request id stop being reachable from `exc` and move to `exc.__cause__`.**

Nothing is lost — `from exc` preserves the chain — but the app's current code reads `exc`
directly and would silently degrade to the generic branch. **That is a real regression in
diagnosability**, and the kind that shows up during an incident rather than in tests.

Two ways out, and this is a decision, not a detail:

1. **The app reads `exc.__cause__`.** Smallest change, keeps the runtime seam narrow.
2. **The runtime stops flattening provider errors** on the raw paths — re-raise the SDK exception
   and let the breaker classify. Better for every future adopter, but it widens what the seam
   promises and touches the circuit-breaker's failure accounting.

Recommendation: **(1) for this integration**, and file (2) as its own question. The
outcome-ambiguity work already argues the runtime destroys a phase distinction by flattening
`httpx.HTTPError` in `outbound_http`; this is the same shape in a second place, and it deserves
one deliberate answer rather than two local ones.

---

## 6. What the governor needs that adoption alone does not give it

The budget scopes are settled — **agent run and tenant, both binding; refuse on breach**. Two
things still have to be true at the call site:

- **Identity must be reachable.** The provider client has none: no tenant, no run. The runtime has
  `syscall_eu_id` / `syscall_trace_id` ContextVars and an `owner_run_id` one that is gated behind
  a default-off flag. **Neither is set by the app when it calls a planner backend**, so the
  governor would see an unattributed call.
- **Unattributed calls need a policy.** Refusing them makes the governor unusable until every
  caller is instrumented; allowing them makes the budget trivially bypassable. `INITIATOR-IDENTITY-1`
  is the same question one layer up and its rule applies: an asserted identity may **constrain,
  never widen** — so the safe default is *allow, and count separately*, with a metric that makes
  the unattributed fraction visible before anyone relies on the cap.

---

## 7. Phasing

| | | |
|---|---|---|
| **0** | Meter the raw response paths (`messages_create`, `chat_completion_response`) | runtime, small, no consumer needed |
| **1** | Route `planner_anthropic.py` through `get_llm_client("anthropic").call_method(...)`, reading `exc.__cause__` for detail | **app repo** |
| **2** | Confirm `aindy_llm_tokens_total` moves in a real deployment | evidence, not code |
| **3** | Thread run/tenant identity to the call site, and count unattributed calls | runtime + app |
| **4** | The governor: reserve → call → reconcile, against a cache, refusing on breach | runtime |

**Phase 0 is worth doing regardless of whether 1–4 happen** — it closes a gap in shipped code.
Phases 1 and 3 are app-repo changes and are not the runtime's to make unilaterally.

---

## 8. What not to do

- **Do not build the governor before phase 2.** A budget enforcer with no traffic passes its tests
  and refuses nothing; this repository has catalogued that shape twice and does not need a third.
- **Do not widen `chat()` to return a rich object.** It is a `Protocol` with four implementations
  and its string return is load-bearing for every existing caller of the embedding-side seam. The
  raw-response methods already exist for callers that need structure.
- **Do not adopt the seam for `deepseek_code_analyzer` or `mention_search` first.** The planner is
  the right first consumer: it is where the token spend actually is, it already has a clean
  injection point (`_make_client`, isolated *for test monkeypatching*), and it is the call a
  budget would most want to refuse.
- **Do not close `COST-GOVERNOR-1` on the meter.** The entry's own argument is that an agent
  cannot bound its own spend credibly — which only holds while the runtime is in the call path.
  Today it is not.
