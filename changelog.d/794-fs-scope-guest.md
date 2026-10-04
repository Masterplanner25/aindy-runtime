### Fixed — a guest's declared filesystem roots and read-only mode are enforced (#794)

- `clamp_to_floor` narrowed the filesystem MODE but passed the declared ROOTS through untouched.
  A spec declaring `scoped` with roots `["/"]` would therefore have reached nodus as
  `allowed_paths=["/"]`, the whole filesystem, under a guest floor whose bound is a per-run scratch
  directory. Roots now narrow like every other field. Under the guest floor any declared root is
  dropped. Under a floor that names roots, only declared roots inside one of them are kept, and
  `..` cannot escape. A dropped root is reported as `visibility.filesystem_roots`. Nothing
  populates a guest's `env_spec` today, so this was latent.
- `readonly` was handled exactly like `scoped`, so a guest that declared read-only could write
  anywhere it could read. It now passes nodus an empty writable set.
- The isolated tool seam is unchanged: there a scoped filesystem still sets only `cwd`
  (FS-SCOPE-1 phase 2).
