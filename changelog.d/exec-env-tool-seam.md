### Added — an isolated tool can declare what its worker may see (`EXEC-ENV-BIND-1` phase 3)

- `register_tool(..., env_spec={...})` lets a tool declare its execution environment. The
  worker subprocess is then spawned with that environment applied — an `env` allow-list, a scoped
  working directory, a shorter wall clock.
- **Why this was open:** `TOOL-SEAM-ISOLATION-1` moved a declared tool *out of the process* but
  did not narrow what that process can **see**. The worker was spawned with no `env=` and no
  `cwd=`, so it inherited the **entire server environment** — `SECRET_KEY`, `DATABASE_URL`, every
  provider API key — and the server's working directory, which holds `alembic/` in Docker and
  `AINDY/.env` in dev. Isolation was process-level and never visibility-level.
- **Nothing changes for a tool that declares nothing.** The tool floor is today's behaviour
  written down, and it produces no spawn arguments at all — same environment, same working
  directory, same timeout. This is deliberately not the guest floor: a tool is first-party code
  an operator registered, and clamping tools to the guest's floor would break every one that
  legitimately reads a credential for the service it calls.
- **A declaration can only ever narrow.** A tool cannot widen its own environment by declaring a
  permissive descriptor, and a declared wall clock can shorten the worker's leash but never
  lengthen it.
- **A malformed declaration is refused at registration**, not at first call — an operator sees it
  at startup rather than the first time someone happens to invoke that tool.
- **What this still cannot enforce:** `authority.network` and `authority.subprocess`. A bare
  subprocess shares the host's network namespace and can spawn children, and no spawn argument
  changes that — which is why this tier reports `insecure-dev` and why the container runner
  exists. The record says what was achieved, not what was asked for.
