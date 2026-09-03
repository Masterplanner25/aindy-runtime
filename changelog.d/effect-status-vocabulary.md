### Added — an effect record can now say "partly applied" and "outcome unobserved"

`EffectRecord.status` gains two values, and the set becomes enforced rather than conventional.
**No migration** — the column is a plain `String(32)` with no CHECK constraint.

- **`partial`** (`EFFECT-PARTIAL-1`) — some units of a batched effect applied and some did not.
  The syscall envelope is binary, so a five-unit effect with two failures forced through it was
  either a **lie** (`success`, silently partial) or a **waste** (`error`, discarding the three
  that landed), and neither was recoverable from afterwards because the record did not say which
  units applied. Write the per-unit outcome into `result_payload`: a `partial` with no such
  record is strictly worse than `failed`, since it reports that something went wrong and removes
  the ability to say what.
- **`unknown`** (`EFFECT-OUTCOME-UNKNOWN-1`) — dispatched, outcome unobserved. Narrowly: a read
  timeout after a full request write, which is the *only* genuinely ambiguous phase. A DNS
  failure, a refused connection or an incomplete write are knowably **not dispatched**; an
  acknowledgement is knowably **landed**. It is a claim about the world, not about the runtime's
  confidence — an unclassified exception is still `failed`.
- **`pending` is now refused as a completion status**, with an error that says why. It was the
  obvious thing to reach for when an outcome is unobserved and it is wrong twice over: the TTL
  cleanup job hard-excludes pending rows, so an honest ambiguity parked there would never be
  cleaned up, and the stale-handler warning would fire on it hourly as a malfunction.
- **`complete_effect_record` now validates.** It previously accepted any `str` against a column
  with no constraint, so the vocabulary was a docstring and a habit — a typo wrote a status
  nothing would query for, and the TTL reaper would silently treat it as terminal.

**Nothing emits the new values yet.** This is the vocabulary; the surfaces that would produce
them are separate changes. The envelope stays binary — widening it is a consumer-visible response
change and is deliberately not bundled here.
