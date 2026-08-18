### Changed — dependency bumps, grouped (#485)

Nine dependabot PRs taken as one, because `strict: true` branch protection means each
individual merge forces a rebase of the other eight, and because dependabot resolves each
package independently — grouping is necessary but not sufficient, so the set was hand-aligned
and verified to resolve together.

| Package | From | To |
|---|---|---|
| `SQLAlchemy` | 2.0.51 | 2.0.52 |
| `uvicorn` | 0.52.1 | 0.52.3 |
| `Mako` | 1.3.12 | 1.4.1 |
| `regex` | 2026.6.28 | 2026.7.19 |
| `prometheus-fastapi-instrumentator` | 8.0.2 | 8.1.0 |
| `cc` (Rust build-dep) | 1.4.1 | 1.4.3 |
| `uuid` (Rust) | 1.24.0 | 1.24.1 |

Every Python pin moved in **both** `pyproject.toml` and `AINDY/requirements.txt`. CI installs
the second and then `pip install -e . --no-deps`, so a bump applied to only the first is a bump
CI never exercises — which is exactly how `nodus-lang` was tested at 4.1.0 for four months while
the wheel required 4.2.0. `test_dependency_pin_agreement.py` now fails when they disagree.

The Rust bumps are lockfile-only — `Cargo.toml` declares caret ranges — and pull
`find-msvc-tools` 0.1.10 → 0.1.11 as a transitive of `cc`.

**★ The two GitHub Actions bumps were really a consistency defect, and in a workflow added this
release.** Dependabot proposed `actions/checkout` 4 → 7 and `actions/setup-python` 5 → 7. All 34
other usages across the workflows are **SHA-pinned with a version comment**; only
`upgrade-path-guard.yml` used floating `@v4` / `@v5` tags. Rather than bump a tag, both are now
pinned to the same commit SHAs the rest of the repo already uses, so a moved tag cannot change
what runs. No floating action tag remains in any workflow.

**Verified, not assumed:** the full declared set resolves (`pip install --dry-run -r
AINDY/requirements.txt`, and again with the separately-installed MCP extra); the Rust crate
builds `cargo build --locked --release`; and the native scorer was **loaded and exercised** with
`AINDY_REQUIRE_NATIVE_BRIDGE=1` rather than left to skip — a skip reads green, which is
`NATIVE-CI-1`.
