---
title: "App and Agent Registration"
api_version: "1.0"
last_verified: "2026-06-14"
status: current
owner: "platform-team"
---

# App and Agent Registration

Two distinct registration models exist in the runtime. **Apps** are router/flow-connected
services. **Agents** are namespaced, memory-connected entities. They are orthogonal — an app
is not an agent, and an agent does not require an app.

The runtime's only concern is registration. How you surface an agent (CLI, web UI, API
consumer, app tab) is your decision, made outside the runtime.

---

## Apps

An app connects to the runtime by registering routers and flows at bootstrap time. Once
registered, the app appears in the **Execution Console → Connected Apps** panel and its
execution pipeline metrics are tracked per-prefix.

### Required call

In your app's bootstrap module (the file loaded by `aindy_plugins.json`), call:

```python
from AINDY.platform_layer.registry import publish_bootstrap_registration

publish_bootstrap_registration("your-app-name")
```

Call this **after** registering routers and flows. Without it, the app runs normally but
does not appear in the Connected Apps panel — silent absence is the footgun.

### Optional additions

```python
from AINDY.platform_layer.registry import (
    publish_bootstrap_registration,
    publish_core_domains,
    register_health_check,
)

publish_bootstrap_registration("your-app-name", dependencies=["other-app"])
publish_core_domains(["social", "search"])          # domains your app owns
register_health_check("your-app-name", my_check_fn) # fn returns {"status": "ok"} or {"status": "degraded"}
```

`publish_core_domains` controls the **Domain Health** panel in the Execution Console.
`register_health_check` adds a health check callable the platform can invoke.

### Verification

After restart, `GET /platform/observability/system` returns `connected_apps` with your
app's entry. The Execution Console shows it under Connected Apps.

---

## Agents

An agent is a named entity with a stable **memory namespace** — a string identifier that
tags every memory node the agent writes. Agents appear in the **Agent Registry** screen,
where their memory stats and recall capability are surfaced.

### System agents (auto-seeded)

The following agents are seeded into the `agents` table on every startup (idempotent):

| Name     | Namespace  | Purpose                                      |
|----------|------------|----------------------------------------------|
| ARM      | `arm`      | Adaptive Reasoning Module — planning agent   |
| Genesis  | `genesis`  | World-building and initialization            |
| Nodus    | `nodus`    | Script execution and flow orchestration      |
| SYLVA    | `sylva`    | Synthesis and language variant               |
| Platform | `platform` | Runtime platform operations                  |
| Runtime  | `runtime`  | Core execution environment                   |
| Memory   | `memory`   | Memory ingestion and retrieval               |

### Registering a custom agent

Use the admin-only endpoint. Idempotent on `memory_namespace` — re-posting updates the
name and description without creating a duplicate.

```http
POST /platform/admin/agents/register
Authorization: Bearer <admin-jwt>
Content-Type: application/json

{
  "name": "LeadGen",
  "memory_namespace": "leadgen",
  "agent_type": "custom",
  "description": "Lead generation and qualification agent."
}
```

Response:

```json
{
  "id": "...",
  "name": "LeadGen",
  "memory_namespace": "leadgen",
  "agent_type": "custom",
  "description": "Lead generation and qualification agent.",
  "is_active": true,
  "owner_user_id": null,
  "created_at": "2026-06-14T...",
  "created": true
}
```

`created: false` means the namespace already existed and was updated.

### Listing registered agents

```http
GET /platform/admin/agents
Authorization: Bearer <admin-jwt>
```

Returns `{ "agents": [...] }`.

### Deactivating an agent

```http
DELETE /platform/admin/agents/{namespace}
Authorization: Bearer <admin-jwt>
```

Soft-delete only. The agent row is marked `is_active=false`; all memory nodes tagged with
that namespace are preserved. The agent disappears from the Agent Registry screen.

### Memory namespace convention

The namespace is a lowercase slug (`leadgen`, `arm`, `my-agent`). All memory nodes
written by the agent should set `source_agent` to this value so the registry's recall and
federated search panels can query them by namespace.

---

## Key distinction

| | Apps | Agents |
|---|---|---|
| Registered via | `publish_bootstrap_registration()` in bootstrap module | `POST /platform/admin/agents/register` |
| Visible in | Execution Console → Connected Apps | Agent Registry |
| Primary identifier | App name string | Memory namespace |
| Runtime concern | Router/flow registration, execution pipeline tracking | Memory namespace, agent type, active state |
| Interface layer | Your choice — not the runtime's concern | Your choice — not the runtime's concern |

---

## Footgun checklist

- **App not appearing in Connected Apps?** Check that `publish_bootstrap_registration` is
  called in the bootstrap module *and* that the bootstrap module is listed in
  `aindy_plugins.json`.
- **Agent not appearing in Agent Registry?** Either call `POST /platform/admin/agents/register`
  or add the agent to `_SYSTEM_AGENTS` in `_bootstrap_system_agents()` in `startup.py`.
- **Domain Health showing nothing?** Call `publish_core_domains(["your-domain"])` in the
  bootstrap module.
- **Health check not running?** Register via `register_health_check("app-name", fn)` where
  `fn` returns `{"status": "ok"}` or `{"status": "degraded", "reason": "..."}`.
