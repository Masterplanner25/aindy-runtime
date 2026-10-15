---
title: "App Handoff — Runtime v2.28.0"
api_version: "1.0"
last_verified: "2026-10-14"
status: current
owner: "platform-team"
---

# App handoff: runtime v2.28.0

Pin bump `constraints.txt` `==2.27.0` → `==2.28.0` (your contract test moves the floor with it).
No ui-kit release: `@aindy/ui-kit` 2.1.1 stays current.

**What you need to do:** bump the pin and rebuild. There is no migration. Then decide what to do
with `scripts/mcp_server.py` (§2), and start reading one new counter (§3).

**What this release is.** It answers **FR-53**, all three parts, filed 2026-10-14. A payload `user_id`
written outside the runtime can no longer choose the user a syscall acts on, and
`aindy-runtime mcp-server` now serves your syscalls. The rest is dependency bumps. One of them, the
OpenTelemetry 1.45.1 fix, is reachable from any client (§4).

> ## ★ Confirm what you are actually running: in the container, printing the path.
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
> # expect: 2.28.0 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 0. Schema: none

No change under `AINDY/db/models/`. Alembic head stays **`0020`**, schema contract **`2026-09-20`**.
`bootstrap-schema` exits 0 on a current stack. The `Upgrade Path Guard` passes **trivially** on a
release like this, because there is no drift to find. Its negative control is the half that carries
meaning.

## 1. FR-53 (b): a payload `user_id` may repeat the caller, never choose another user (#831, DEC-099)

**Wider than you filed.** You found it on the MCP path. The **Nodus `sys()` seam had it too**:
`dispatch_worker_syscall` used `setdefault("user_id", user_id)`, which *kept* a script-supplied id.
A runtime test pinned that as intended since FR-5b. So a `.nd` script could name another user to
any syscall of yours that reads `payload["user_id"]`. We counted 55 such handler sites in your
source on 2026-10-14.

**Now**, at the three places where someone outside the runtime writes the payload:

| Transport | Payload `user_id` absent | Same as the caller | Anyone else |
|---|---|---|---|
| MCP tool call | filled with the caller's | passes | **refused** before an execution unit is claimed: `TENANT_VIOLATION`, `failure_class: permission` |
| Nodus `sys()` | filled (as before) | passes | **refused**, same error, never dispatched |
| `POST /platform/syscall` | filled | passes | **403** |

"Same" compares UUIDs in any spelling. The id is **refused, never overwritten**, so a wrong
assumption in a caller surfaces instead of running against a different user.

**What it means for you:**
- **Your `reasoning_apply_v1.nd`** passes `input_payload["args"]` = `{"user_id": <run owner>}`,
  which is the caller's own id, so it keeps working. It is your only `sys()` script (checked
  2026-10-14).
- **Your MCP allowlist can now include syscalls that read `payload["user_id"]`.** An outside AI that
  sends no id gets the caller's, and one that sends another id is refused. Your note in
  `scripts/mcp_server.py` ("They are not exposed") and the source check in `test_mcp_read_access.py`
  were the right call before this release. On 2.28.0 they are a choice, not a necessity.
- **In-process dispatch is NOT checked yet.** Your own code calling `get_dispatcher().dispatch(...)`
  behaves exactly as before. See §3 for how that changes.

## 2. FR-53 (a): `aindy-runtime mcp-server` loads your plugins (#831, DEC-101)

The command now runs `load_plugins()` for the manifest selected exactly as at boot
(`AINDY_PLUGIN_MANIFEST` / `AINDY_BOOT_PROFILE`), before it resolves `AINDY_MCP_SERVER_TOOLS`.
Plugin output goes to stderr during the load, at both the `sys.stdout` and fd-1 level, so a stray
`print` or C-extension write cannot corrupt the stdio channel.

- **Your launcher keeps working unchanged.** It calls `load_plugins()` and then `serve_stdio()`,
  which loads again. We ran exactly that sequence: the second load is harmless and the syscall is
  served.
- **You can now retire it**, or keep it for what it adds: the read-only allowlist and the refusal
  when `AINDY_MCP_SERVER_ALLOW_WRITES` is set. The plain equivalent is
  `AINDY_MCP_SERVER_TOOLS=<your list> aindy-runtime mcp-server --transport stdio`.
- **A plugin that fails to load now STOPS the server**, the same way it stops your api's boot. The
  owner chose this over warn-and-continue on 2026-10-14. If you ever need an MCP server with runtime
  tools only while a plugin is broken, run it with **`AINDY_BOOT_PROFILE=platform-only`**. Your
  `aindy_plugins.json` is profile-format, so that profile loads nothing.

**FR-53 (c), the two smaller points:**
- **Capability by name is deliberate.** MCP and `sys()` grant exactly
  `_infer_dispatch_capability(name)`, so `sys.v1.work.context` gets `work.context`. A syscall
  registered under `masterplan.read` is refused there. It is now written down in
  `register_syscall`'s docstring. Name the syscall so the inferred capability is the one it
  declares.
- **The `nodus_<domain>_<action>` tool names** come from `nodus-mcp`'s `syscall_entry_to_tool`, not
  from this repo. That is a request for nodus-mcp, if you want it made.

## 3. New counter: `aindy_payload_identity_mismatch_total{syscall}` (#832, DEC-100)

The dispatcher now **observes**, and does not refuse, any dispatch whose payload `user_id` differs
from its context's caller. It logs a WARNING (`payload user_id differs from the caller … observed,
not refused`) and increments this counter by `syscall`. The call runs unchanged. Transports refuse
before dispatch, so **every count is in-process code.** That is yours, since your ~60 dispatch call
sites are the ones nobody has audited for a system-context call made on behalf of another user.

**Why this matters to you:** zero across a window of your real traffic is the evidence for making
the dispatcher refuse too (`PAYLOAD-IDENTITY-DISPATCH-1`). A non-zero label names a call site,
which is either a bug to fix or a real on-behalf case to design for, never a payload key. The
refusal would ship as its own release with a `00-` changelog note.

**Ask:** read it on `/metrics/` after a week of normal use, the same way as the FR-49 window. The
counter resets when the api is recreated, so read it before each rebuild.

## 4. Dependency bumps (#830)

Runtime pins: `pydantic` 2.14.0, `uvicorn` 0.54.0, `jiter` 0.17.0, OpenTelemetry
`api`/`sdk`/`exporter-otlp-proto-grpc` 1.45.1 + `instrumentation-fastapi` 0.66b1 (moved together).
Release extra: `build` 1.6.1. Platform UI (dev): `vite` 8.3.4, `@vitejs/plugin-react` 6.1.2,
`postcss` 8.5.29. Native crate (build-time only): `cc` 1.7.0, `uuid` 1.28.0.

**Release notes read for each runtime pin that moved:**

- **★ OpenTelemetry 1.45.1: reachable.** It fixes *quadratic-time parsing of the `tracestate`
  header* and discards headers longer than 8,192 characters. The runtime always runs
  `FastAPIInstrumentor.instrument_app(app)` (`startup.py`), so on 2.27.0 any client could send
  that header, before authentication. Also fixed: a thread leak in SDK resource detection.
- **pydantic 2.14.0:** it now rejects newlines in `NameEmail`, which neither repo uses (checked).
  It no longer mutates `model_config`, and it drops Python 3.9, which doesn't affect us (`>=3.11`).
  The rest are validation-robustness fixes (`Decimal`, `ZoneInfo`, `Fraction` exponents).
- **uvicorn 0.54.0:** only HTTP/2 trailers and `103 Early Hints`, both on its experimental, opt-in
  HTTP/2 path. You don't use it, so nothing changes.
- **jiter 0.17.0:** speed work (SIMD) and less `unsafe`. No API change.

## For the record

- **Nothing owed from earlier releases.** Your 2.27.0 adoption (#451) answered the 2.26.0
  `RESOURCE_LIMIT_EXCEEDED` ask (0 hits across three `nodus_vm` runs) and FR-52 (p50 715 ms).
- **Next runtime release:** `AINDY_MEMORY_RECALL_OWN_SESSION` default ON (DEC-083 met by your
  10-09 readout), in its own release. You already run it on, so it changes nothing for you.
- **This release is the first your release-handoff watcher follows** (your #471). `publish.yml`'s
  trigger, job names, `production` gate and this file's path are unchanged from 2.27.0, as confirmed
  to your session on 2026-10-14.

## Asks, in one place

1. Pin `==2.28.0`, rebuild, and confirm the version and path with the command at the top.
2. Decide on `scripts/mcp_server.py`: keep it, or retire it for `aindy-runtime mcp-server` (§2).
   Revisit which payload-`user_id` syscalls you're willing to expose over MCP (§1).
3. Read `aindy_payload_identity_mismatch_total` after a week of normal use and report it,
   including any `syscall` label that is non-zero (§3).
4. Mark FR-53 closed in your register.
