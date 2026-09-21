### Changed — platform UI: `@aindy/ui-kit` 2.0.0 → 2.1.0 — `request()` reads `X-AINDY-Envelope` (FR-37, #735)

- The kit's `request()` now resolves a body from the header the runtime has stamped since 2.6.0:
  an execution envelope is unwrapped there, and once the backend has been seen stamping, a bare
  `{data: …}` row is never again mistaken for an envelope by shape (FR-19's client half). Against
  a runtime older than 2.6.0 nothing changes. `unwrapEnvelope()`'s signature is unchanged.
- Reaches a container only when a release is cut AND the Dockerfile pin bumped (the SPA ships
  prebuilt in the wheel). Lockfile resolved on Linux by the `Platform Lockfile` workflow.
