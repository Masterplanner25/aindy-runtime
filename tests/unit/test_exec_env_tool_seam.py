"""`EXEC-ENV-BIND-1` phase 3 — the tool seam asks instead of inheriting.

Phase 1 let an execution unit **declare** an environment and confined nothing. Phase 2 made the
guest path **ask** — `nodus_worker` derives every confinement argument from a spec clamped to
`GUEST_FLOOR`. Phase 3 is the tool seam.

★★ WHAT WAS ACTUALLY OPEN, AND IT IS SHARPER THAN "A PHASE WAS UNFINISHED"
---------------------------------------------------------------------------
`TOOL-SEAM-ISOLATION-1` moved a declared tool **out of the process**. It did not narrow what that
process can **see**. The worker was spawned with no `env=` and no `cwd=`, so it inherited:

- the **entire server environment** — `SECRET_KEY`, `DATABASE_URL`, every provider API key;
- the server's **working directory** — `/home/aindy` in Docker, which holds `alembic/`; the repo
  root in dev, which holds `AINDY/.env`.

Isolation was process-level and never visibility-level, and until a tool could declare otherwise
there was no vocabulary in which to say so. That is the same shape as `GUEST-CONFINE-1`'s
residual — a bound that was an undeclared inherited default — arriving at a second seam.

★ THE PROPERTY EVERY TEST HERE IS REALLY DEFENDING
---------------------------------------------------
**An undeclared tool must be completely unaffected.** `tool_floor()` is today's behaviour written
down, and a floor that changed live tools would be reverted rather than obeyed. So the
most important assertions below are the ones showing that *nothing happens* by default — and
they are paired with declared cases, because "nothing happens" is also what a completely
unwired mechanism does.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.runtime_only


# ── the floor: today's behaviour, written down ───────────────────────────────


def test_the_tool_floor_is_permissive_and_that_is_deliberate():
    """★ A tool is first-party code an operator registered, not submitted content.

    The guest floor exists because a `.nd` script arriving over HTTP is data from an
    authenticated session. That argument does not transfer to a registered tool — and clamping
    tools to the guest floor would break every one that legitimately reads a credential for the
    service it calls. A floor that breaks its subjects gets removed, not obeyed.
    """
    from AINDY.core.execution_environment import (
        ENV_INHERIT,
        FS_HOST,
        NET_OPEN,
        guest_floor,
        tool_floor,
    )

    floor = tool_floor()
    assert floor.visibility.env == ENV_INHERIT
    assert floor.visibility.filesystem == FS_HOST
    assert floor.authority.network == NET_OPEN
    assert floor.authority.subprocess is True
    assert floor != guest_floor(), (
        "the tool floor and the guest floor are the same object. They answer different "
        "questions — deployed code an operator registered vs content submitted over HTTP."
    )


def test_the_floor_produces_no_spawn_kwargs():
    """★★ THE SAFETY PROPERTY THIS WHOLE CHANGE RESTS ON.

    With nothing declared there must be no `env=`, no `cwd=`, and no changed timeout — the
    subprocess is spawned exactly as it was before. If the floor produced kwargs, landing this
    would alter every isolated tool at once.
    """
    from AINDY.core.execution_environment import subprocess_confinement, tool_floor

    assert subprocess_confinement(
        tool_floor(), scratch_root="/tmp/unused", default_timeout_s=120.0
    ) == {}


# ── the translation ──────────────────────────────────────────────────────────


def test_an_env_allowlist_excludes_the_servers_secrets():
    """★★ THE AXIS THAT WAS SILENTLY WIDE OPEN.

    A worker spawned with no `env=` inherits the parent's whole environment, so a tool that
    declared isolation still saw every secret the server holds. Declaring an allow-list produces
    an environment containing only the named variables.
    """
    import os

    from AINDY.core.execution_environment import (
        ENV_ALLOWLIST,
        ExecutionEnvironmentSpec,
        Visibility,
        subprocess_confinement,
    )

    os.environ["SOAK_FAKE_SECRET"] = "do-not-leak"
    os.environ["SOAK_TOOL_KEY"] = "needed"
    try:
        spec = ExecutionEnvironmentSpec(
            visibility=Visibility(env=ENV_ALLOWLIST, env_allow=("SOAK_TOOL_KEY",))
        )
        env = subprocess_confinement(
            spec, scratch_root="/tmp/unused", default_timeout_s=120.0
        )["env"]

        assert env.get("SOAK_TOOL_KEY") == "needed", "the declared variable was not passed"
        assert "SOAK_FAKE_SECRET" not in env, (
            "an undeclared variable reached the worker — the allow-list is not filtering, and a "
            "tool that asked to be confined still sees the server's secrets"
        )
    finally:
        os.environ.pop("SOAK_FAKE_SECRET", None)
        os.environ.pop("SOAK_TOOL_KEY", None)


def test_the_interpreter_can_still_start():
    """★ A confinement that cannot launch is a confinement nobody keeps.

    `PATH` and friends are passed through even under an allow-list. Omit them and the worker
    fails to start, which turns a declaration into a crash — and the declaration then gets
    removed rather than the omission fixed.
    """
    from AINDY.core.execution_environment import (
        ENV_NONE,
        ExecutionEnvironmentSpec,
        Visibility,
        subprocess_confinement,
    )

    spec = ExecutionEnvironmentSpec(visibility=Visibility(env=ENV_NONE))
    env = subprocess_confinement(spec, scratch_root="/tmp/unused", default_timeout_s=120.0)["env"]
    assert "PATH" in env, "PATH was stripped; the worker cannot start"


def test_a_declared_wall_clock_only_narrows():
    """A tool may shorten its own leash, never lengthen it."""
    from AINDY.core.execution_environment import (
        ExecutionEnvironmentSpec,
        Resources,
        subprocess_confinement,
    )

    shorter = ExecutionEnvironmentSpec(resources=Resources(wall_time_ms=5_000))
    assert subprocess_confinement(
        shorter, scratch_root="/tmp/unused", default_timeout_s=120.0
    )["timeout"] == 5.0

    longer = ExecutionEnvironmentSpec(resources=Resources(wall_time_ms=999_000))
    assert subprocess_confinement(
        longer, scratch_root="/tmp/unused", default_timeout_s=120.0
    )["timeout"] == 120.0, "a declared budget lengthened the worker's timeout"


def test_a_scoped_filesystem_gets_the_scratch_root():
    from AINDY.core.execution_environment import (
        FS_SCOPED,
        ExecutionEnvironmentSpec,
        Visibility,
        subprocess_confinement,
    )

    spec = ExecutionEnvironmentSpec(visibility=Visibility(filesystem=FS_SCOPED))
    kwargs = subprocess_confinement(spec, scratch_root="/tmp/scratch-x", default_timeout_s=120.0)
    assert kwargs["cwd"] == "/tmp/scratch-x", (
        "no cwd was set, so the worker keeps inheriting the server's working directory — the "
        "GUEST-CONFINE-1 residual, at a second seam"
    )


# ── the declaration ──────────────────────────────────────────────────────────


def test_a_malformed_declaration_is_refused_at_registration():
    """★ Fails at declare time, not at first call.

    A malformed spec should be a startup error an operator sees, not a runtime refusal the first
    time someone happens to invoke the tool. The guest path can fall back at call time because
    its payload already passed a gate; there is no such gate here.
    """
    from AINDY.agents.tool_registry import register_tool

    with pytest.raises(ValueError) as caught:
        register_tool(
            name="bad_env_tool", risk="low", description="d", capability="c",
            required_capability="rc", category="cat", egress_scope="none",
            env_spec={"visibility": {"filesystem": "not-a-mode"}},
        )
    assert "env_spec" in str(caught.value)


def test_an_undeclared_tool_gets_no_confinement_kwargs():
    """★★ The liveness control for every assertion above, and the deployment safety property.

    An undeclared tool must be spawned exactly as before. This is also the check that would fail
    if the helper started returning kwargs unconditionally.
    """
    import AINDY.agents.tool_registry as tr

    kwargs, scratch = tr._worker_confinement("a_tool_that_declared_nothing")
    assert kwargs == {}
    assert scratch is None


def test_a_declared_tool_gets_confinement_kwargs(monkeypatch):
    """The other half — without this, "no kwargs" would be satisfied by a dead mechanism."""
    import AINDY.agents.tool_registry as tr

    monkeypatch.setitem(
        tr.TOOL_REGISTRY,
        "declared_env_tool",
        {"fn": lambda **kw: None, "env_spec": {"visibility": {"env": "allowlist", "env_allow": []}}},
    )
    kwargs, scratch = tr._worker_confinement("declared_env_tool")
    try:
        assert "env" in kwargs, "a declared allow-list produced no env= for the subprocess"
    finally:
        if scratch is not None:
            scratch.cleanup()


def test_a_declaration_cannot_widen_the_floor(monkeypatch):
    """★ The property that makes accepting a declaration safe at all.

    A spec may only ever narrow. A tool arriving with a *more permissive* descriptor than the
    floor must not get it — which is the same guarantee the guest path relies on, checked here
    because this seam accepts declarations from a different source.
    """
    from AINDY.core.execution_environment import (
        ExecutionEnvironmentSpec,
        Resources,
        clamp_to_floor,
        subprocess_confinement,
        tool_floor,
    )

    # The floor has no wall-clock bound; a declaration asking for a longer one cannot create it.
    declared = ExecutionEnvironmentSpec(resources=Resources(wall_time_ms=10**9))
    effective, _ = clamp_to_floor(declared, tool_floor())

    spawned = subprocess_confinement(
        effective, scratch_root="/tmp/unused", default_timeout_s=120.0
    )
    assert spawned.get("timeout", 120.0) <= 120.0, (
        "a declared wall clock exceeded the caller's own budget — a declaration widened the floor"
    )
