---
title: "Security Policy"
last_verified: "2026-10-08"
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

For current support interpretation, treat the runtime first as a trusted-internal
runtime platform rather than as a broadly hardened external extension platform.

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
- `PyJWT` — JWT signing and verification (replaced `python-jose` 2026-10-08)
- Transitives of the above (e.g. `cryptography`)

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

None currently.

### Closed by replacement — python-jose and ecdsa (2026-10-08)
`CVE-2026-85394` / `GHSA-3qf3-8w2g-rqmx` (critical, python-jose <= 3.5.0, no fix released): the
HMAC algorithm-confusion guard accepts a DER-encoded public key, so a token forged with a
service's public key verifies when `algorithms` is not pinned — an incomplete fix for
CVE-2024-33663. The runtime was not exploitable (every verifying decode pins
`algorithms=["HS256"]`; every key is a symmetric secret), but with no fix release the advisory
could only be accepted indefinitely, and this was jose's second miss in the same guard. The JWT
library was replaced with PyJWT instead. Tokens are wire-identical — sessions and emailed links
minted by jose verify unchanged (`tests/unit/test_jwt_library_migration.py` holds jose-minted
fixtures).

`CVE-2024-23342` (ecdsa Minerva timing attack, no fix released; accepted 2026-05-25, Dependabot
alerts #4 and #11 dismissed `not_used` 2026-07-07) closed with it: ecdsa arrived only as a
dependency of python-jose. `rsa` and `pyasn1` left the install the same way. The exemption was
deleted from `security-audit.yml` in the same change.

### Closed by absence — the nltk findings (2026-10-01)
`PYSEC-2026-97`, `GHSA-rf74-v2fm-23pw`, `PYSEC-2026-597` and `PYSEC-2026-3740` were accepted
(2026-05-25 to 2026-09-02) because nltk arrived only as a transitive dependency of textstat and no
affected API was reachable. Both pins were removed in 2.25.0 (`PACK-DEBT-6`): nothing in the
runtime imported either, and the app that did now declares them itself. The exemptions were
deleted from `security-audit.yml` in the same change. An advisory with no released fix closes this
way or not at all.

## Reporting a Vulnerability

To report a vulnerability privately, contact the platform team directly rather
than opening a public GitHub issue. After triage, a fix will be released within
the SLA window appropriate for the severity, and a public disclosure will follow.
