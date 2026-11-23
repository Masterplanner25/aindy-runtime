---
title: "App Handoff — Runtime v2.41.0"
api_version: "1.0"
last_verified: "2026-11-23"
status: current
owner: "platform-team"
---

# App handoff: runtime v2.41.0

**No schema step**: Alembic stays at `0022` and the schema contract at `2026-11-09`. No ui-kit or aindy-sdk release.
**nodus-lang 5.16.0 → 5.17.0** (§1) and a fix to how Nodus guests import modules (§2). Nothing to do on upgrade beyond
the pin.

You are on **2.38.0**; 2.39.0 and 2.40.0 are still unadopted, and their asks stand (§3). Going straight to 2.41.0 is
fine: read `APP_HANDOFF_v2.40.0.md` §1 first, because the `openai` 3 move is the one with something to check (your
own `OpenAI(...)` in `probe_openai`, and any `respx` mock of OpenAI).

Bump the `constraints.txt` pin to `==2.41.0` (your contract test moves the floor with it). You don't pin `nodus-lang`
yourselves, so the runtime's `==5.17.0` decides it. Then **rebuild the image** (`mcp` shares it, so recreate both).

> ## ★ After the rebuild: confirm what you are running
>
> ```bash
> docker exec <api-container> python -c "import os, AINDY, AINDY._version as v, importlib.metadata as m; print(v.__version__, m.version('nodus-lang'), m.version('openai'), os.path.isfile(os.path.join(AINDY.__path__[0], 'nodus', 'stdlib', 'memory.nd')))"
> # expect: 2.41.0 5.17.0 3.57.0 True
> ```

---

## 1. nodus-lang 5.17.0 (#994)

**A guest's time limit and memory ceiling now hold inside stdlib and imported module calls.** Through 5.16.0 each
call into a module (`import "std:strings"`, …) ran on a child VM that kept the guest's permissions but none of its
bounds, so `AINDY_NODUS_MAX_EXECUTION_MS` and the guest memory ceiling did not reach work done there. A script that
spends a long time in such calls can now stop with `Execution timed out` where it used to finish. Its work was always
that large; it was not measured.

**Your scripts:** we read the five `.nd` files under `apps/analytics/nodus/` and `apps/rippletrace/nodus/`. None
imports a module, so the new accounting does not reach them. The rest of 5.17.0 doesn't touch them either:

- `let` is now lexical, a parameter keeps its argument when a later block reuses its name, and a `return` from inside
  a `for … in` loop or a `match` no longer leaves values on the caller's stack. Each was a nodus bug that silently gave
  `nil` or a wrong value. Your scripts use none of these shapes.
- `register_function` refuses four new builtin names (`substring`, `str_substring`, `str_span`, `str_span_until`). If
  any app code registers a host function under one of them, rename it.
- The bytecode optimizer runs again (it was dead since nodus v1.0); cached bytecode recompiles once.

## 2. Guest imports in a non-root container, and `import "memory"` (NODUS-STDLIB-ROOT-1, DEC-139, #995)

Two defects in every release up to 2.40.0, neither of which reached you:

- **In the runtime's own Docker image every guest `import` failed** (`[Errno 13] Permission denied:
  …/AINDY/nodus/stdlib/.nodus/cache`). nodus writes a bytecode cache under the guest's project root on every import,
  and that root was the installed package, read-only for a non-root user. Your container runs as root, so the write
  succeeded, into site-packages. Each worker process now uses its own temp directory instead. **If you ever move the
  container to a non-root user, this is what made that safe.**
- **`import "memory"` in an installed runtime never reached the runtime's memory module.** `memory.nd` was never
  packaged, so the import fell through to nodus's own `std:memory`, a local key/value store, and the calls in
  `NODUS_DEVELOPER_GUIDE.md` failed with arity errors. It ships now; the confirm line's last field checks it.

## 3. Your register

- **2.39.0 / FR-55:** still waiting on your ported-loop report (`APP_HANDOFF_v2.39.0.md` §1).
- **2.40.0 / the trace question:** closes on your re-read of `d534a3fd…` scoped to its owner (`APP_HANDOFF_v2.40.0.md`
  §2; expected 584 nodes, 583 edges).
- **2.40.0 / FR-6:** please mark it closed (`APP_HANDOFF_v2.40.0.md` §3).
- **Nothing new is asked in this release** beyond the pin, the rebuild and the confirm line.
