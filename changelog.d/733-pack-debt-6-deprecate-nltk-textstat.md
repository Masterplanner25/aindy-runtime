### Deprecated — `nltk` and `textstat` leave the runtime's dependencies in the release after this one (PACK-DEBT-6, #733)

- The runtime pins `nltk==3.10.3` and `textstat==0.7.13` and **imports neither**; they were kept
  because `aindy-apps-monolith`'s search service imported both without declaring them — a consumer
  depending on what the runtime *installs* rather than what it *declares*. The app now declares
  them itself (its #391, 2026-09-20).
- **Both pins are removed in the next release after this one, and not before 2026-10-01.** Nothing
  changes in this release. A consumer that imports either package must declare it in its own
  `pyproject.toml` before then; one that already does (the app) sees no change at all.
- With the pins go the four `pip-audit --ignore-vuln` acceptances that existed only for them
  (`PYSEC-2026-97`, `GHSA-rf74-v2fm-23pw`, `PYSEC-2026-597`, `PYSEC-2026-3740`) — an advisory
  with no fix released closes by absence, which is the only way it ever closes.
