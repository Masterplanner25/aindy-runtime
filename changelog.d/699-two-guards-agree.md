### Changed — the two tenant-path guards and the two admin guards each share one rule (#699)

- `TenantContext.validate_memory_path` and `memory_address_space.validate_tenant_path` now
  both call `kernel.tenant_context.tenant_owns_memory_path`. Visible differences: the exact
  tenant root `/memory/{tenant}` is inside the namespace for both (the kernel guard used to
  refuse it); paths are slash-normalised before comparison for both; an empty tenant owns no
  path for both (the kernel guard used to accept `/memory//…` on a raw prefix match). The
  kernel guard had no production caller, so no served behaviour changes; MAS's answers are
  unchanged for every well-formed path.
- `require_admin_principal` and `require_platform_admin_access` share `is_operator_principal`
  (a session is an operator iff `is_admin`; an API key iff it carries `platform.admin`).
  Behaviour unchanged. The tree gate's docstring now states, for the first time, that it
  admits any API key by design because every `/platform` route enforces its own scope —
  use `require_admin_principal` for an operator check. Closes `KEY-SCOPE-ESCALATION-1`.
