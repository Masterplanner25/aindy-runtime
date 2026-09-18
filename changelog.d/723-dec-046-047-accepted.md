### Changed — `HTTP-SCOPE-GAP-1` and `CLI-EXEC-SURFACE-1` closed by decision (#723; DEC-046, DEC-047 accepted)

- No code change. DEC-046: a scope answers the VERB and a row filter answers OWNERSHIP; no
  cross-owner read path and no `:any` scope variant is added. DEC-047: the operator half of the
  runtime (resume, flow list/get, queue + DLQ, trace, health) stays HTTP-only — not
  syscall-addressable, no CLI. Both were recorded provisional in #716 and accepted by the owner.
