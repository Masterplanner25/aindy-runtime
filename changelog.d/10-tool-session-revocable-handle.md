### Changed — tools receive a revocable DB handle, not the live session (TOOL-SEAM-ISOLATION-1 step A)

`execute_tool` resolved the tool by **name** through `TOOL_REGISTRY` — handle-shaped and correct —
and then handed it a live SQLAlchemy `Session`: a direct object reference across a trust boundary
that could not be validated, revoked mid-call, or narrowed. Every authority check the function
performs (token, granted tools, capabilities, policy, rate limit, egress, secret scope) was
advisory with respect to what the tool did with that one argument.

The tool now receives a `RevocableToolSession` (`AINDY/agents/tool_session.py`), revoked in a
`finally` when the call returns. Using it afterwards raises `ToolSessionRevoked` naming the tool.

**Measured before changing it:** across all 18 tool functions that exist — 3 runtime-owned and 15
in `aindy-apps-monolith` — **18 take `db` in their signature and 0 reference `db.<anything>`.**
Pure ambient authority with zero utility, so the narrowing breaks nothing that exists. The
parameter name is unchanged, so no tool signature moves. Same evidence `GUEST-CONFINE-1` gathered
before denying its three capabilities.

**What it buys:** a tool can no longer stash the session and use it after the call. That is a
security narrowing *and* a bug class — using a request-shared session after its request has moved
on is `RT-MEMTXN-LEAK-1`'s neighbourhood. Any access at all is logged once per tool, so the
exposure stays countable against a measured baseline of zero.

**★ What it does NOT buy, and must not be read as:** the process is not bounded. A tool holding
this handle can still `import os`, spawn a thread, or open a socket. **`TOOL-SEAM-ISOLATION-1`
remains open.** Treating step A as closing it would be exactly the "gated path that does not
actually confine" failure the scope warns against.

**Known limitation, stated rather than discovered:** the handle is not a `Session` subclass, so
`isinstance(db, Session)` is `False` inside a tool. Deliberate — subclassing would let it be
passed anywhere a real session goes and defeat the point — and safe because no tool uses the
parameter. A tool that genuinely needs data should reach through a syscall, which is what every
app tool already does.

Unflagged, because no compatibility argument exists and a security default that ships off is a
pattern this repository keeps recording as a mistake. 14 tests, mutation-tested **7/7**.
