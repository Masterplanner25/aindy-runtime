### Added — an on-demand latency floor (#798)

- **Why.** Nothing in the test suite bounded how long anything takes, so "soak, then flip" had no
  timing evidence to stand on (PERF-BASELINE-1).
- **What.** `tests/integration/test_latency_floor.py` measures, on the path the flags flip, the
  median time of one gated `EXACTLY_ONCE` effect and of one agent step on each backend. Each
  bound sits about 10x above the local measurement: 28.8 ms per effect; 212 ms per step on
  `agent_flow` and 352 ms on `nodus_vm`. Each bound has a control that injects a delay of its
  size and must trip it.
- **On demand only.** The tests skip unless `AINDY_LATENCY_FLOOR=1`. The new `Latency Floor`
  workflow runs only when started by hand and is not a required check. A per-PR wall-clock gate
  on shared CI is the flake this entry was written to avoid.
