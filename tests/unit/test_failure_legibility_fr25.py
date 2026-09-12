"""FR-25 (a) + (c) — a failure the runtime already knows about must arrive where it can be read.

Filed by the app team 2026-09-11 after two sessions lost to the same shape: the runtime had the
message, computed it, and put it somewhere nobody looks.

(a) Every dispatcher error funnels through ``_error_envelope``. It counted the outcome metric
    (which is how the app noticed three syscalls failing at all) and returned the message inside
    a dict — which a correctly defensive caller (``!= "success"`` → ``return 0``) discards. Eleven
    of thirteen paths logged nothing. Now the funnel logs once, at WARNING.

(c) ``_ensure_tools_loaded`` is the ONLY plugin-load entry point in the Nodus worker subprocess.
    A load failure there was ``logger.debug``, and the run continued on a registry holding only
    the runtime's own syscalls — so the caller's first symptom was ``"Unknown syscall"`` for a
    name correctly registered in the parent. Now WARNING, naming the manifest, once per distinct
    failure; and the worker's Unknown-syscall error says why when the process knows.

★ These tests drive the real entry points (the dispatcher, the loader, the worker dispatch), not
the log call. ``caplog`` is fine here: every path under test runs synchronously on the calling
thread (the CLAUDE.md caveat is about worker threads).
"""
from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.runtime_only

_DISPATCHER_LOGGER = "AINDY.kernel.syscall_dispatcher"
_REGISTRY_LOGGER = "AINDY.agents.tool_registry"


# ── harness ──────────────────────────────────────────────────────────────────


def _ctx(capabilities=None):
    from AINDY.kernel.syscall_registry import SyscallContext

    return SyscallContext(
        execution_unit_id="eu-fr25",
        user_id="u-fr25",
        capabilities=list(capabilities or ["memory.read"]),
        trace_id="trace-fr25",
        memory_context={},
        metadata={},
    )


def _dispatch(name, handler=None, *, capability="memory.read", capabilities=None):
    """Dispatch through the real dispatcher; register ``handler`` under ``name`` if given."""
    from AINDY.kernel.syscall_dispatcher import SyscallDispatcher
    from AINDY.kernel.syscall_registry import SyscallEntry

    registry_patch = {}
    if handler is not None:
        registry_patch[name] = SyscallEntry(handler, capability, "fr25 probe")
    with patch.dict("AINDY.kernel.syscall_registry.SYSCALL_REGISTRY", registry_patch, clear=False):
        return SyscallDispatcher().dispatch(name, {}, _ctx(capabilities))


def _dispatcher_records(caplog, *, min_level=logging.WARNING):
    return [
        r for r in caplog.records
        if r.name == _DISPATCHER_LOGGER and r.levelno >= min_level
    ]


# ── (a) the dispatcher funnel ────────────────────────────────────────────────


def test_an_unknown_syscall_is_logged_at_warning_with_the_message_the_caller_gets(caplog):
    caplog.set_level(logging.WARNING, logger=_DISPATCHER_LOGGER)

    envelope = _dispatch("sys.v1.fr25.does_not_exist")

    assert envelope["status"] == "error"
    records = _dispatcher_records(caplog)
    assert len(records) == 1, [r.getMessage() for r in records]
    line = records[0].getMessage()
    assert "sys.v1.fr25.does_not_exist" in line
    assert line.endswith(envelope["error"]), (
        "the log line and the envelope must carry the SAME message — two texts for one "
        "failure will drift, and the operator reads the log while the caller reads the dict"
    )
    assert "eu=eu-fr25" in line and "trace=trace-fr25" in line


def test_a_permission_denial_is_logged_and_names_the_missing_capability(caplog):
    """The operator-actionable case: 'requires capability X' used to exist only in a dict."""
    caplog.set_level(logging.WARNING, logger=_DISPATCHER_LOGGER)

    envelope = _dispatch(
        "sys.v1.fr25.guarded",
        handler=lambda payload, ctx: {"ok": True},
        capability="fr25.secret",
        capabilities=["memory.read"],  # caller lacks fr25.secret
    )

    assert envelope["status"] == "error"
    assert "Permission denied" in envelope["error"]
    records = _dispatcher_records(caplog)
    assert len(records) == 1
    assert "fr25.secret" in records[0].getMessage()


def test_a_handler_exception_is_logged_exactly_once_not_twice(caplog):
    """The generic-exception path already logged (with a traceback). The funnel must not add a
    second line for it — 'once' is the property the app asked for, and a double line is how a
    reader concludes two things failed."""
    caplog.set_level(logging.WARNING, logger=_DISPATCHER_LOGGER)

    def _boom(payload, ctx):
        raise RuntimeError("fr25 handler exploded")

    envelope = _dispatch("sys.v1.fr25.boom", handler=_boom)

    assert envelope["status"] == "error"
    records = _dispatcher_records(caplog)
    assert len(records) == 1, [r.getMessage() for r in records]
    assert records[0].exc_info is not None, "the traceback is the reason this site keeps its own log"


def test_a_successful_dispatch_logs_nothing_at_warning(caplog):
    """Absence assertion with a liveness control: the error path first, so a silent instrument
    cannot pass this by seeing nothing at all."""
    caplog.set_level(logging.WARNING, logger=_DISPATCHER_LOGGER)

    _dispatch("sys.v1.fr25.control_error")
    assert _dispatcher_records(caplog), "liveness control: the instrument sees the error path"
    caplog.clear()

    envelope = _dispatch("sys.v1.fr25.fine", handler=lambda payload, ctx: {"ok": True})

    assert envelope["status"] == "success"
    assert _dispatcher_records(caplog) == []


def test_a_site_may_declare_already_logged_only_if_it_logged():
    """Source-derived census (a supplement, not the coverage): a `_error_envelope(` call site
    may pass `already_logged=True` ONLY when a `logger.<level>(...)` call precedes it in the
    same statement block — otherwise the flag re-silences the path this fix exists to surface.

    ★ Derived, not enumerated. The first draft pinned the count at the app team's "two sites
    log"; the real number was FIVE (handler error, malformed outcome claim and stable output
    mismatch all log too), which the behavioural test above found and a literal would have
    hidden — variant 12 in miniature.
    """
    import ast
    import inspect

    from AINDY.kernel import syscall_dispatcher

    tree = ast.parse(inspect.getsource(syscall_dispatcher))
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def _is_logger_call(stmt: ast.stmt) -> bool:
        return (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Call)
            and isinstance(stmt.value.func, ast.Attribute)
            and isinstance(stmt.value.func.value, ast.Name)
            and stmt.value.func.value.id == "logger"
        )

    def _logged_in_same_block(call: ast.Call) -> bool:
        # climb to the statement holding the call, then to the block (list) holding that
        stmt = call
        while not isinstance(stmt, ast.stmt):
            stmt = parents[stmt]
        block_owner = parents[stmt]
        for field in ("body", "orelse", "finalbody", "handlers"):
            block = getattr(block_owner, field, None)
            if isinstance(block, list) and stmt in block:
                return any(_is_logger_call(s) for s in block[: block.index(stmt)])
        return False

    sites = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_error_envelope"
    ]
    assert len(sites) >= 13, f"census liveness: the app counted 13 paths; found {len(sites)}"

    declared = [s for s in sites if any(k.arg == "already_logged" for k in s.keywords)]
    silent = [s for s in sites if s not in declared]
    assert declared, "census liveness: no site declares already_logged — the flag went unused"
    assert silent, "census liveness: every site declares it — the funnel log is dead code"

    unjustified = [s.lineno for s in declared if not _logged_in_same_block(s)]
    assert not unjustified, (
        f"lines {unjustified} pass already_logged=True with no logger call in the same block — "
        "that re-silences the path. Log there, or drop the flag."
    )
    redundant = [s.lineno for s in silent if _logged_in_same_block(s)]
    assert not redundant, (
        f"lines {redundant} log on their own AND fall through to the funnel log — that is the "
        "double line the flag exists to prevent. Pass already_logged=True."
    )


# ── (c) the plugin loader, and the point of use ──────────────────────────────


@pytest.fixture
def _clean_loader(monkeypatch):
    """Reset the recorded failure so tests do not see each other's."""
    import AINDY.agents.tool_registry as tr

    monkeypatch.setattr(tr, "_LAST_PLUGIN_LOAD_FAILURE", None)
    monkeypatch.setattr(tr, "_LOADING_PLUGINS", False)
    yield tr


def _failing_load(message="No module named 'apps'"):
    def _raise(*_a, **_k):
        raise ModuleNotFoundError(message)

    return _raise


def _registry_records(caplog, level):
    return [r for r in caplog.records if r.name == _REGISTRY_LOGGER and r.levelno == level]


def test_a_plugin_load_failure_is_a_warning_that_names_the_manifest_and_the_cause(
    caplog, _clean_loader
):
    tr = _clean_loader
    caplog.set_level(logging.DEBUG, logger=_REGISTRY_LOGGER)

    with patch("AINDY.platform_layer.registry.load_plugins", _failing_load()):
        tr._ensure_tools_loaded()  # must not raise — the fallback is correct and stays

    warnings = _registry_records(caplog, logging.WARNING)
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
    line = warnings[0].getMessage()
    assert "manifest=" in line, "ask 2: say what was being loaded"
    assert "ModuleNotFoundError: No module named 'apps'" in line, "ask 1: the cause, at a visible level"
    assert "runtime-only registry" in line
    assert tr.last_plugin_load_failure() == "ModuleNotFoundError: No module named 'apps'"


def test_a_persistent_failure_warns_once_and_a_new_failure_warns_again(caplog, _clean_loader):
    """`_ensure_tools_loaded` is re-entered on every tool call; an unconditional WARNING is a
    flood. Once per distinct message, DEBUG for the repeat, WARNING again when the cause moves."""
    tr = _clean_loader
    caplog.set_level(logging.DEBUG, logger=_REGISTRY_LOGGER)

    with patch("AINDY.platform_layer.registry.load_plugins", _failing_load("first")):
        tr._ensure_tools_loaded()
        tr._ensure_tools_loaded()
        tr._ensure_tools_loaded()
    assert len(_registry_records(caplog, logging.WARNING)) == 1
    assert len(_registry_records(caplog, logging.DEBUG)) >= 2, "the repeats are still recorded, quietly"

    with patch("AINDY.platform_layer.registry.load_plugins", _failing_load("second")):
        tr._ensure_tools_loaded()
    assert len(_registry_records(caplog, logging.WARNING)) == 2
    assert tr.last_plugin_load_failure() == "ModuleNotFoundError: second"


def test_a_successful_load_clears_the_recorded_failure(_clean_loader):
    tr = _clean_loader
    with patch("AINDY.platform_layer.registry.load_plugins", _failing_load()):
        tr._ensure_tools_loaded()
    assert tr.last_plugin_load_failure() is not None

    with patch("AINDY.platform_layer.registry.load_plugins", lambda *a, **k: []):
        tr._ensure_tools_loaded()
    assert tr.last_plugin_load_failure() is None, (
        "a stale failure would make the worker blame the plugin stack for a genuinely wrong name"
    )


def test_the_worker_says_why_a_syscall_is_unknown_when_the_plugin_stack_failed(_clean_loader):
    """Ask 3, driven through the real worker dispatch: the same 'Unknown syscall' that cost the
    app team a session now carries the cause the process already knew."""
    from AINDY.runtime.nodus_worker import dispatch_worker_syscall

    with patch("AINDY.platform_layer.registry.load_plugins", _failing_load()):
        result = dispatch_worker_syscall("sys.v1.fr25.app_only", {}, user_id="u-fr25")

    assert result["status"] == "error"
    assert result["error"].startswith("Unknown syscall: 'sys.v1.fr25.app_only'")
    assert "app plugin stack failed to load" in result["error"]
    assert "No module named 'apps'" in result["error"]


def test_the_worker_does_not_blame_the_plugin_stack_when_it_loaded(_clean_loader):
    """Control: a wrong name with a healthy loader is just a wrong name."""
    from AINDY.runtime.nodus_worker import dispatch_worker_syscall

    with patch("AINDY.platform_layer.registry.load_plugins", lambda *a, **k: []):
        result = dispatch_worker_syscall("sys.v1.fr25.app_only", {}, user_id="u-fr25")

    assert result["status"] == "error"
    assert result["error"] == "Unknown syscall: 'sys.v1.fr25.app_only'"


@pytest.mark.parametrize(
    "envelope",
    [
        {"status": "success", "data": {"ok": True}, "error": None},
        {"status": "error", "error": "Permission denied: 'x' requires capability y"},
        {"status": "error", "error": None},
        "not a dict",
    ],
)
def test_only_an_unknown_syscall_error_is_annotated(envelope, _clean_loader):
    """The annotation is scoped to the one error it explains. A permission denial with a
    failed plugin stack is still a permission denial."""
    import AINDY.agents.tool_registry as tr
    from AINDY.runtime.nodus_worker import _annotate_unknown_syscall

    tr._LAST_PLUGIN_LOAD_FAILURE = "ModuleNotFoundError: No module named 'apps'"
    assert _annotate_unknown_syscall(envelope) == envelope
