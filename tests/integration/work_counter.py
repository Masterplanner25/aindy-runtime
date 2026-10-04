"""Count WORK, not time — the regression instrument PERF-BASELINE-1 asked for.

Why not a clock
---------------
The performance regressions this repository actually had were both visible as *work*, never as
a duration: ``MEM-RECALL-N1-1`` issued 3 queries per recall candidate, and ``RT-MEMTXN-LEAK-1``
held a pooled connection ``idle in transaction`` across the embedding API call until the pool
drained. A wall-clock threshold on shared CI hardware flakes, gets widened until it asserts
nothing, and would have caught neither. Query counts and connection holds are deterministic.
(`docs/design/WITNESS_AND_BASELINE_SCOPE.md`, Part 1.)

What it counts, on one engine
-----------------------------
* ``statements`` — every SQL statement sent (``before_cursor_execute``), in order.
* ``checkouts`` — pool checkouts during the window.
* ``held`` — connections checked out *now* relative to the start of the window, and ``peak``,
  the most held at once. Sample ``held`` from inside a slow call to prove it holds nothing.

Attach it to the engine the code under test really uses. On Postgres in this suite that is
``AINDY.db.database.engine`` itself (``tests/fixtures/db.py::test_engine`` yields it), which is
also what the effect gate's and the recall's own sessions bind to, so their work is counted too.

★ The trap it is shaped around: a count taken over the wrong engine reads zero and passes. Every
test using it should first assert the window saw *some* work (`assert_saw_work`).
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator


@dataclass
class Work:
    statements: list[str] = field(default_factory=list)
    checkouts: int = 0
    held: int = 0
    peak: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def queries(self) -> int:
        return len(self.statements)

    def matching(self, needle: str) -> list[str]:
        """Statements mentioning ``needle`` (a table name, usually), case-insensitive."""
        needle = needle.lower()
        return [s for s in self.statements if needle in s.lower()]

    def assert_saw_work(self) -> None:
        assert self.statements, (
            "the work counter saw no SQL at all. It is almost certainly attached to an engine the "
            "code under test does not use, and every count it reports is vacuous."
        )


@contextmanager
def count_work(engine) -> Iterator[Work]:
    from sqlalchemy import event

    work = Work()

    def _statement(conn, cursor, statement, params, context, executemany):
        with work._lock:
            work.statements.append(statement)

    def _checkout(dbapi_connection, record, proxy):
        with work._lock:
            work.checkouts += 1
            work.held += 1
            work.peak = max(work.peak, work.held)

    def _checkin(dbapi_connection, record):
        with work._lock:
            work.held -= 1

    event.listen(engine, "before_cursor_execute", _statement)
    event.listen(engine, "checkout", _checkout)
    event.listen(engine, "checkin", _checkin)
    try:
        yield work
    finally:
        event.remove(engine, "before_cursor_execute", _statement)
        event.remove(engine, "checkout", _checkout)
        event.remove(engine, "checkin", _checkin)
