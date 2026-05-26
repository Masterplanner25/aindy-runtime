"""
AINDYNodusRuntime was removed in favour of using nodus.runtime.embedding.NodusRuntime directly.

History of the class:
- Originally patched BUG-E03 (host_globals not forwarded to ModuleLoader in nodus-lang 1.1.0).
- Also added: stdlib project_root defaulting to bundled stdlib, register_function aliases for
  recall_from / recall_all / share → __memory_stdlib_* names, memory import rewriting.
- nodus-lang 3.0.2 fixed BUG-E03 upstream. The alias registration and memory import rewriting
  were inlined into nodus_worker.py (AINDY/runtime/nodus_worker.py) during OVERRIDE-DRIFT-1
  deletion (2026-05-25). The class was removed at that point.

Do not re-introduce a NodusRuntime subclass here without a concrete justification.
Anything that was in AINDYNodusRuntime now lives in nodus_worker.py or the base class.
"""
