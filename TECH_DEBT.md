# Technical Debt

## SDK Extraction (Pending)

`AINDY/sdk/` contains a self-contained package (`aindy-sdk 1.0.0`,
stdlib-only, zero external deps, own `pyproject.toml`, own `tests/`
and `examples/` directories). It was included in `aindy-runtime` during
the monolith split. It does not belong in this repo long-term.

Next action: extract to a standalone `aindy-sdk` repo. SDK test coverage
belongs there, not here. Do not add tests for `AINDY/sdk/` in `aindy-runtime`.

Condition to close: `aindy-sdk` exists as a standalone repo with its own
CI. `AINDY/sdk/` is removed from `aindy-runtime` and from the package
manifest in `pyproject.toml`.
