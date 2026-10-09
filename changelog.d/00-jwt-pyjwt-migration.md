### Changed — the JWT library is PyJWT; python-jose, ecdsa, rsa and pyasn1 leave the install (#PR)

**Operators: read before upgrading** if any plugin or extension in your deployment imports
`jose`, `ecdsa`, `rsa` or `pyasn1` without declaring it — the runtime no longer installs them.

- **Why.** `CVE-2026-85394` / `GHSA-3qf3-8w2g-rqmx` (critical, python-jose <= 3.5.0, no fix
  released): the algorithm-confusion guard accepts a DER-encoded public key as an HMAC secret.
  The runtime was not exploitable — every verifying decode pins `HS256`, every key is symmetric —
  but an advisory with no fix can only be accepted forever, and it is jose's second incomplete
  fix of the same guard. pip-audit (a required check) has been red on every PR since it landed
  in OSV.
- **Tokens are unchanged on the wire.** Header, claim order and signature are byte-identical;
  sessions, verification links and reset links minted before the upgrade verify after it
  (pinned by jose-minted fixtures in `tests/unit/test_jwt_library_migration.py`).
- **`ecdsa`'s accepted finding (CVE-2024-23342) is gone with it** — jose was its only reason to
  be installed. `security-audit.yml` now carries no exemptions.
- **`pymongo` 4.18.1 → 4.18.2** in the same change: `CVE-2026-96747/96748/96749` (forced local
  socket via a `.sock` KMS endpoint, connection redirection via percent-encoded host delimiters,
  a heap out-of-bounds write in BSON encoding) landed in OSV after jose did, so either fix alone
  would still have left the required check red.
