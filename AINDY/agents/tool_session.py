"""A revocable handle over the DB session handed to a tool (TOOL-SEAM-ISOLATION-1, step A).

Scope and reasoning: ``docs/runtime/TOOL_SEAM_ISOLATION_SCOPE.md``.

The principle, from the Linux fd model
--------------------------------------
Userspace holds an **integer index into a kernel table, never a pointer**: hand out opaque
handles across a trust boundary, resolved through a table you can *validate, revoke and redirect*.

``execute_tool`` gets this half right and then breaks it in the same function. It resolves the
tool by **name** through ``TOOL_REGISTRY`` — handle-shaped, correct — and then calls::

    result = entry["fn"](args=args, user_id=user_id, db=db)

``db`` is a live SQLAlchemy ``Session``: a direct object reference across the trust boundary,
which cannot be validated, cannot be revoked mid-call, and cannot be redirected to a narrower
view. Every authority decision made before that line is advisory with respect to what the tool
does with that one argument.

Why this is cheap, measured rather than assumed
-----------------------------------------------
Across **all 18 tool functions that exist** — 3 runtime-owned and 15 in ``aindy-apps-monolith`` —
**18 take ``db`` in their signature and 0 reference ``db.<anything>``.** The parameter is pure
ambient authority: maximum exposure, zero utility. That is the same measurement
``GUEST-CONFINE-1`` made before denying its three capabilities, with the same conclusion — the
narrowing breaks nothing that exists.

What this does and does not buy
-------------------------------
**Does:** a tool can no longer stash the session and use it after the call returns. That is a
security narrowing *and* a bug class — using a request-shared session after its request has moved
on is ``RT-MEMTXN-LEAK-1``'s neighbourhood. It also makes any use at all **countable**, which
matters precisely because the measured baseline is zero.

**Does not:** bound the process. A tool holding this handle can still ``import os``, spawn a
thread, or open a socket. **This narrows one argument; only the process boundary (step C) bounds
ambient authority.** Do not read step A landing as this entry being closed — that would be the
"gated path that does not actually confine" failure the scope warns about.

Deliberately unflagged
----------------------
No compatibility argument exists: nothing uses the session, and the only behaviour that changes is
use-after-return, which is already a defect. A security default that ships off is a pattern this
repo keeps recording as a mistake.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Attribute names owned by the handle itself. Everything else forwards to the session.
_OWN = frozenset({"_rs_session", "_rs_tool", "_rs_revoked", "_rs_touched"})


class ToolSessionRevoked(RuntimeError):
    """A tool used its DB handle after the call that lent it returned.

    This is not a policy refusal a caller should retry — it means the tool retained a reference
    it was not entitled to keep. The traceback names the tool.
    """


class RevocableToolSession:
    """An opaque, revocable proxy over a live SQLAlchemy ``Session``.

    ★ **Known limitation, stated rather than discovered:** this is *not* a ``Session`` subclass, so
    ``isinstance(db, Session)`` is ``False`` inside a tool. That is deliberate — subclassing would
    let the handle be passed anywhere a real session goes and defeat the point — and it is safe
    here because no tool uses the parameter at all. A tool that genuinely needs a session should
    reach through a syscall, which is what every app tool already does.
    """

    def __init__(self, session: Any, *, tool_name: str) -> None:
        object.__setattr__(self, "_rs_session", session)
        object.__setattr__(self, "_rs_tool", tool_name)
        object.__setattr__(self, "_rs_revoked", False)
        object.__setattr__(self, "_rs_touched", False)

    # ── lifecycle ────────────────────────────────────────────────────────────

    def revoke(self) -> None:
        """Invalidate the handle. Idempotent; never touches the underlying session.

        ★ The session itself is NOT closed here. The runtime's own bookkeeping
        (``_finalize_tool_effect``) runs after the tool returns and needs the real session, and
        closing a request-shared session out from under its owner is exactly what
        ``RT-MEMTXN-LEAK-1`` established must never happen.
        """
        object.__setattr__(self, "_rs_revoked", True)

    @property
    def revoked(self) -> bool:
        return bool(object.__getattribute__(self, "_rs_revoked"))

    @property
    def touched(self) -> bool:
        """Whether the tool accessed the session at all. Measured baseline across all tools: never."""
        return bool(object.__getattribute__(self, "_rs_touched"))

    # ── the boundary ─────────────────────────────────────────────────────────

    def _rs_check(self, attr: str) -> Any:
        if object.__getattribute__(self, "_rs_revoked"):
            tool = object.__getattribute__(self, "_rs_tool")
            raise ToolSessionRevoked(
                f"tool {tool!r} used its database handle after returning (attribute {attr!r}). "
                "The handle is valid only for the duration of the tool call; retaining it is not "
                "supported. Reach through a syscall instead."
            )
        if not object.__getattribute__(self, "_rs_touched"):
            object.__setattr__(self, "_rs_touched", True)
            logger.info(
                "[ToolSession] tool %r accessed its database handle (attribute %r). "
                "No first-party tool does this; recording it so the exposure is countable.",
                object.__getattribute__(self, "_rs_tool"),
                attr,
            )
        return object.__getattribute__(self, "_rs_session")

    def __getattr__(self, name: str) -> Any:
        # Only reached for names not found normally, so the handle's own API is never forwarded.
        return getattr(self._rs_check(name), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _OWN:
            object.__setattr__(self, name, value)
            return
        setattr(self._rs_check(name), name, value)

    # ★ Dunders are looked up on the TYPE for implicit invocation, so they never route through
    # __getattr__ and must be forwarded by hand. `with db:` is the realistic one.
    def __enter__(self) -> Any:
        return self._rs_check("__enter__").__enter__()

    def __exit__(self, *exc: Any) -> Any:
        return self._rs_check("__exit__").__exit__(*exc)

    def __repr__(self) -> str:
        state = "revoked" if self.revoked else "live"
        return f"<RevocableToolSession tool={object.__getattribute__(self, '_rs_tool')!r} {state}>"
