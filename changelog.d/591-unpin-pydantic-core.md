### Changed — `pydantic-core` is no longer pinned by this package (#591)

- **`pydantic_core==` is removed from `pyproject.toml` and `AINDY/requirements.txt`.** Consumers
  resolving against `aindy-runtime` no longer have to satisfy an exact `pydantic-core` version
  from us; pip derives it from `pydantic`, which fixes it with `==` of its own. Anyone who was
  holding `pydantic-core` to our number to keep a resolution working can stop.
- **Reproducibility is unchanged.** It never came from our line — it comes from `pydantic`'s own
  exact requirement. Ours was a second, unauthoritative copy of a value we do not choose, and a
  hand-maintained copy of a derived value can only drift from the thing deriving it. It drifted
  three times in a week: #575, #584 and #590 each failed with `ResolutionImpossible`.
- **Why grouping did not fix it, since #583 shipped claiming it would:** a dependabot group bumps
  whichever members have updates, *each to its own latest*. For an `==`-linked pair that is valid
  only in the coincidence where the newest `pydantic-core` is the exact one the newest `pydantic`
  pins — so it fails whether it moves one member or both. The group is kept only as a guard rail
  in case the pin is ever re-added.
- **The convention this makes an exception to:** `AINDY/requirements.txt` otherwise pins
  transitives exactly. The rule that survives is **pin what you choose, never what is derived** —
  a transitive whose version is fixed by an exact requirement upstream must not be pinned here.
- Recorded under `DEP-UPGRADE-DEFERRED-1`, including that
  `tests/unit/test_dependency_pin_agreement.py::test_no_installed_package_forbids_our_declared_pins`
  would have caught all three at a developer's desk and was not run on any of them.
