---
title: "Security Policy"
last_verified: "2026-07-02"
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

### CVE-2024-23342 — ecdsa Minerva timing attack
- **Package:** ecdsa (transitive dep of python-jose)
- **Fix version:** None released
- **Accepted:** 2026-05-25
- **Rationale:** Not reachable. The runtime uses HS256 (HMAC-SHA256) signing for all JWTs (`ALGORITHM = "HS256"` in `AINDY/services/auth_service.py`). The ecdsa package is pulled in transitively by python-jose but EC key operations are never invoked. A Minerva timing attack requires repeated access to an EC signing oracle, which does not exist in this codebase.
- **Reopen trigger:** Any addition of ECDSA/ES256 JWT signing or any direct ecdsa import. A fix release from the ecdsa maintainers would also allow removing this exemption.

### PYSEC-2026-97 — nltk filestring() path traversal
- **Package:** nltk (transitive dep of textstat)
- **Fix version:** None released
- **Accepted:** 2026-05-25
- **Rationale:** Not reachable. nltk is a transitive dependency of textstat; the runtime never imports nltk directly and never calls `nltk.util.filestring()`.
- **Reopen trigger:** A fix release from the nltk maintainers, or any direct nltk import added to the codebase.

### GHSA-rf74-v2fm-23pw — nltk JSONTaggedDecoder recursion DoS
- **Package:** nltk (transitive dep of textstat)
- **Fix version:** None released
- **Accepted:** 2026-05-25
- **Rationale:** Not reachable. nltk is a transitive dependency of textstat; the runtime never uses nltk's JSON tag serialization system.
- **Reopen trigger:** A fix release from the nltk maintainers, or any direct nltk JSON tag usage added to the codebase.

### PYSEC-2026-597 (CVE-2026-12243) — nltk url2pathname() percent-encoded path traversal
- **Package:** nltk (transitive dep of textstat)
- **Fix version:** None released
- **Accepted:** 2026-07-02
- **Rationale:** Not reachable. Incomplete-fix follow-up to PYSEC-2026-97: `_UNSAFE_NO_PROTOCOL_RE` in `nltk/data.py` rejects literal `../` but not percent-encoded `..%2f`, which `url2pathname()` decodes after the check. Exploitation requires an attacker-controlled resource name passed to `nltk.data.load()`/`nltk.data.find()`. nltk is a transitive dependency of textstat; the runtime never imports nltk directly and never calls those loaders, so no attacker-controlled path reaches `url2pathname()`.
- **Reopen trigger:** A fix release from the nltk maintainers, or any direct call to `nltk.data.load()`/`find()` added to the codebase.

## Reporting a Vulnerability

To report a vulnerability privately, contact the platform team directly rather
than opening a public GitHub issue. After triage, a fix will be released within
the SLA window appropriate for the severity, and a public disclosure will follow.
