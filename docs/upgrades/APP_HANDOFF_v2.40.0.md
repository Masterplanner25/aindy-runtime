---
title: "App Handoff — Runtime v2.40.0"
api_version: "1.0"
last_verified: "2026-11-20"
status: current
owner: "platform-team"
---

# App handoff: runtime v2.40.0

**No schema step**: Alembic stays at `0022` and the schema contract at `2026-11-09`. No ui-kit or aindy-sdk release.
**One dependency major moved: `openai` 2.54.0 → 3.57.0** (§1). It answers the trace question from your 2.34.0 report
(§2) and corrects your register on FR-6 (§3).

Bump the `constraints.txt` pin to `==2.40.0` (your contract test moves the floor with it). If `constraints.txt` or a
requirements file pins `openai`, `httpx2`, `httpcore2` or `truststore`, move them to the runtime's pins (§1), or the
install will not resolve. Then **rebuild the image** (`mcp` shares it, so recreate both).

> ## ★ After the rebuild: confirm what you are running
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v, openai, httpx2; print(v.__version__, openai.__version__, httpx2.__version__, list(AINDY.__path__))"
> # expect: 2.40.0 3.57.0 2.16.2 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 1. openai 3.57.0 (DEC-138, #991)

openai 3.0's only breaking change moves the SDK's HTTP transport from `httpx` to a separate package, `httpx2`; 3.1 to
3.57 are features and fixes. The runtime pins `openai==3.57.0`, `httpx2==2.16.2`, `httpcore2==2.16.2` and
`truststore==0.10.5`. `httpx` stays installed (0.28.1); the two stacks sit side by side.

**What it means for you:**

- **`apps/automation/services/owner_notices.py::probe_openai`** builds its own `OpenAI(...)`. That keeps working:
  `timeout=` and `max_retries=` are unchanged, and `classify()` reads `status_code`, `code` and `body`, which openai 3
  errors still carry (checked on 3.57.0).
- **Tests that mock OpenAI with `respx` stop intercepting.** `respx` patches `httpx` only, so on openai 3 a
  respx-mocked call goes to the real API (ours went red only because the fixture key was fake). Mock with
  `httpx2.MockTransport` instead: `OpenAI(api_key=…, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))`.
  The runtime's version is `tests/fixtures/sdk_transport.py`, which also fails a test on any request it did not route.
  `respx` over the Anthropic SDK or plain `httpx` is unaffected.
- **TLS trust for OpenAI, DeepSeek and Azure OpenAI calls is the OS store, not `certifi`.** In the image that is
  Debian's `ca-certificates`, refreshed whenever the image is rebuilt. A stock deployment needs nothing. If anything
  between the api container and the provider presents a private CA (a TLS-intercepting proxy), add that CA to the OS
  store or set `SSL_CERT_FILE` / `SSL_CERT_DIR`. The `certifi>=` floor (DEC-102) still applies to everything else.

## 2. Your 2.34.0 trace question: answered (DEC-137, #990)

You asked (`RUNTIME_2_34_0_UPGRADE.md` §6 item 5; earlier handoffs called it "§5") whether a scoped trace graph should
keep an edge to the caller's own event outside the trace. **Yes, as a dangling edge, like the unscoped graph.**

- Read scoped to its owner, `d534a3fd…` should now show the 290 `async_child` edges again: **583 edges, as unscoped**,
  and still **584 nodes**. The far events are not added as nodes; the nodes stay this trace's events.
- An edge to **another user's** event, or to an event with **no user**, is still dropped (FR-60 stands).
- Terminal events now agree with the unscoped view: an event that queued the owner's own work is no longer terminal.
  Scoped **depth** still counts only edges between two nodes of the trace, so it does not change.

**Ask:** re-read `d534a3fd…` scoped to its owner on 2.40.0 and report nodes / edges / depth. That closes the question.

## 3. FR-6 is closed, and has been since 2.0.0

Your register still lists "FR-6 items 2–3" as open. They shipped in **2.0.0** (#337, #338) as you chose them (option
(a), the runtime sends the mail), and the runtime closed FR-6 on 2026-11-04 without telling you; this is that notice.

- `POST /auth/password/forgot` — always `200` (no enumeration), `3/minute` per IP and per address, mails a reset link.
- `POST /auth/password/reset` — consumes the token; it is stateless and single-use (the reset bumps
  `token_version`), with a 30-minute lifetime (`AINDY_PASSWORD_RESET_TTL_MINUTES`).
- `AINDY_PASSWORD_RESET_URL_TEMPLATE` (`{token}` substituted) points the mail at your UI; empty, the mail carries the
  bare token. Mail goes through the `AINDY_SMTP_*` settings.

**Ask:** mark FR-6 closed in `RUNTIME_FEATURE_REQUESTS.md`, or tell us which part does not work for you.

## 4. Your register

- **FR-55** (2.39.0): still waiting on your ported-loop report; no new ask.
- **The trace question:** answered here (§2); closes on your re-read.
- **FR-6:** closed (§3).
