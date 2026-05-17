---
title: "Release Staging"
last_verified: "2026-05-17"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Release Staging

This document stages the `aindy-runtime` release flow without publishing a real
external release.

## Version Source

The runtime package version comes from:

- `AINDY/_version.py`

`pyproject.toml` reads that value dynamically through setuptools. `/api/version`
and the runtime compatibility metadata also derive from the same version source.

Current staged version:

- `aindy-runtime 1.0.0`

## Staged Release Contract

Before publishing a new runtime release:

1. bump `AINDY/_version.py`
2. confirm `/api/version` compatibility metadata reflects the new MAJOR series
3. run the runtime contract suite
4. build wheel and sdist artifacts
5. run `twine check` on the built artifacts
6. only publish after the apps repo confirms support for the target runtime

## GitHub Workflow

`.github/workflows/release-staging.yml` is the staged release workflow.

It does not publish to PyPI. It only:

- installs the `release` tooling extra
- verifies runtime version and compatibility metadata
- builds the wheel and sdist
- runs `twine check`
- uploads the artifacts to the workflow run

## Local Staging Commands

```bash
python -m pip install -e .[release] --no-deps --no-build-isolation
python -m pytest \
  tests/unit/test_runtime_only_test_fixtures.py \
  tests/unit/test_platform_only_startup.py \
  tests/unit/test_runtime_packaging.py \
  tests/unit/test_runtime_boundary.py \
  tests/unit/test_runtime_compatibility_metadata.py \
  tests/api/test_version_api.py \
  -m runtime_only -q
python -m build
python -m twine check dist/*
```

## Compatibility Policy

The runtime publishes the recommended apps dependency range through
`/api/version`:

- `compatibility.runtime_package.name`
- `compatibility.runtime_package.version`
- `compatibility.apps_repo_contract.recommended_runtime_requirement`

For `1.0.0`, the staged recommended apps dependency is:

- `aindy-runtime>=1.0,<2.0`

This flow is staged and internally coherent, but it is not a real published
release by itself.
