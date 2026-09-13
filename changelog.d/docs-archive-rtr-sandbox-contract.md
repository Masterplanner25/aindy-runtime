### Added — `docs/runtime/SANDBOX_CONTRACT.md` (docs/archive-rtr)

The sandbox contract the C2 audit recommended on 2026-05-24 and nobody wrote: sixteen numbered
invariants across the three seams where the runtime runs code it did not author (plugin host,
guest VM, tool worker), each naming its enforcement point and the test that pins it, a runner
matrix of what each runner actually delivers, and a §7 stating what is *not* guaranteed so it
cannot be inferred from silence. Two coverage gaps it surfaced are recorded in the document
rather than hidden: the `hostile-third-party` post-launch kill has no direct test, and the strong
runner's launcher flags are verified only by the live `/proc` probe, never in CI.

### Changed — `EXTENSION_TRUST_MODEL.md` regains its Assurance Reporting section

The 2026-05-31 docset reconciliation deleted the only prose definitions of *assurance class /
attestation / certification tier*; the terms stayed in the posture report and four live docs
with no definition. Restored against the current constants — five vocabularies now, including
the assurance ceiling and the `kernel-observable` verification method that post-date the
deleted text.

### Changed — five root documents archived, one deleted

`RTR.md`, `IDEMPOTENCY_AUDIT.md`, `ISOLATION_MODEL_PLAN.md` and `C2_SANDBOX_AUDIT.md` moved to
`docs/archive/`, each with a per-item verification in the archive README of why nothing
forward-looking remains. `sandbox_runner.py`'s operator-facing `ceiling_note` and two Alembic
docstrings now cite the archive paths; `effect_record.py`'s bare-name citation was left alone on
purpose (a docstring edit under `db/models/` costs a schema-version bump). The gitignored
`Idempotency convo.txt` transcript was deleted — it was the conversation that proposed
`EffectRecord`, and everything in it has shipped, been filed, or been declined.
