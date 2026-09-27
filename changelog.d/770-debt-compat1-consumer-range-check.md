### Added — the runtime warns at boot when a plugin's declared `aindy-runtime` range excludes it (DEBT-COMPAT-1, DEC-080, #770)

- `load_plugins` now finds the installed distribution that owns each plugin module and compares
  its declared `aindy-runtime` requirement with the running version. It logs a WARNING when the
  runtime is outside the range, when the distribution declares no dependency on the runtime, or
  when the range has no upper bound. **Why:** the runtime published its compatibility policy on
  `/api/version` and nothing read it. A consumer ran a major behind, under the advertised floor,
  and a dev environment ran five releases older than the app's own declared range while every
  suite passed.
- **Warns, never refuses**, and never raises. The runtime's own modules are skipped. Each warning
  prints `AINDY.__path__` beside the version.
- New additive field `compatibility.consumers` on `GET /api/version`, listing each distribution
  with its declared range and status.
- The check reads the metadata pip wrote at the last install, not `pyproject.toml`. A raised floor
  is seen after the next `pip install`.
