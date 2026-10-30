### Fixed — 2.29.0's notes said setuptools 84.1.0 reaches every install; it reaches only `[security]` (#869)

- **Correction to the published 2.29.0 entry (2026-10-29).** The operator note "`setuptools` 84.1.0
  clears two low-severity, Windows-only advisories" holds only for installs that take the
  `security` extra. `pyproject.toml` declares `setuptools>=84.1.0` there and nowhere else, and the
  exact `==84.1.0` in `AINDY/requirements.txt` is CI's lock file. A plain `aindy-runtime` install
  (the runtime's own `Dockerfile` included) keeps the base image's setuptools. The app's container
  showed 79.0.1, which also predates the CVE-2026-59890 floor. Install `aindy-runtime[security]`
  to take it. Runtime exposure is unchanged (none on Linux); only what an image scanner reports
  differs. `APP_HANDOFF_v2.29.0.md` §2 carries the same dated correction. Reported by the app
  while it prepared its adoption.
