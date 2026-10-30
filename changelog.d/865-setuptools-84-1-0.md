### Fixed — `setuptools` 84.0.0 → 84.1.0 for GHSA-xmj3-9gf7-h52w (#865)

- **Low severity, no runtime exposure.** On Windows, a crafted wheel's
  `.dist-info/namespace_packages.txt` could make setuptools' legacy wheel-install path create an
  `__init__.py` outside the target egg directory (the attacker picks the path, not the content).
  The runtime never takes that path, and its images are Linux. The pin was still a known-vulnerable
  version in the lock file.
- The exact pin in `AINDY/requirements.txt`, the `[build-system]` requirement and the CVE floor in
  `pyproject.toml` all move to 84.1.0, so a later downgrade gets caught.
- **Why it was missed:** Dependabot opened alert #27 on 2026-10-27 07:26Z, about eight hours before
  2.28.2 was tagged. pip-audit stayed green, and the release checklist reads pip-audit and the
  release notes, not the Dependabot alert list. Read the alert list at release too.
