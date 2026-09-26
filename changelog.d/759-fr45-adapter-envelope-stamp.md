### Fixed — the runtime's own envelope-bodied response adapters now set `X-AINDY-Envelope` (FR-45, #759)

- `raw_canonical_adapter`, `legacy_envelope_adapter`, `memory_execute_adapter` and the success
  path of `memory_completion_adapter` (`AINDY.platform_layer.response_adapters`) return an
  envelope as their body, but did not set the header. `adapt_response` stamps only its own
  default exit, and the registered-adapter branches returned before it.
- **Why it was wrong:** ui-kit ≥ 2.1.0 treats every unstamped body as bare once a session has
  seen one stamped response. An app that registered these adapters had its envelopes unwrapped
  before that point and not after it: the same page rendered or went blank depending on which
  page the session opened first. The app team found 26 of its 78 parameterless GETs affected.
- The rule is the one the client applies: a body is stamped exactly when it is a dict with a
  top-level `data` key. `raw_json_adapter` never stamps, and neither does the legacy adapter's
  passthrough of a payload without `data`. **An app's own adapter still decides for itself**
  (`UI_CONTRACT.md`).
- An app that already stamps these routes itself (the monolith's `apps/_shared/envelope.py`)
  sets the same value twice. That is harmless, and the app can remove its own stamping on this
  release.
