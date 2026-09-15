### Changed — Boot Smoke retries the PyPI install instead of failing on index lag (#672)

On the `v2.15.0` tag, `publish.yml`'s Boot Smoke on the published wheel failed with
`No matching distribution found for aindy-runtime==2.15.0` seconds after the upload, and the
GitHub release was skipped; a `--failed` rerun passed. The smoke's gate probes PyPI's JSON API
at the origin (200 the moment the upload lands) while `pip install` resolves through the simple
index behind PyPI's CDN, which lagged by minutes — two caches, two answers. The install step in
`smoke-postgres.yml` now retries up to 10× at 30 s with `--no-cache-dir` and fails loudly if the
version never becomes resolvable. **What a green means is unchanged; what a red means is
narrower:** it is no longer "the CDN was slow". Nothing in the package changed.
