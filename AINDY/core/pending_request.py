"""WAIT-TYPED-CONTRACT-1 — a typed pending request on the durable wait.

A node that returns WAIT may declare the shape of the payload that is allowed to resume it. The
runtime records that declaration beside the wait and CHECKS a resume against it before anything
is injected or woken — a resume is checked rather than trusted.

★ The asymmetry this closes: `SyscallDispatcher.dispatch()` validates every syscall input against
its declared schema, and the wait path — which accepts data from outside the process after an
arbitrary delay, across a restart — validated nothing. The looser gate sat on the less trusted
input. So the schema dialect here is the dispatcher's OWN (`syscall_versioning.validate_payload`:
``required`` + ``properties[<name>].type``), deliberately: one vocabulary, one validator, and a
node author who has written a syscall schema has already written one of these.

★ What an unchecked resume costs, concretely (Tutorial 2): phase 2 reads
``approval["reviewer"]``. A resume with an empty payload was ACCEPTED, the wait was consumed
(the scheduler entry deleted), the script crashed on its re-run and the run went ``failed``.
The malformed resume was not refused at the door — it destroyed the run one step later.
`test_wait_typed_contract.py` keeps that as its liveness control.

The record lives on the run's state under a reserved key, written by the runner's WAIT branch —
NOT in the node's ``output_patch`` (so `flow_history.output_patch` stays what the node returned)
and NOT in a column (an additive column makes every existing deployment owe
``bootstrap-schema --reconcile``, 0018's operator note, for a defence-in-depth check). Consequence,
stated rather than hidden: the DUR-4 fold (`flow_history_fold`) does not reconstruct it — a run
recovered from a torn snapshot resumes UNTYPED. Absent is never mismatch, the same rule
`FLOW-GRAPH-SIGNATURE-1` applies to its signature; the degradation is to the pre-feature
behaviour, never to a wrong rejection.

Phase 1 covers the `FlowRun` path. The request-level wait (`ExecutionWaitSignal`) it deliberately
left out was removed the same day (`EU-WAIT-SIGNAL-DEAD-1`) — a request has no continuation to
resume, so there was never a second path to type.
"""
from __future__ import annotations

from typing import Any

# Reserved state key. Dunder-prefixed like `__effect_scope`: the runtime's, not a node's.
PENDING_REQUEST_KEY = "__pending_request"

# The WAIT-result key a node uses to declare the schema (and the guest-side state key that
# `nodus_worker` forwards as it — see `nodus_wait_resume_schema`).
RESUME_SCHEMA_KEY = "resume_schema"

# Metric label values, in one place so the counter and the tests agree on the vocabulary.
OUTCOME_ACCEPTED = "accepted"
OUTCOME_REJECTED = "rejected"
OUTCOME_UNTYPED = "untyped"


class ResumePayloadRejected(Exception):
    """A resume payload did not satisfy the schema the waiting node declared.

    Raised BEFORE anything is written or woken: the run stays ``waiting``, the scheduler entry
    stays registered, and the caller gets the validator's own error strings.
    """

    def __init__(self, run_id: str, event_type: str, errors: list[str]) -> None:
        self.run_id = str(run_id)
        self.event_type = str(event_type)
        self.errors = list(errors)
        super().__init__(
            f"resume payload for run {self.run_id} on {self.event_type!r} rejected by the "
            f"waiting node's declared schema: {'; '.join(self.errors)}"
        )


class InvalidResumeSchema(ValueError):
    """A node declared ``resume_schema`` with something that is not a schema dict.

    Loud on purpose. Silently treating a malformed declaration as "untyped" would make a typo
    indistinguishable from an undeclared wait — the guard would be off and nothing would say so.
    """


def build_pending_request(*, node: str, event: str, schema: Any) -> dict | None:
    """The record the WAIT branch writes, or ``None`` when the node declared no schema.

    ``schema`` is the node's ``resume_schema`` value: absent/``None``/``{}`` → no record (the
    wait is untyped, exactly as before this existed); a non-empty dict → the record; anything
    else → `InvalidResumeSchema`.
    """
    if schema is None or schema == {}:
        return None
    if not isinstance(schema, dict):
        raise InvalidResumeSchema(
            f"node {node!r} declared resume_schema of type {type(schema).__name__!r}; "
            "a schema is a dict in the syscall dialect ({'required': [...], 'properties': {...}})"
        )
    for key in ("required", "properties"):
        if key in schema and not isinstance(schema[key], (list, dict)):
            raise InvalidResumeSchema(
                f"node {node!r} declared resume_schema[{key!r}] of type "
                f"{type(schema[key]).__name__!r}"
            )
    return {"node": str(node), "event": str(event), "schema": dict(schema)}


def pending_request_of(state: dict | None) -> dict | None:
    record = (state or {}).get(PENDING_REQUEST_KEY)
    return record if isinstance(record, dict) else None


def check_resume_payload(
    state: dict | None,
    payload: dict | None,
    *,
    run_id: str,
    event_type: str,
) -> str:
    """Validate ``payload`` against the run's pending request, if it declared one.

    Returns the outcome label (`OUTCOME_ACCEPTED` or `OUTCOME_UNTYPED`); raises
    `ResumePayloadRejected` with the validator's errors otherwise. Pure — reads state, writes
    nothing, so a caller can decide before it touches the row.
    """
    from AINDY.kernel.syscall_versioning import validate_payload

    record = pending_request_of(state)
    schema = (record or {}).get("schema")
    if not isinstance(schema, dict) or not schema:
        return OUTCOME_UNTYPED
    errors = validate_payload(schema, dict(payload or {}))
    if errors:
        raise ResumePayloadRejected(run_id, event_type, errors)
    return OUTCOME_ACCEPTED
