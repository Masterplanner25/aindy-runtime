### Added — `AT_MOST_ONCE`: an effect that is never run twice, and refuses rather than degrade (EFFECT-OUTCOME-UNKNOWN-1, DEC-089, #791)

- **Read before upgrading if you call `register_tool` with an unusual `execution_guarantee`.**
  `register_tool` now validates it like `register_syscall`. It must be `AT_LEAST_ONCE`,
  `EXACTLY_ONCE` or `AT_MOST_ONCE` (case-insensitive; `None` still means the default), otherwise
  registration raises `ValueError`. **Why:** it accepted any string, so a typo like
  `"EXACTLY ONCE"` silently registered a tool with no effect gate.
- New guarantee `AT_MOST_ONCE` for syscalls and tools whose counterparty is not transactional. It
  uses the same effect gate as `EXACTLY_ONCE`, with three differences:
  - it engages even when `AINDY_SYSCALL_IDEMPOTENCY` / `AINDY_TOOL_IDEMPOTENCY` are off;
  - it always takes the strict lock;
  - where `EXACTLY_ONCE` degrades to at-least-once (another call holds the effect, the lock wait
    times out, or the gate fails), it **refuses** with `failure_class: "transient"`. Nothing was
    dispatched, so a retry is safe. Watch `aindy_effect_gate_outcomes_total{outcome="refused_at_most_once"}`.
- New counter `aindy_effect_contract_shortfall_total{guarantee, seam}`: an effect that ended
  `unknown` under `EXACTLY_ONCE`, a label that promised completion. Under `AT_MOST_ONCE` an
  `unknown` is a legitimate end and is not counted.
