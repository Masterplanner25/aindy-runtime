---
title: "Security Policy"
last_verified: "2026-05-25"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Security Policy

## Scope

This policy covers vulnerability response for `aindy-runtime` and its declared
dependencies. It applies to self-hosted local-install deployments. Cloud-hosted
deployments will be governed by a separate policy when the cloud runtime is
operational.

## CVE Monitoring

Two mechanisms are active:

**pip-audit (primary)** — runs on every PR and on a weekly schedule
(`.github/workflows/security-audit.yml`). Scans all installed deps against the
OSV (Open Source Vulnerabilities) database. Fails CI on any detected CVE.

**Dependabot security alerts (secondary)** — enabled at the repository level.
Catches transitive deps that pip-audit may miss against a stale lockfile. Creates
automated PRs for security advisories on PyPI and for outdated GitHub Actions
SHA pins.

The auth-adjacent dependencies under active monitoring:
- `bcrypt` — password hashing
- `passlib` — password verification abstraction
- `python-jose` — JWT signing and verification
- Transitives of the above (e.g. `cryptography`, `ecdsa`)

## Response SLA

| Severity | Response window |
|---|---|
| Critical (CVSS 9.0–10.0) | Patch within **7 days** |
| High (CVSS 7.0–8.9) | Patch within **14 days** |
| Medium (CVSS 4.0–6.9) | Address in next minor release |
| Low (CVSS 0.1–3.9) | Address in next major release |

CVSS scores are sourced from NVD/OSV records. If a CVSS score is unavailable,
treat the vulnerability as High until a score is assigned.

## Exempting Known-Acceptable Findings

If pip-audit flags a CVE that has been reviewed and accepted (e.g., does not
affect the runtime's usage pattern, or a fixed version is unavailable), exempt
it by adding `--ignore-vuln <GHSA-ID>` to the pip-audit invocation in
`.github/workflows/security-audit.yml` with a comment explaining the rationale:

```yaml
# GHSA-xxxx-xxxx: affects feature Y which the runtime does not use.
# Accepted 2026-06-01, revisit when fix is available.
- name: Run pip-audit
  run: pip-audit --ignore-vuln GHSA-xxxx-xxxx ...
```

Accepted findings must be documented here under **Accepted Findings**.

## Accepted Findings

None at this time.

## Reporting a Vulnerability

To report a vulnerability privately, contact the platform team directly rather
than opening a public GitHub issue. After triage, a fix will be released within
the SLA window appropriate for the severity, and a public disclosure will follow.
