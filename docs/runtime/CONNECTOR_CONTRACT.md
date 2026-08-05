---
title: "Connector Registration + Authorized Outbound Contract (FR-1)"
api_version: "1.0"
last_verified: "2026-08-05"
status: current
owner: "platform-team"
---
# Connector Registration + Authorized Outbound Contract (FR-1)

> **Status: implemented.** Runtime counterpart to the apps-monolith request
> `MASTERPLAN-CONNECTOR-RUNTIME-1` (handoff `APP-FR-1`). Apps register outbound
> integrations via a runtime hook instead of a hardcoded `if/elif` ladder, and every
> outbound call flows through a capability-enforced boundary that reuses the same
> authorization stack `execute_tool` applies to agent tools.

---

## 1. What FR-1 provides

| Piece | Symbol | Location |
|---|---|---|
| Registration hook | `register_connector(connector_type, handler, *, capability=None, description=None, overwrite=False)` | `AINDY/platform_layer/registry.py` |
| Lookup | `get_connector` / `iter_connectors` | `AINDY/platform_layer/registry.py` |
| Runtime dispatch | `dispatch_connector(connector_type, action, *, user_id, db, metadata)` | `AINDY/platform_layer/connector_service.py` |
| Per-dispatch context | `ConnectorContext` (`.call(...)`, `.capability`, `.user_id`, `.db`) | `AINDY/platform_layer/connector_service.py` |
| Enforced outbound primitive | `authorized_external_call(...)` + `OutboundCallDenied` | `AINDY/platform_layer/external_call_service.py` |
| Shared HTTP client | `outbound_request(...)` (retry + circuit-breaking) | `AINDY/platform_layer/outbound_http.py` |

Symmetric to `register_job` / `register_flow`: a connector is registered at bootstrap and
dispatched by the runtime, so multiple apps can contribute connector types.

## 2. The handler contract

```python
from AINDY.platform_layer.registry import register_connector

def send_email(action: dict, ctx) -> dict:
    # ctx.call is authorized_external_call pre-bound to this connector's capability.
    return ctx.call(
        service_name="email",
        operation=lambda: _smtp_send(action["to"], action["subject"], action["body"]),
        action=action,               # recipient/domain allowlist inspects this
        endpoint="smtp://mail.example.com",
    )

register_connector("email", send_email, capability="outbound.email")
```

- The handler is invoked as `handler(action, ctx)`. `action` is the connector action dict;
  `ctx` is a `ConnectorContext`.
- `capability` is the capability that gates this connector's outbound I/O. When omitted it
  defaults to **`outbound.<connector_type>`**.
- The handler may also call `resolve_secret(name)` directly — it is gated by the same
  capability via the ambient scope `dispatch_connector` installs, so credentials are
  fetched just-in-time and never persisted or returned in the trace envelope.

## 3. The enforcement stack (all opt-in, vacuous until configured)

`authorized_external_call` — invoked per outbound call via `ctx.call` or `outbound_request`
— layers, in order:

1. **Recipient / domain allowlist** — `enforce_capability_policy([capability], action)`
   denies the call when the action carries an email/host outside the capability's
   `CapabilityPolicy` allowlist. Register via `AINDY_CAPABILITY_POLICIES`
   (`{"outbound.webhook": {"domains": ["hooks.slack.com"]}}`).
2. **Rate limit** — `enforce_capability_rate` records one hit per logical call against a
   `capability × user_id` fixed-window counter (Redis when available, in-memory fallback).
   Register via the same policy JSON (`"rate": "30/minute"`).
3. **Socket-level egress guard** — for the duration of the operation the process's DNS
   resolution is pinned to the capability's domain allowlist when
   `AINDY_EGRESS_ENFORCEMENT` is on, catching runtime-built URLs static arg inspection
   misses. `dispatch_connector` also installs this around the whole handler, so a connector
   that bypasses `ctx.call` and issues raw `urllib`/`smtplib` I/O is still guarded.
4. **JIT credential vaulting** — the operation runs inside `capability_scope([capability])`,
   so `resolve_secret(name)` is gated by the capability (lock a secret to it via
   `AINDY_SECRET_SCOPES`).
5. **Observability** — the real op is wrapped in `perform_external_call`
   (`external.call.started|completed|failed` events + timing).

A denial at steps 1–2 raises `OutboundCallDenied` **before any network I/O**;
`dispatch_connector` maps it to `{"success": False, "denied": True, "error": …}`.

**Registering a connector changes dispatch routing only — behavior is unchanged until an
operator registers a policy / secret scope / enables egress.** This matches the runtime
convention that new enforcement seams are inert by default.

## 4. Shared HTTP client (`outbound_request`)

`outbound_request(method, url, *, service_name, capability, …)` is the resilient HTTP entry
point that replaces app-side raw `urllib`. It routes through `authorized_external_call`
(so authorization is enforced once, outside the retry loop) and adds:

- **Retry with exponential backoff** on transport errors and retryable statuses
  (408/429/500/502/503/504), bounded by `max_retries`.
- **Per-service circuit breaker** (`CircuitBreaker(name="outbound:<service>")`): after
  `failure_threshold` consecutive failures the circuit opens and calls fail fast with
  `CircuitOpenError` until the recovery timeout elapses.

## 5. Dispatch envelope

`dispatch_connector` never raises — it returns a normalized envelope:

| Outcome | Envelope |
|---|---|
| Success | `{"success": True, "result": <handler return>, "error": None}` |
| Unknown connector | `{"success": False, "result": None, "error": "connector 'x' is not registered"}` |
| Policy/rate denial | `{"success": False, "result": None, "error": …, "denied": True}` |
| Handler exception | `{"success": False, "result": None, "error": <str(exc)>}` |

## 5a. Reserved type: `transactional_email` (runtime-owned mail)

**`transactional_email` is dispatched by the runtime itself, not by an app.** It carries
password-reset and email-verification mail (FR-6). An app does not need to register it —
if nothing is registered, the runtime's own SMTP (`AINDY_SMTP_*`) carries the mail. Register
it only to take delivery over deliberately.

**This type is separate from `email` on purpose (FR-9).** In 2.0.0 the runtime dispatched
transactional mail to `email`, the same type apps register for user-authored automations.
Registering one silently opted an app into carrying auth-critical mail in a shape it had
never been told about, and because a registered-connector failure deliberately does **not**
fall back to SMTP, a shape mismatch meant `/auth/register` returned `202` while no
verification mail could ever be sent. Registration looked healthy; no account could complete
signup. The two senders share nothing but the word "email", so they now have separate types.

If you do register it, this is the action shape — stable, and the only shape dispatched:

```python
{"type": "send", "to": "<recipient>", "subject": "<subject>", "body": "<plain text>"}
```

Branch on `action["type"]`; treat any unrecognised value as unhandled rather than assuming
`send`. Return the normal handler envelope. **A failure is final** — the runtime logs it at
ERROR and does not retry or fall back, because silently rerouting mail to a channel the
operator did not choose, exactly when the chosen one is broken, is worse than not sending.
If you cannot deliver reliably, do not register this type; leave it to runtime SMTP.

## 6. App-side adoption (the contract this satisfies)

The app deletes its `if/elif` connector ladder in
`apps/automation/services/automation_execution_service.py::execute_automation_action` and
registers each connector via `register_connector`; outbound calls become
authorized / allow-listed / rate-limited by the runtime, and credentials resolve from a
runtime broker rather than app config. Delivery behavior is unchanged — this is enforcement
+ pluggability. See `APP-FR-1` in `TECH_DEBT.md`.
