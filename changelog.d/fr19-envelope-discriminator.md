### Added — responses now say whether their body is the execution envelope (FR-19)

Routes that pass through `ExecutionPipeline` return `{status, data, trace_id, duration_ms}`;
every other route returns a bare body. Both share the same URL space and **nothing on the wire
told them apart**, so every consumer had to carry per-route knowledge of whether that route
happened to enter a pipeline — knowledge obtainable only by trying it.

The app team reports this as the dominant defect class of their entire live-verification phase:
five defects on five surfaces, ~40 `safeMap prevented crash` lines inside `@aindy/ui-kit`, fixed
eleven times in client code. The failure signature is why it cost so much — an envelope where a
list was expected has no `.length`, so the empty-state branch does not fire either and the
surface renders **blank, with no error at all**.

Enveloped responses now carry:

```
X-AINDY-Envelope: v1
```

**Client rule:** unwrap `data` when the header is present, use the body as-is when it is not —
one helper instead of one decision per module. The header is deliberately **absent** on error
responses, handler-built `Response` objects, and routes with a registered response adapter,
because those bodies are not the envelope; absence means "not enveloped", never "unknown".

`X-Trace-ID` cannot serve this purpose — middleware sets it on every response.

**Also fixed, and it would have made the above useless: none of the runtime's response headers
were readable by a browser client on another origin.** `allow_headers` governs the *request*
direction, and a browser exposes only the CORS safelist unless the server names the rest. The
CORS middleware now sets `expose_headers` for `X-AINDY-Envelope`, `X-Trace-ID`, `X-Request-ID`,
`X-EU-ID`, `X-API-Version` and `X-Version-Warning`. `X-Trace-ID` has been documented as a
debugging aid all along while being unreadable from the browser doing the debugging.

Additive: no body shape changes, no existing consumer breaks. Contract documented in
`SDK_CONTRACT.md` and `UI_CONTRACT.md`.

**Not closed by this:** making every `/apps/*` route enter the pipeline is app-side work, and it
is their preferred end state. This settles the half only the runtime can — that a client can find
out which shape it received.
