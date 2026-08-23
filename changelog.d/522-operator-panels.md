### Added — Webhooks and Dead-Letter Queue panels in the operator console (FR-21, #522)

The runtime serves an operator SPA at `/platform/`. The app team independently grew a second
one beside it and offered it back rather than keep maintaining two — this adopts the part that
belongs here.

**The gap was two panels, not five.** They named five as "clearly runtime"; the console already
shipped four of them (flow engine, agent registry, admin users, executions). The two it did not
expose were **webhooks** and the **dead-letter queue** — and their check of our served bundle
found zero occurrences of `webhook`, `dlq`, `dead-letter` or `drain`, so these were capabilities
with no operator surface rather than duplicated implementations.

Both drive runtime-owned routes:

| Panel | Routes | Actions |
|---|---|---|
| Webhooks (`/platform/webhooks` in the SPA) | `GET/POST /platform/webhooks`, `DELETE /platform/webhooks/{id}` | list, create, delete |
| Dead-Letter Queue (`/platform/dead-letters`) | `GET /platform/queue/health`, `GET /platform/queue/dead-letters`, `POST …/drain`, `POST …/{job_id}/replay`, `DELETE …/{job_id}` | inspect, replay, delete, drain |

Every destructive action is confirm-gated in place, and both panels are admin-gated client-side
to match the server-side scope (`webhook.manage` / `platform.admin`).

**Note the DLQ ambiguity, because two runtime records share the name:** this panel is the *async
job queue's* dead-letter queue, whose jobs can be replayed because their payload was preserved.
`GET /platform/observability/dead-letter` is a different record — dead-lettered **flow runs** —
and is not what this panel shows.

The SPA's paths for these routes live in `platform/src/api/_routes.js` as `RUNTIME_ROUTES`
rather than in `@aindy/ui-kit`'s `ROUTES`, so a panel does not wait on a ui-kit release. Fold
them in on the next one. `UI_CONTRACT.md` lists them as canonical either way.

**Operators: a UI change reaches no container until a release is cut and the Dockerfile pin is
bumped** — the SPA ships as package data inside the wheel. A running container shows the last
*released* console.
