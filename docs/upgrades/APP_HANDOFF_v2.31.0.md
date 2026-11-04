---
title: "App Handoff — Runtime v2.31.0"
api_version: "1.0"
last_verified: "2026-11-04"
status: current
owner: "platform-team"
---

# App handoff: runtime v2.31.0

Bump the `constraints.txt` pin to `==2.31.0` (your contract test moves the floor with it), then
**rebuild the image**. No ui-kit release: `@aindy/ui-kit` 2.1.1 stays current.

**What this release is.** `sys.v1.agent.undo` has been a no-op since it shipped, and now it works
(#891, DEC-108..110). **We expect no change on your stack**, based on a search of your repo on
2026-11-04: it has no caller of `sys.v1.agent.undo`, no `compensate=` hook, no plan step declaring
`expects`, and no code reading `VERIFY_FAILED`. Please confirm that holds on your `main`.

There is no route, scope, dependency-pin or schema change. One new env var, default off.

> ## ★ After the rebuild: confirm what you are running
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
> # expect: 2.31.0 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 0. Schema: none

No change under `AINDY/db/models/`. Alembic head stays **`0020`** and the schema contract stays
**`2026-09-20`**. `bootstrap-schema` exits 0 on a current stack. The Upgrade Path Guard therefore
passes trivially on this release; its `negative-control` job is the half that carries meaning here.

## 1. Undo now works (#891)

Undo selected effects by `EffectRecord.execution_id`, and nothing wrote that column, so every run
returned an empty summary. The ledger now links each effect to its execution unit (DEC-108).

- **What undo does now:** deletes the memory nodes a run wrote through `sys.v1.memory.write`, including
  through the `memory.write` agent tool. It refuses and keeps a node whose content, tags or type changed,
  or which has edit history, a link or a trace membership (DEC-109). A node's causal edges are deleted with it.
- **Who can call it:** it has no HTTP route and is off `POST /platform/syscall`. Only in-process code can
  reach it, or an MCP server an operator configured with `AINDY_MCP_SERVER_TOOLS` to expose it.
- **What it cannot see:** effects with no ledger row, including default Nodus guest memory writes, and
  effects recorded before this release. An empty summary does not mean a run had no effects.
- **If you add a `compensate=` hook:** it receives undo's session as `context.metadata["_db"]`, and the
  owner as `context.user_id`. It must flush, and never commit or roll back. See `SYSCALL_SYSTEM.md` §5.

## 2. The Verifier reports and does not delete (DEC-110)

A run that fails post-condition verification now lists `reversible` on its `VERIFY_FAILED` agent event,
beside `reversed` and `irreversible`, and changes nothing. The rollback it described never did anything,
and fixing undo would otherwise have made it delete memory on any failed check.

`AINDY_VERIFY_FAILED_AUTO_UNDO=1` opts in. **Do not set it.** `SOAK_REGISTER.md` row 8 says what evidence
comes first. Only a plan step that declares `expects` can fail verification, and we found none of yours
that do, so your honest report today is "no declaring plan".

## 3. Other things you may notice

- **One more query per reserved effect:** an `execution_units` lookup on the gate's own session, with no
  extra connection. A replay costs nothing extra.
- **`effect_records.execution_id` is now populated** on new rows. `scripts/soak_idempotency_probe.py`
  deletes only its own rows, so it is unaffected.
- **`register_tool` gains an optional `effect_syscall`.** Use it only for a tool that just wraps a syscall.
  Its own ledger row then stays unlinked, so undo sees that effect once.

`publish.yml` job names and the `production` gate are unchanged, so your release watcher is unaffected.

## 4. Asks

1. Bump the pin to `==2.31.0`, rebuild, and run the in-container check above.
2. Confirm our search: no caller of `sys.v1.agent.undo`, no `compensate=` hook, no plan declaring `expects`.
   Report anything we missed in your `RUNTIME_2_31_0_UPGRADE.md`.
