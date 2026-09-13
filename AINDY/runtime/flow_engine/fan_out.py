"""FLOW-PARALLEL-1 phases 1+2 — declared fan-out, bounded width, per-branch sessions, join policies.

Design: `docs/design/FLOW_PARALLEL_DESIGN.md`. Read §3 before changing anything here; it is a
hard constraint that narrows this more than the topology model does.

Phase 0 (#603) widened the *transaction*: ordinals are allocated for a whole superstep at the
barrier, and the merge moved out of per-node status handling into `_merge_superstep`, which
already takes an ordered list of branch results. **Both take the plural form with a list of one.**
Phase 1 is what lengthens that list.

★★ **The flag controls CONCURRENCY, not SEMANTICS.** A declared fan-out group runs its branches
in declaration order either way; `AINDY_FLOW_FAN_OUT` decides only whether they run *at the same
time*. Sequential execution produces the same patches in the same order, so the same merge, the
same ordinals and the same `FlowHistory`. That matters for rollback: turning the flag off must
never change what a flow computes, only how long it takes. A design where the flag also gated the
*shape* would make "off" a second, less-tested semantics.

★★ **The width bound is PROCESS-WIDE, not per-run, and the distinction is load-bearing.** The
design's §3(a) reads naturally as a per-run bound, and a per-run bound is not enough: runners are
created from request handlers, syscall dispatch, rehydration and scheduler recovery, so many runs
are in flight at once and a per-run width of W allows *runs × W* concurrent sessions. The
scheduler solved the same problem the same way — one shared, fixed-size pool — and
`test_flow_fan_out_leaves_db_headroom` pins the trade so it cannot be made accidentally.

★ **Every branch gets its OWN session.** `AGENT_WORKING_RULES` §5 is unconditional and the
failure mode is silent corruption, not an exception. The runner's own session stays
single-threaded and remains the only `FlowHistory` writer (§3c, §4).

★ **`WAIT` inside a group is refused, by decision (§5, option 3, approved 2026-09-08).** A
suspended branch would need a durable partial-superstep record that may never be wanted, and
lifting the restriction is coupled to `RECOVERY-GRANULARITY-1` rather than to this. The
limitation is *declared and loud* rather than discovered.

★★ **Phase 2 — the join is DECLARED on the group, resolved at the barrier (2026-09-13).** Phase 1
shipped the degenerate join — every branch must succeed and converge — as the narrowest thing
that made fan-out coherent. `join="all"` (the default, byte-for-byte phase 1), `join="any"` and
`join="quorum", quorum=k` generalise it. Under a lenient join a superstep in which SOME branches
failed is not a failure and not a success: it is a **`partial`** outcome, `EFFECT-PARTIAL-1`'s
vocabulary, naming the branches that did not land — recorded on the run's state, carried on the
completion event, and lifted onto the `sys.v1.flow.run` envelope. The merge is unchanged: only
SUCCESS branches ever contributed, and convergence is required of the branches that succeeded.
"""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ── The declared shape ───────────────────────────────────────────────────────


JOIN_ALL = "all"
JOIN_ANY = "any"
JOIN_QUORUM = "quorum"
JOIN_POLICIES = (JOIN_ALL, JOIN_ANY, JOIN_QUORUM)


@dataclass(frozen=True)
class FanOutEdgeGroup:
    """A declared set of successors that run as one superstep, and how they JOIN.

    A distinct type rather than another dict shape: `flow["edges"]` already uses a dict to mean
    *conditional edge*, and overloading it would make the graph ambiguous to read and to
    canonicalise.

    ★ `targets` is a tuple because DECLARATION ORDER IS THE CONTRACT — it is the merge order
    (`state_merge` resolves `last_write_wins` in declaration order) and the ordinal order
    (§4). A mutable sequence would let a caller reorder it after the signature was taken.

    ★ `join` (phase 2) says how many branches must SUCCEED for the superstep to proceed:
    `all` (every one — the default, and phase 1's behaviour exactly), `any` (at least one), or
    `quorum` with `quorum=k` (at least k). A lenient join that proceeds past a failed branch
    reports a `partial` outcome naming it; it never hides it. The join is part of the graph's
    SHAPE — it decides which successors are reachable — so a non-default join is in the graph
    signature and a run planned under one join will not resume under another.
    """

    targets: tuple[str, ...]
    join: str = JOIN_ALL
    quorum: int | None = None

    def __init__(self, targets, join: str = JOIN_ALL, quorum: int | None = None):
        object.__setattr__(self, "targets", tuple(str(t) for t in targets))
        object.__setattr__(self, "join", str(join or JOIN_ALL).strip().lower())
        object.__setattr__(self, "quorum", int(quorum) if quorum is not None else None)
        if len(self.targets) < 2:
            raise ValueError(
                f"FanOutEdgeGroup needs at least two targets, got {list(self.targets)}. "
                "A group of one is a sequential edge and should be declared as one — an "
                "unnecessary group changes the flow's graph signature for no behaviour."
            )
        if len(set(self.targets)) != len(self.targets):
            raise ValueError(
                f"FanOutEdgeGroup has duplicate targets: {list(self.targets)}. The same node "
                "twice in one superstep would allocate two ordinals for one node and merge its "
                "patch against itself."
            )
        if self.join not in JOIN_POLICIES:
            raise ValueError(
                f"FanOutEdgeGroup join must be one of {list(JOIN_POLICIES)}, got {join!r}."
            )
        if self.join == JOIN_QUORUM:
            if self.quorum is None or not (1 <= self.quorum <= len(self.targets)):
                raise ValueError(
                    f"FanOutEdgeGroup join='quorum' needs quorum=k with 1 <= k <= "
                    f"{len(self.targets)} (the group's width), got {quorum!r}. k == width is "
                    "`all` and k == 1 is `any`; both are allowed, spelled either way."
                )
        elif self.quorum is not None:
            raise ValueError(
                f"FanOutEdgeGroup quorum={quorum!r} is only meaningful with join='quorum' "
                f"(got join={self.join!r}). A number that would be ignored is a declaration "
                "that lies."
            )

    @property
    def required_successes(self) -> int:
        """How many branches must succeed for the superstep to proceed."""
        if self.join == JOIN_ANY:
            return 1
        if self.join == JOIN_QUORUM:
            return int(self.quorum or 0)
        return len(self.targets)

    @property
    def join_label(self) -> str:
        """`all` | `any` | `quorum:k` — what a record of the superstep says about its join."""
        return f"{JOIN_QUORUM}:{self.quorum}" if self.join == JOIN_QUORUM else self.join


@dataclass(frozen=True)
class JoinOutcome:
    """What the barrier decided for one superstep."""

    join: str
    required: int
    succeeded: tuple[str, ...]
    failed: tuple[str, ...]

    @property
    def satisfied(self) -> bool:
        return len(self.succeeded) >= self.required

    @property
    def partial(self) -> bool:
        """Proceeded past at least one failed branch — a `partial` outcome, never a silent one."""
        return self.satisfied and bool(self.failed)


def resolve_join(group: FanOutEdgeGroup, statuses: dict[str, str]) -> JoinOutcome:
    """Apply the group's join policy to per-branch statuses (`SUCCESS` | `FAILURE` | ...).

    Order is declaration order throughout, for the same reason the merge and the ordinals
    are: a record that listed failed branches in completion order would read differently on
    every run.
    """
    succeeded = tuple(t for t in group.targets if statuses.get(t) == "SUCCESS")
    failed = tuple(t for t in group.targets if statuses.get(t) != "SUCCESS")
    return JoinOutcome(
        join=group.join_label, required=group.required_successes, succeeded=succeeded, failed=failed,
    )


class FanOutWaitRefused(RuntimeError):
    """A branch inside a fan-out group returned WAIT. Refused at the barrier (§5, option 3)."""


# ── The bound ────────────────────────────────────────────────────────────────

_DEFAULT_MAX_WIDTH = 4

_executor_lock = threading.Lock()
_executor: ThreadPoolExecutor | None = None


def fan_out_concurrency_enabled() -> bool:
    """Phase 1 ships default-OFF; phase 4 flips it on evidence, not on code.

    ★ Opt-IN: anything unrecognised reads as off. An unparseable value must never turn on
    concurrency, and there is no test-mode short-circuit above this read.
    """
    return os.getenv("AINDY_FLOW_FAN_OUT", "").strip().lower() in {"1", "true", "yes", "on"}


def max_fan_out_width() -> int:
    """Process-wide ceiling on concurrently-executing branches.

    ★ Sized against the DB connection budget, which is SHARED with request handling. The
    arithmetic, at the shipped defaults: capacity is `DB_POOL_SIZE + DB_MAX_OVERFLOW` = 30, the
    scheduler's lanes already hold up to 13, and this pool adds at most `_DEFAULT_MAX_WIDTH`.
    That leaves request handling the balance, and `test_flow_fan_out_leaves_db_headroom` asserts
    it as a ratio rather than a magic number so it keeps meaning something if either side is
    retuned.

    ★ `SYSMAX-5` is why this is a ceiling and not a knob to raise when fan-out feels slow: that
    entry was fixed by ISOLATION rather than capacity, on the finding that more threads sharing
    one connection budget starve the API instead of speeding anything up.
    """
    raw = os.getenv("AINDY_FLOW_FAN_OUT_MAX_WIDTH", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return _DEFAULT_MAX_WIDTH


def _branch_executor() -> ThreadPoolExecutor:
    """One shared, fixed-size pool for every flow run in this process (see the module docstring)."""
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=max_fan_out_width(),
                thread_name_prefix="aindy-flow-branch",
            )
        return _executor


def reset_branch_executor_for_tests() -> None:
    """Drop the pool so a test can change the width and get a pool sized to it."""
    global _executor
    with _executor_lock:
        if _executor is not None:
            _executor.shutdown(wait=False)
        _executor = None


# ── Branch execution ─────────────────────────────────────────────────────────


def _run_one_branch(node: str, state: dict, context: dict, execute: Callable) -> dict:
    """Execute one branch on its OWN database session.

    ★ The session is opened here and closed in `finally`, per branch. `RT-MEMTXN-LEAK-1`'s rule
    applies more sharply than usual: N branches multiply the holding time by N, so a session held
    across slow work costs N connections for the duration rather than one.

    ★ The branch receives a COPY of the context carrying its own `db`. Mutating the runner's
    context from a thread would be exactly the shared-state hazard §5 forbids, one level up from
    the session itself.
    """
    from AINDY.db.database import SessionLocal

    branch_db = SessionLocal()
    try:
        branch_context = dict(context)
        branch_context["db"] = branch_db
        # State is READ by a branch and never written: branches return patches, and the runner
        # merges them centrally (§3c). A copy makes that structural rather than trusted.
        result = execute(node, dict(state), branch_context)
        try:
            branch_db.commit()
        except Exception:
            branch_db.rollback()
            raise
        return {"node": node, "result": result, "error": None}
    except Exception as exc:  # noqa: BLE001 - reported per branch, never swallowed
        try:
            branch_db.rollback()
        except Exception:
            pass
        logger.error("[FlowFanOut] branch %s raised: %s", node, exc)
        return {"node": node, "result": None, "error": str(exc)}
    finally:
        try:
            branch_db.close()
        except Exception:  # pragma: no cover
            pass


def run_fan_out_branches(
    targets: tuple[str, ...] | list[str],
    state: dict,
    context: dict,
    execute: Callable[[str, dict, dict], Any],
) -> list[dict]:
    """Run every branch and return results in **declaration order**, whatever they finish in.

    ★ Ordering the results by declaration rather than completion is the whole point. Completion
    order is nondeterministic, and `state_merge` resolves `last_write_wins` in declaration order —
    so returning completion order would make the merged state depend on a race, which is the
    property `FLOW-PARALLEL-1` exists to prevent.

    ★ Concurrency is bounded by the shared pool; a group wider than the pool still runs, in
    waves. Width is a resource ceiling, not a limit on how wide a flow may declare.
    """
    targets = tuple(targets)

    if not fan_out_concurrency_enabled():
        # Sequential, in declaration order. Same patches, same order, same result — only the
        # timing differs. See the module docstring on why the flag gates timing and not shape.
        return [_run_one_branch(node, state, context, execute) for node in targets]

    pool = _branch_executor()
    futures = [pool.submit(_run_one_branch, node, state, context, execute) for node in targets]
    # `.result()` in submission order: this both preserves declaration order and re-raises
    # nothing, because `_run_one_branch` converts a branch failure into a reported result.
    return [f.result() for f in futures]
