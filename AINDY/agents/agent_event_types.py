"""The agent-event vocabulary — one canonical list, pinned by a contract test.

Companion to `AINDY/core/system_event_types.py`, which has had a frozen SHA-256 baseline since
early on. Agent events had the model, the emitter and a warning, and never got the guard. This
module is the missing half.

★★ **Why this is not in `AINDY/db/models/agent_event.py`, where a copy used to live.**
`scripts/check_schema_version.py` content-hashes **every** file under `AINDY/db/models/`, so
adding one string to a set there trips the schema contract and demands a
`SCHEMA_CONTRACT_VERSION` bump, a baseline regeneration and two test-assertion edits — for a
change with **zero DDL impact**, since `AgentEvent.event_type` is a plain `String(32)` with no
constraint or enum.

That tax is why the copy rotted. It was never updated after the initial extraction: every one of
the five commits that added an event type put it in the service-side list instead, which was the
rational choice each time. A vocabulary that costs a schema ceremony to correct will be left
incorrect. Keeping it out of `db/models/` is the fix for the *cause*, not just the symptom.

★ **The runtime still WARNS rather than rejects, deliberately, and the precedent settles it.**
`SystemEventTypes` is not enforced at runtime either — its guard is a *test*
(`tests/unit/test_system_event_contract.py`). An unknown type must still reach the database,
because losing an audit row is strictly worse than recording one with an undeclared name. The
contract test is what makes a new name *intentional*; the warning is what makes it *visible*.

★★ **Eight of the names below were in active use and undeclared** when this module was created.
Each logged *"Unknown event type"* on every emission and was written anyway, so the list that was
supposedly enforcing the vocabulary described 14 names while 22 were in use. They are declared
here because they are real, not because they are all good — see the note on `FAILED`.

★ **The first survey of this said SIX, and was wrong twice over.** It used a regex for
`event_type="..."`, which cannot see a name assigned to a variable first (the two
`DELEGATION_ACCEPTED`/`REJECTED` sites) and reads only the first branch of a ternary (hiding
`AGENT_STEP_FAILED` and `COLLABORATION_STARTED` behind their `else`). The AST census in
`test_agent_event_contract.py` resolves all three shapes; the miscount is recorded because a
census that is easy to get wrong is the thing this file exists to stop being hand-maintained.
"""

from __future__ import annotations


AGENT_EVENT_TYPES: frozenset[str] = frozenset({
    # ── Run lifecycle ────────────────────────────────────────────────────────
    "PLAN_CREATED",
    "APPROVED",
    "REJECTED",
    "EXECUTION_STARTED",
    "COMPLETED",
    "EXECUTION_FAILED",
    "RECOVERED",
    "REPLAY_CREATED",
    "WAITING",            # RTR-1 Phase 2e — parked on a mid-plan WAIT step
    "CANCELLED",          # AGENT-HARDEN-1 — operator cancel (terminal)

    # ── Verification (AGENT-HARDEN-6) ────────────────────────────────────────
    "VERIFIED",           # post-conditions checked and held
    "VERIFY_FAILED",      # post-conditions did not hold (terminal)

    # ── Authority ────────────────────────────────────────────────────────────
    "CAPABILITY_DENIED",
    # AUTHORITY-NEGOTIATION-1 phase 1 — a denial was offered exactly one downgrade to a
    # tool-declared fallback. Recorded whether or not it was taken.
    "AUTHORITY_NEGOTIATED",

    # ── Step progress ────────────────────────────────────────────────────────
    "AGENT_STEP_COMPLETED",   # was undeclared; emitted by nodus_execution_service
    # ★ The else-branch of the same ternary. Found only after the census was taught to read
    #   `"A" if cond else "B"` — a regex that stops at the first branch reports one of the two,
    #   which is how the original survey of this drift undercounted.
    "AGENT_STEP_FAILED",
    # ★ `FAILED` is declared because it is emitted (nodus_execution_service), NOT because it is
    #   a good name. It sits beside `EXECUTION_FAILED` and `VERIFY_FAILED` with no indication of
    #   what failed, which is the kind of ambiguity this vocabulary exists to prevent. Declaring
    #   it makes the collision visible and reviewable; renaming it is a separate change, because
    #   an event type is a stored value and old rows would keep the old name.
    "FAILED",

    # ── Delegation (AgentCoordinator) ────────────────────────────────────────
    # All four were undeclared and in active use.
    "DELEGATION_DISPATCHED",
    "DELEGATION_ACCEPTED",
    "DELEGATION_REJECTED",
    "DELEGATION_BLOCKED",
    # ★ Also a ternary else-branch (`execution.py`), and not in the original survey at all.
    "COLLABORATION_STARTED",
})
