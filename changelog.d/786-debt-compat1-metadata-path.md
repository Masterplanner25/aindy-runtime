### Added — the boot-time compatibility check names where it read a consumer's metadata, and warns when another copy shadows it (DEBT-COMPAT-1, #786)

- Each `compatibility.consumers` record on `GET /api/version` now carries `metadata_path` (the
  `*.dist-info` / `*.egg-info` it read) and `shadowed_metadata` (any other copies of that
  distribution's metadata on `sys.path`). More than one copy logs a WARNING, whatever the status.
  **Why:** the app's dev environment reported an outdated range that the reinstall did not fix.
  The cause was a stale `*.egg-info` in its repo root, which Python read first whenever it ran
  from that directory, and nothing said which copy had been read.
