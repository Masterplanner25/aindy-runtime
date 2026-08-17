"""NODUS-UPGRADE-1 — the nodus surface this runtime depends on, asserted against the real VM.

`nodus-lang` is pinned **exactly** (`==`), so an app cannot adopt a nodus release on its own and
bumping promptly is the runtime's obligation. `CLAUDE.md` carries a prose checklist of what to
re-verify before each bump. Prose checklists are the thing this repo keeps catching itself on —
`DOCS-COVERAGE-CLAIM-1`, `ROUTE-AST-UNWIRED-1` — so this file is that checklist, executable.

Every assertion here is against the **installed nodus package**, never a mock. That is the whole
point: `GUEST-CONFINE-1`'s note is that *"a renamed argument leaves the guest unconfined while
every VM-mocking test still passes"*.

What this file does **not** cover, because other files do it better by driving real scripts:

* that a confined guest is actually denied subprocess/network/env — `test_guest_confinement.py`
* that idiomatic `import "std:sys"` fails loud rather than reaching AINDY —
  `test_nodus_std_sys_guard.py`

Those prove behaviour. This one proves the *surface those behaviours are built on* still exists,
so a bump that moves it fails here with a name rather than there with a mystery.
"""
from __future__ import annotations

import inspect

import pytest

pytestmark = pytest.mark.runtime_only

_DENY_FLAGS = ("allow_subprocess", "allow_network", "allow_env")


@pytest.fixture(scope="module")
def runtime_cls():
    from nodus.runtime.embedding import NodusRuntime

    return NodusRuntime


# --------------------------------------------------------------------------------------
# ★ Guest confinement (GUEST-CONFINE-1)
# --------------------------------------------------------------------------------------


def test_the_three_deny_flags_are_still_accepted(runtime_cls):
    """`nodus_worker` passes these three; if any is renamed, the guest runs unconfined."""
    parameters = inspect.signature(runtime_cls.__init__).parameters

    missing = [flag for flag in _DENY_FLAGS if flag not in parameters]

    assert not missing, (
        f"NodusRuntime no longer accepts {missing}. `nodus_worker` passes these to confine the "
        f"guest VM; without them a guest script reaches subprocess, network and host env "
        f"without touching the dispatcher, token, ledger or egress guard."
    )


def test_the_constructor_takes_no_kwargs_catch_all(runtime_cls):
    """★ The assertion that makes a rename *loud* instead of silent.

    If `NodusRuntime.__init__` grew a `**kwargs`, passing `allow_subprocess=False` to a version
    that had renamed the flag would be **silently swallowed** — the call succeeds, the guest is
    unconfined, and every test that mocks the VM still passes. `GUEST-CONFINE-1` was found by
    demonstration (a guest script wrote a file on the host and did real DNS), not by a failing
    test, precisely because nothing was watching this.

    With no catch-all, a rename raises `TypeError` at construction and the worker fails closed.
    That property is worth an explicit test because it is a property of *someone else's* code
    that we depend on and do not control.
    """
    parameters = inspect.signature(runtime_cls.__init__).parameters

    catch_all = [n for n, p in parameters.items() if p.kind is p.VAR_KEYWORD]

    assert not catch_all, (
        f"NodusRuntime.__init__ now accepts {catch_all}. A renamed confinement flag would be "
        f"silently ignored rather than raising, which is exactly how GUEST-CONFINE-1 went "
        f"unnoticed. Re-verify confinement against the real VM before accepting this bump."
    )


def test_the_deny_flags_are_keyword_only(runtime_cls):
    """The worker passes them by name. Positional acceptance would let an argument reorder
    silently change which one is being denied."""
    parameters = inspect.signature(runtime_cls.__init__).parameters

    positional = [
        flag for flag in _DENY_FLAGS if parameters[flag].kind is not parameters[flag].KEYWORD_ONLY
    ]

    assert not positional, f"these confinement flags are no longer keyword-only: {positional}"


def test_nodus_still_defaults_to_denying(runtime_cls):
    """★ This test caught the 5.0.0 change on the first run, and it was the *good* kind.

    Until 5.0.0 nodus shipped these permissive (`True`), so deny-by-default was **ours** —
    applied per construction in `nodus_worker`, and the only thing standing between a guest
    script and the host. The original version of this test asserted exactly that, and said in
    its own docstring that a flip to `False` would be a good failure to have to read.

    **nodus 5.0.0 flipped them to `False`.** The worker's explicit arguments are now
    belt-and-braces rather than load-bearing, which is a genuine upstream improvement.

    Kept, inverted, because the *new* proposition is worth guarding: if a later nodus reverts to
    permissive defaults, any construction path that forgets the flags silently unconfines its
    guest. `nodus_worker` passes them regardless — deliberately, so this repo never depends on
    someone else's default — but a revert is something we would want to learn from a red test
    rather than from a demonstration, which is how `GUEST-CONFINE-1` was found the first time.
    """
    parameters = inspect.signature(runtime_cls.__init__).parameters

    defaults = {flag: parameters[flag].default for flag in _DENY_FLAGS}

    assert all(value is False for value in defaults.values()), (
        f"nodus confinement defaults are now {defaults}, i.e. permissive again. `nodus_worker` "
        f"still passes all three explicitly so the guest stays confined — but any other "
        f"construction site is now unconfined by default. Re-read GUEST-CONFINE-1."
    )


# --------------------------------------------------------------------------------------
# The three fragile couplings CLAUDE.md names
# --------------------------------------------------------------------------------------


def test_get_active_vm_still_exists(runtime_cls):
    """`nodus_worker.py:409` calls `runtime._get_active_vm()`.

    A **private** method on someone else's class, so it carries no compatibility promise — which
    is exactly why it needs a test rather than a comment. Nothing else in the repo referenced it.
    """
    assert hasattr(runtime_cls, "_get_active_vm"), (
        "NodusRuntime._get_active_vm is gone; `nodus_worker` uses it to reach the active VM."
    )


def test_call_syscall_is_still_where_the_guard_patches_it():
    """`test_nodus_std_sys_guard` and the worker's fail-loud guard patch this exact path.

    If it moves, the guard silently stops being installed and `import "std:sys"` goes back to
    reaching nodus's own 4-syscall stub without saying so — `NODUS-SYS-SURFACE-1`.
    """
    import nodus.services.syscall_runtime as syscall_runtime

    assert hasattr(syscall_runtime, "call_syscall"), (
        "nodus.services.syscall_runtime.call_syscall moved; the std:sys fail-loud guard patches "
        "this path and would silently no-op."
    )


@pytest.mark.parametrize("builtin_name", ["print", "len", "syscall"])
def test_builtins_still_cannot_be_overridden(runtime_cls, builtin_name):
    """★ The premise `NODUS-SYS-SURFACE-1` rests on, and it was only ever a docstring.

    `test_nodus_std_sys_guard`'s module docstring states *"it cannot be aliased (nodus forbids
    overriding a builtin)"* — and nothing asserted it. If a nodus release allowed overrides, the
    fail-loud guard could be bypassed by a guest redefining `syscall`, and every existing test
    would still pass because they all assume the refusal rather than check it.

    `syscall` is the one that matters; `print` and `len` are included so a failure distinguishes
    *"this builtin became overridable"* from *"the refusal mechanism is gone entirely"*.
    """
    runtime = runtime_cls(allow_subprocess=False, allow_network=False, allow_env=False)

    with pytest.raises(ValueError, match="Cannot override built-in"):
        runtime.register_function(builtin_name, lambda *args: "hijacked", arity=1)


# --------------------------------------------------------------------------------------
# The environment this ran against
# --------------------------------------------------------------------------------------


def test_the_installed_version_matches_the_declared_pin():
    """★ Otherwise every assertion above is about a version we do not ship.

    The pin is exact, so `pip install nodus-lang==X` in a dev environment succeeds and leaves the
    tree inconsistent with `pyproject.toml` — and an editable install moves it back. This session
    found exactly that: `nodus-lang 4.1.0` installed against a `==4.2.0` pin, meaning every local
    run had been exercising a version the runtime does not declare.

    CI installs from `pyproject.toml`, so this can only fail locally — which is the point.
    """
    import pathlib
    import re
    from importlib.metadata import version

    pyproject = (
        pathlib.Path(__file__).resolve().parents[2] / "pyproject.toml"
    ).read_text(encoding="utf-8")
    declared = re.search(r'"nodus-lang==([0-9][^"]*)"', pyproject)

    assert declared, "nodus-lang is no longer pinned exactly in pyproject.toml"
    assert version("nodus-lang") == declared.group(1), (
        f"installed nodus-lang {version('nodus-lang')} != pinned {declared.group(1)}. Local "
        f"results about the nodus surface are not evidence about what this runtime ships. "
        f"Run: pip install nodus-lang=={declared.group(1)}"
    )
