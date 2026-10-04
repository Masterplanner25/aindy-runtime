"""FS-SCOPE-1 phase 2 — filesystem scope for an isolated tool's worker.

Before this, a tool that declared ``visibility.filesystem = scoped`` (or ``readonly``/``none``) got
one thing at the tool seam: its worker's ``cwd`` was a scratch directory. ``cwd`` is a default
location, not a boundary, so the worker could still open any path the OS allowed.

The shape mirrors egress (EGRESS-INPROC-1, DEC-048..050):

* the PARENT resolves one :class:`FilesystemDecision` from the tool's effective spec (clamped to
  the tool floor, so a declaration can only narrow) and puts it on the worker's request;
* the WORKER installs it process-globally (:func:`install_process_fs_guard`) before the plugin
  stack loads or the tool is resolved, and reports the mechanism it applied;
* the parent puts ``{mode, mechanism}`` on the envelope, so it says what was enforced.

★ **The mechanism is a Python audit hook** (PEP 578): ``open`` (which ``io.open_code`` also
raises, so imports are covered), directory listing, and the mutating ``os``/``shutil`` events. It
cannot be removed once installed, which is what a worker that runs one tool and exits wants.

★★ **What it is not, stated so nobody cites it as more.** It is not a kernel boundary:
a C extension or ``ctypes`` reaching libc directly, and any child process, never raise the
events. It does not change the deployment's assurance (``insecure-dev`` for this tier); the
container runner is the boundary. What it does is turn the declaration into enforcement for the
Python code the tool runs, which is where a tool's own file I/O happens.

★ **The import path stays readable.** The guard is installed before the plugin stack loads and
tool functions import lazily, so every directory on ``sys.path`` (and the interpreter's prefixes)
is readable, never writable. In a source checkout that includes the repo root; in a wheel
install it is site-packages. The worker's ``cwd`` is the scratch root, never the server's.
"""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

MECHANISM_AUDIT_HOOK_WORKER = "audit_hook:worker"
MECHANISM_NONE = "none"

FS_MODES = ("none", "readonly", "scoped")

#: Events that only READ. `open` is classified by its mode and flags.
_READ_EVENTS = frozenset({"os.listdir", "os.scandir"})

#: Events that change the filesystem. Each lists the argument positions holding a path.
_WRITE_EVENTS = {
    "os.remove": (0,),
    "os.rmdir": (0,),
    "os.mkdir": (0,),
    "os.rename": (0, 1),
    "os.link": (0, 1),
    "os.symlink": (0, 1),
    "os.truncate": (0,),
    "os.chmod": (0,),
    "os.chown": (0,),
    "os.chflags": (0,),
    "os.utime": (0,),
    "os.setxattr": (0,),
    "os.removexattr": (0,),
    "shutil.rmtree": (0,),
    "shutil.copyfile": (1,),
    "shutil.copymode": (1,),
    "shutil.copystat": (1,),
    "shutil.move": (1,),
}

_WRITE_FLAGS = (
    os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC | getattr(os, "O_EXCL", 0)
)

#: Device files a library may legitimately read whatever the scope.
_ALWAYS_READABLE = ("/dev/null", "/dev/urandom", "/dev/random")


@dataclass(frozen=True)
class FilesystemDecision:
    """``mode`` is ``none | readonly | scoped``; ``roots`` are the declared roots (already
    clamped); ``scratch`` is the per-execution scratch root, or ``None``."""

    mode: str
    roots: tuple = ()
    scratch: Optional[str] = None

    def readable(self) -> tuple:
        if self.mode == "none":
            return ()
        base = self.roots or ()
        return tuple(base) + ((self.scratch,) if self.scratch else ())

    def writable(self) -> tuple:
        # `scoped`: its roots, and its own scratch root (the worker's cwd, where a relative
        # write lands). `readonly` and `none`: nothing.
        if self.mode != "scoped":
            return ()
        return tuple(self.roots) + ((self.scratch,) if self.scratch else ())

    def to_payload(self) -> dict:
        return {"mode": self.mode, "roots": list(self.roots), "scratch": self.scratch}

    @classmethod
    def from_payload(cls, raw) -> Optional["FilesystemDecision"]:
        if not isinstance(raw, dict):
            return None
        mode = str(raw.get("mode") or "")
        if mode not in FS_MODES:
            return None
        roots = tuple(str(r) for r in (raw.get("roots") or ()) if r)
        scratch = raw.get("scratch")
        return cls(mode=mode, roots=roots, scratch=str(scratch) if scratch else None)


def resolve_filesystem_decision(spec, scratch_root: Optional[str]) -> Optional[FilesystemDecision]:
    """The decision for an EFFECTIVE (already clamped) spec, or ``None`` when it bounds nothing
    (``host``, the tool floor). ``scratch_root`` is the worker's scratch dir."""
    mode = spec.visibility.filesystem
    if mode not in FS_MODES:
        return None
    return FilesystemDecision(mode=mode, roots=tuple(spec.visibility.filesystem_roots), scratch=scratch_root)


def _canonical(path) -> str:
    if isinstance(path, bytes):
        path = os.fsdecode(path)
    return os.path.normcase(os.path.realpath(os.fspath(path)))


def _within(path: str, roots: tuple) -> bool:
    for root in roots:
        try:
            if os.path.commonpath([path, root]) == root:
                return True
        except ValueError:  # different drives on Windows
            continue
    return False


def _import_roots() -> tuple:
    """Directories code is read from: the interpreter's prefixes and every ``sys.path`` entry.
    Read at install time, before the plugin stack can add to ``sys.path``."""
    candidates = {sys.prefix, sys.base_prefix, sys.exec_prefix, getattr(sys, "base_exec_prefix", sys.exec_prefix)}
    candidates.update(p for p in sys.path if p)
    return tuple(sorted({_canonical(p) for p in candidates if os.path.isdir(p)}))


def _is_write_open(mode, flags) -> bool:
    if isinstance(mode, str) and any(c in mode for c in "wax+"):
        return True
    return isinstance(flags, int) and bool(flags & _WRITE_FLAGS)


_INSTALLED: Optional[FilesystemDecision] = None


def install_process_fs_guard(decision: FilesystemDecision) -> str:
    """Enforce ``decision`` for the REST OF THIS PROCESS; return the mechanism to report.

    Called once per worker. A second call is refused (an audit hook cannot be removed, so a
    second one would only narrow, and a worker never needs it).
    """
    global _INSTALLED
    if _INSTALLED is not None:
        return MECHANISM_AUDIT_HOOK_WORKER
    readable = tuple(_canonical(p) for p in decision.readable())
    writable = tuple(_canonical(p) for p in decision.writable())
    code_roots = _import_roots()
    always = tuple(_canonical(p) for p in _ALWAYS_READABLE if os.path.exists(p))

    def _deny(op: str, path) -> None:
        raise PermissionError(
            f"FS-SCOPE-1: {op} of {os.fspath(path)!r} is outside this tool's declared "
            f"filesystem scope ({decision.mode})"
        )

    def _check(op: str, path, *, write: bool) -> None:
        if path is None or isinstance(path, int):
            return  # an already-open descriptor: its open was checked
        p = _canonical(path)
        if write:
            if not _within(p, writable):
                _deny(op, path)
            return
        if _within(p, readable) or _within(p, code_roots) or p in always:
            return
        _deny(op, path)

    def _hook(event: str, args) -> None:
        if event == "open":
            path, mode, flags = (tuple(args) + (None, None, None))[:3]
            _check("open", path, write=_is_write_open(mode, flags))
        elif event in _READ_EVENTS:
            _check(event, args[0] if args else None, write=False)
        elif event in _WRITE_EVENTS:
            for i in _WRITE_EVENTS[event]:
                if i < len(args):
                    _check(event, args[i], write=True)

    if decision.scratch and decision.mode == "scoped":
        # A library's temp file should land somewhere this scope can write.
        import tempfile

        tempfile.tempdir = decision.scratch
    # Bytecode writes into the import path would be refused (and swallowed by importlib);
    # skip them rather than pay for the refusal on every import.
    sys.dont_write_bytecode = True
    sys.addaudithook(_hook)
    _INSTALLED = decision
    logger.info(
        "[fs_guard] process filesystem scope installed: %s roots=%s", decision.mode, list(decision.roots)
    )
    return MECHANISM_AUDIT_HOOK_WORKER
