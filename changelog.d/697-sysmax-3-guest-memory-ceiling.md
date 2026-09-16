### Added — `AINDY_NODUS_MAX_MEMORY_MB`: a per-execution memory ceiling for the Nodus guest VM (`SYSMAX-3` guest half, #697)

- New operator setting, unset by default (no ceiling — nothing changes until it is set). When
  set, every guest execution inherits `resources.memory_bytes` on the guest floor, translated
  to nodus-lang 5.13's `max_memory_mb`: the VM reads the worker's RSS when the script starts
  and fails the run with a `sandbox` error (`Memory limit exceeded: …`) once the process has
  grown past the budget. A per-execution `env_spec` may narrow the ceiling, never widen it.
- What it is and is not: it bounds memory **growth over the run**, polled by the VM — not a
  single large allocation, which only an OS-level limit (ulimit / cgroup / container cap)
  prevents. It applies to the guest path only; agent runs and flow nodes in the API process
  remain unbounded, so `SYSMAX-3` stays open.
- Fails closed: on a host where the VM cannot read RSS, a declared ceiling makes the run
  fail with `declared memory ceiling cannot be enforced on this host …` rather than run
  unbounded. With nothing declared, such a host is unaffected.
- `enforced_resources(spec, guest=True)` now lists `memory_bytes`; the default path's list is
  unchanged (`GUEST_RESOURCES_ENFORCED` vs `RESOURCES_ENFORCED`).
