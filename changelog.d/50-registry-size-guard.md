### Changed — CI now enforces the CLAUDE.md registry's size rule (#493)

**This changes what a green `Runtime Contracts` means**, which is why it is here rather than
filed as a docs-only change. `tests/unit/test_debt_registry_accuracy.py` gained three assertions:
no registry entry may exceed 1150 bytes (850 under a `### Closed` heading), the cap must stay
near the data it governs, and the registry must stay under 60% of `CLAUDE.md`. A PR that adds an
over-long entry to the registry now fails CI.

The caps are the current high-water mark, written into the test as a **ratchet against regrowth**
rather than an endorsement of that length. The registry had been trimmed twice and grown back
both times; the previous attempt reported −14,936 B while the file grew 96,913 → 115,234 B,
because the delta was measured over the entries touched rather than over the file. Mutation
tested 5/5, including a liveness control that fires if the cap ever drifts far above the data.

Same PR trimmed the registry 67,986 → 55,829 B with no entry deleted, after verifying that 79 of
91 entries already have a larger record in `TECH_DEBT.md`.
