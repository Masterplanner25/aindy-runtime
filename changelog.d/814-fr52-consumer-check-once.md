### Fixed — every request with plugins loaded paid ~3 s for a version check (`FR-52`, #814)

- DEBT-COMPAT-1's consumer check (2.25.0) ran at the end of every `load_plugins()` call, and the
  registry's getters call `load_plugins()` lazily, 26 times in one warm `GET /memory/nodes`. Each
  run read every installed distribution's metadata. The app measured p50 3,780 ms as shipped vs
  820 ms with the check stubbed out. It now runs once per plugin module set, so a different
  manifest or profile is still checked. `/api/version` reports the same records.
- Our own suites never saw it: runtime-only boot loads no plugins, so `load_plugins()` returned
  before the check. The regression test boots a plugin manifest and counts metadata scans across a
  warm request through the real route (26 before, 0 after).

### Changed — PyJWT is pinned with its `crypto` extra (#814)

- PyJWT's guard against asymmetric key material under an HMAC algorithm returns early when
  `cryptography` is absent. The runtime had `cryptography` through a separate pin; the pin is now
  `PyJWT[crypto]==2.15.1`, so the guard no longer depends on it, and a test asserts it is live.
