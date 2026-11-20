### Changed — openai 3.57.0: the SDK's HTTP transport is httpx2, and its TLS trusts the OS store (#991)

- **Operator note (custom CAs):** OpenAI, DeepSeek and Azure OpenAI calls now verify TLS against the operating
  system's trust store (in the published image, Debian's `ca-certificates`), not the `certifi` bundle. A deployment
  that reaches these providers through a TLS-intercepting proxy or a private CA must have that CA in the OS store, or
  set `SSL_CERT_FILE` / `SSL_CERT_DIR`. A stock deployment needs nothing. The `certifi` floor (DEC-102) no longer
  applies to these calls; they pick up CA updates when the image is rebuilt (DEC-138).
- openai 2.54.0 → 3.57.0. 3.0's only breaking change moves the SDK's transport from `httpx` to `httpx2`; new pins
  `httpx2==2.16.2`, `httpcore2==2.16.2`, `truststore==0.10.5`. `httpx` stays for the other callers.
- The OpenAI and DeepSeek contract tests were mocked with `respx`, which patches only `httpx`: on openai 3 they reached
  the real API. They now use an `httpx2.MockTransport` on the client the adapter builds, and fail on any request they
  did not route.
