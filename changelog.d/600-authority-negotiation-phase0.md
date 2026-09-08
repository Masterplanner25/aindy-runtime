### Added — a tool can declare a lower-authority fallback (`AUTHORITY-NEGOTIATION-1` phase 0, #600)

- `register_tool(..., degraded_variant="other_tool")` names a registered tool that may one day be
  attempted when this tool is refused for lack of authority. It sits beside `isolation` and
  `env_spec` on the same declaration surface rather than inventing a fourth vocabulary there.
- **★★ Phase 0 is INERT and that is the whole design.** Nothing consults the field: no denial
  path negotiates, and a tool declaring a variant behaves in every observable respect as it did
  before. This ships the vocabulary so it is reviewable, on the declare-then-enforce sequence that
  let `EXEC-ENV-BIND-1` land in pieces. **The entry stays open** — a declaration nothing reads is
  `ECOGAP-4`'s G4a, and this repo already carries one of those.
- **The tool declares it, never the plan and never the model.** Same rule that makes `env_spec`
  safe: the thing being constrained must not choose its own constraint, or a model that was just
  refused could nominate whatever it liked as its "lower authority" option.
- **Validation splits, of necessity.** Local checks (non-empty, not self-referential) happen in
  the decorator; the three cross-tool rules — target is registered, `caps(fallback)` is a
  **strict** subset of `caps(original)`, target declares no variant of its own — are swept at
  startup. A forward reference is legitimate, and a tool's capability set resolves against
  definitions supplied by plugin providers that load after the import pass, so "at registration"
  was not achievable for those three. The design doc said it was; it is corrected.
- **★ Unevaluable is reported separately from failed.** `_get_capabilities_for_tool` returns `[]`
  both when a tool requires nothing and when the lookup could not run. An empty set for the
  original makes a strict subset impossible, so a naive check would blame an operator's typo for
  an unloaded capability provider — green-check variant 10 in reverse. Chain detection runs first,
  so a structural error is still named correctly where capabilities do not resolve.
- The startup sweep **raises unconditionally**, unlike the syscall check beside it which warns
  outside prod: a malformed declaration is a deterministic coding error, not an environment
  difference. No tool declares a variant today, so the sweep examines nothing and cannot change
  any boot; the startup log prints the count so "none declared" and "never ran" stay distinct.
- Mutation-verified 5/5, including a mutation that simulates phase 1 arriving early — a stray read
  of the field on an execution path fails the inertness guard.
- **★★ The first version of this was not inert, and CI caught what the unit suite could not.**
  `validate_degraded_variants()` opened with `_ensure_tools_loaded()`, which runs
  `_ensure_runtime_agent_defaults()` — a trusted bootstrap registration. That took a platform-only
  boot from `bootstrap_registration_count: 0` to `1` and failed `tests/api/test_version_api.py`.
  The sweep no longer forces loading; it validates the tools registered when it runs, and the
  startup log says how many it examined so a vacuous sweep is visible rather than reassuring.
- **★ Why the unit suite was structurally blind to it:** every test in the new file patches
  `_ensure_tools_loaded` to a no-op so the registry stays isolated — correct for what it isolates,
  and it makes any assertion about *how that dependency is used* impossible. **A fixture that
  neutralises a dependency also neutralises any test of how that dependency is used.** A guard
  that asserts the call does not happen now exists, and re-adding the call fails it.
