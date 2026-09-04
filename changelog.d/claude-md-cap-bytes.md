### Fixed — the registry size guard measured characters while its constant said bytes

- `tests/unit/test_debt_registry_accuracy.py` capped registry entries with `len(line)` —
  **characters** — while the constant was named `_MAX_ENTRY_BYTES` and the failure message
  printed "B". Every measurement of `CLAUDE.md`'s growth in that file is in bytes, so the guard
  enforced a different quantity from the policy it implements.
- **It drifted in the loose direction for a specific reason:** these entries are dense with the
  multibyte characters this file uses most (`★`, `—`, `→`), so the effective byte budget ran
  systematically above the stated one. **Four entries sat over the documented cap with the guard
  green.** Those are trimmed, and the caps are ratcheted to the new high-water mark (1150→1144,
  850→833).
- Also corrects `FS-SCOPE-1`: `EXEC-ENV-BIND-1` phase 3 gave the tool seam a scoped `cwd`, which
  is a default *location*, not a boundary — `roots` is still unenforced there, and a bare
  subprocess can open any path the OS allows. The entry previously read as though the remaining
  work was only "the other seams".
