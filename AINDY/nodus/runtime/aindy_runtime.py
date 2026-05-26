"""
AINDYNodusRuntime - NodusRuntime subclass with AINDY-specific extensions.

History: originally written to fix a bug where NodusRuntime.run_source()
accepted host_globals but did not forward it to ModuleLoader. That fix is now
in nodus-lang >= 3.0.2, but this subclass is kept for:
  - register_function() aliases (recall_from, recall_all, share)
  - auto project_root fallback to bundled stdlib
  - memory import rewriting ("import memory" -> "import "memory" as memory")

Bug fixes applied vs the original override:
  - initial_globals now forwarded to load_module_from_source (was silently
    dropped, causing "Undefined variable" for state/user_id/etc in scripts)
  - error handling now returns a failure dict instead of raising, matching
    the base class contract and preserving captured stdout on script error
"""
from __future__ import annotations

import os
import os as _os
import re

from nodus.builtins.nodus_builtins import BuiltinInfo
from nodus.result import Result, normalize_filename
from nodus.runtime.diagnostics import LangRuntimeError, LangSyntaxError, HostFunctionError
from nodus.runtime.embedding import NodusRuntime
from nodus.runtime.errors import coerce_error, legacy_error_dict
from nodus.runtime.module_loader import ModuleLoader
from nodus.tooling.sandbox import capture_output, configure_vm_limits
from nodus.vm.vm import VM

_STDLIB_DIR = _os.path.join(
    _os.path.dirname(_os.path.dirname(__file__)),
    "stdlib",
)


class AINDYNodusRuntime(NodusRuntime):
    """NodusRuntime subclass with AINDY-specific extensions.

    Extends the base class with:
    - auto project_root fallback to the bundled stdlib directory
    - register_function aliases for recall_from / recall_all / share
    - memory import rewriting so bare ``import memory`` works as a namespace import
    - initial_globals and host_globals both forwarded to ModuleLoader (base class
      fix — present in nodus-lang >= 3.0.2, kept here for the other extensions)

    Usage:
        rt = AINDYNodusRuntime()
        rt.register_function("set_state", set_state_fn, arity=2)
        result = rt.run_source(
            script,
            initial_globals={"state": {}, "user_id": uid},
            host_globals={"memory_bridge": bridge},
        )
    """

    def __init__(self, **kwargs):
        if "project_root" not in kwargs:
            kwargs["project_root"] = _STDLIB_DIR if _os.path.isdir(_STDLIB_DIR) else None
        super().__init__(**kwargs)

    def register_function(self, name: str, fn, *, arity: int | tuple[int, ...] | None = None) -> None:
        super().register_function(name, fn, arity=arity)
        stdlib_aliases = {
            "recall_from": "__memory_stdlib_recall_from",
            "recall_all": "__memory_stdlib_recall_all",
            "share": "__memory_stdlib_share",
        }
        alias = stdlib_aliases.get(name)
        if alias:
            super().register_function(alias, fn, arity=arity)

    def run_source(
        self,
        source: str,
        *,
        filename: str | None = None,
        max_steps: int | None = None,
        timeout_ms: int | None = None,
        max_stdout_chars: int | None = None,
        optimize: bool = True,
        import_state: dict | None = None,
        debugger=None,
        max_frames: int | None = None,
        initial_globals: dict | None = None,
        host_globals: dict | None = None,
    ) -> dict:
        """Run a Nodus script with AINDY extensions applied.

        Differences from NodusRuntime.run_source:
        - Rewrites bare ``import memory`` to a namespace import before execution.
        - Forwards both initial_globals and host_globals to ModuleLoader so scripts
          can read injected values (state, user_id, etc.) as module-level variables.
        - Returns a failure dict on script error (matching base class contract)
          rather than raising, preserving captured stdout.

        See NodusRuntime.run_source docstring for parameter documentation.
        """
        self.last_emitted_events: list[dict] = []
        if "memory." in source:
            source = re.sub(
                r'(?m)^(\s*)import\s+"memory"\s*$',
                r'\1import "memory" as memory',
                source,
            )
            source = re.sub(
                r"(?m)^(\s*)import\s+memory\s*$",
                r'\1import "memory" as memory',
                source,
            )
        normalized = normalize_filename(filename)
        if import_state is None and self.project_root is not None:
            import_state = {
                "loaded": set(),
                "loading": set(),
                "exports": {},
                "modules": {},
                "module_ids": {},
                "project_root": self.project_root,
            }
        elif import_state is not None and self.project_root is not None:
            import_state["project_root"] = self.project_root

        vm = VM(
            [],
            {},
            code_locs=[],
            source_path=filename,
            allowed_paths=self.allowed_paths,
            module_globals=initial_globals,
            host_globals=host_globals,
        )
        if not self.allow_input:
            vm.input_fn = self._blocked_input
        if debugger is not None:
            vm.debugger = debugger
            vm.debug = True
        self.last_vm = vm
        host_builtins = {
            name: BuiltinInfo(
                info.name,
                info.arity,
                lambda *args, _fn=info.fn, _vm=vm: self._invoke_host_function(_vm, _fn, *args),
            )
            for name, info in self._host_functions.items()
        }

        resolved_steps = self.max_steps if max_steps is None else max_steps
        resolved_timeout = self.timeout_ms if timeout_ms is None else timeout_ms
        resolved_stdout = self.max_stdout_chars if max_stdout_chars is None else max_stdout_chars
        configure_vm_limits(vm, max_steps=resolved_steps, timeout_ms=resolved_timeout)
        resolved_frames = self.max_frames if max_frames is None else max_frames
        vm.max_frames = resolved_frames

        with capture_output(max_stdout_chars=resolved_stdout) as (stdout, stderr):
            try:
                loader = ModuleLoader(
                    project_root=self.project_root,
                    vm=vm,
                    host_builtins=host_builtins,
                    extra_builtins=set(self._host_functions.keys()),
                    host_globals=host_globals or {},
                    debugger=debugger,
                )
                if filename and os.path.isfile(filename):
                    loader.load_module_from_path(filename, auto_run_main=True, initial_globals=initial_globals)
                else:
                    loader.load_module_from_source(
                        source,
                        module_name=filename or "<memory>",
                        auto_run_main=True,
                        initial_globals=initial_globals,
                    )
            except HostFunctionError as wrapped:
                raise wrapped.cause
            except Exception as err:
                stage = "parse" if isinstance(err, (LangSyntaxError, SyntaxError)) else "execute"
                structured = coerce_error(err, stage=stage, filename=normalized)
                return Result.failure(
                    stage=stage,
                    filename=normalized,
                    stdout=stdout.getvalue(),
                    stderr=stderr.getvalue(),
                    errors=[structured.to_dict()],
                    error=legacy_error_dict(err, filename=normalized),
                ).to_dict()

        # Extract user-emitted events from the VM's event bus.
        # Filter out internal Nodus VM instrumentation events.
        _INTERNAL_EVENT_PREFIXES = ("vm_", "runtime.", "nodus.")
        self.last_emitted_events = [
            e.to_dict()
            for e in vm.event_bus.events()
            if not any(e.type.startswith(p) for p in _INTERNAL_EVENT_PREFIXES)
        ]

        return Result.success(
            stage="execute",
            filename=normalized,
            stdout=stdout.getvalue(),
            stderr=stderr.getvalue(),
        ).to_dict()
