### Added — a pin that cannot be installed now fails at the developer's desk, naming the culprit

A pin can be written, committed and merged while being **impossible to install**, because another
package in the same environment caps it. pip then resolves *down* without complaint, and the only
symptom is that the installed version differs from the declared one — which says nothing about
who is responsible.

`test_no_installed_package_forbids_our_declared_pins` checks every exact pin in `pyproject.toml`
against the stated requirements of every installed distribution, and fails with the offender
named:

```
nodus-mcp requires nodus-lang<5.0.0,>=4.0.0 but we pin ==5.0.0
```

**Found by walking into it.** Bumping `nodus-lang` to 5.0.0 passed locally and failed CI with
`installed nodus-lang 4.2.0 != pinned 5.0.0`. The cause is `nodus-mcp 0.1.2`, which requires
`nodus-lang<5.0.0`; CI installs it *after* `requirements.txt`, so pip silently downgraded
nodus-lang to satisfy it. `pip install nodus-lang==5.0.0 nodus-mcp` is a flat
`ResolutionImpossible`.

Local had been green only because the environment was in a state pip would never produce —
`nodus-lang 5.0.0` force-installed alongside `nodus-mcp 0.1.2`. `pip check` flagged it; nothing
in the test suite did. This closes that gap.

Our own distribution is excluded from the scan: in an editable dev install its recorded metadata
is whatever it was at `pip install -e .` time and goes stale on every pin change, which would
fail for a reason that is not a conflict. `pyproject.toml` is the authority on our own
declaration, and the existing tests already compare it against `AINDY/requirements.txt`.

Same family as `MCP-SDK-2X-1`: an ecosystem package capping a dependency and blocking an upgrade
until it ships a compatible release.
