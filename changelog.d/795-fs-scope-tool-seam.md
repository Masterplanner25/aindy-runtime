### Added — an isolated tool's declared filesystem scope is enforced in its worker (#795)

- Before this, a tool declaring `visibility.filesystem = scoped | readonly | none` got only a
  scratch working directory. `cwd` is a default location, not a boundary, so its worker could
  still open any path the OS allowed. The declaration was recorded and never applied.
- The parent now resolves the scope once and sends it to the worker. The worker installs it as a
  Python audit hook before the plugin stack loads, the same way egress works:
  - `scoped` reads and writes its roots and scratch root;
  - `readonly` reads them and writes nothing;
  - `none` reads no file;
  - the import path stays readable.
- The envelope carries `filesystem: {mode, mechanism}`, using the mechanism the worker reported
  (`audit_hook:worker`, or `none`).
- This is enforcement for the tool's own Python file I/O, not a kernel boundary. C extensions,
  `ctypes` and child processes bypass it, and the deployment's assurance does not change.
- An in-process tool cannot be scoped. `register_tool` now warns when a tool declares a
  filesystem scope without `isolation`.
- A `readonly` tool's worker now also starts in the scratch directory, not the server's
  working directory.
- It applies only to tools that declare a filesystem scope. No tool in the app declares one.
