### Fixed — `llms.txt` and the Rust source were missing from the distribution

`llms.txt` (12.5 KB) and `llms-full.txt` (22 KB) lived only at the repository root. Neither sits
under `AINDY/`, and `[tool.setuptools.package-data]` cannot match outside the package, so **they
shipped in neither the wheel nor the sdist**. They exist so a model reading the *installed*
package can orient itself; at the repo root they served a reader who had already found the repo,
which is the audience that needed them least.

Both are now at `AINDY/llms.txt` and `AINDY/llms-full.txt`, declared in package-data **and**
`MANIFEST.in` — the wheel takes the first, the sdist the second, and declaring only one is half
a fix. Verified by building and inspecting both artifacts.

**The Rust source now ships in the sdist; the compiled artifact deliberately does not.** The
backend produces a pure-Python `py3-none-any` wheel. A `.pyd`/`.so`/`.dylib` inside one installs
a **broken binary** for every user not on the exact OS, architecture and CPython it was built
with — worse than the current state, in which `native_bridge.py` falls back cleanly to the
Python scorer. `Cargo.toml`, `Cargo.lock`, `build.rs` and `src/*.rs` now travel so the
accelerator can be built locally, and the README says plainly that installed users run the
Python path.

**★ And one the audit did not look for: the sdist was carrying cargo build output.**
`recursive-include AINDY *.json` is path-based, so it matched **~200 fingerprint files** under
the crate's `target/` — measured in the 2.4.0 sdist — some embedding the building machine's
absolute rustup and toolchain paths. `prune` now excludes it.

**This never reached PyPI.** The published 2.3.0 wheel was downloaded and checked: zero
`target/` files, because CI builds in a checkout where `target/` is unpopulated. It is a
local-build hazard — precisely the kind that ships the day someone cuts a release from their own
machine.

*A correction worth recording, since it changes what the fix is doing:* the wheel also showed
100 such files during testing, and a clean rebuild without the new `exclude-package-data` showed
**zero**. Package-data globs only apply to directories setuptools treats as packages, and
`target/` is not one. The wheel's copies came from a stale `build/lib` carrying them across
builds. `prune` is the fix; `exclude-package-data` is belt-and-braces against that staleness.

Before benchmarking any move to per-platform wheels: no comparison of the native scorer against
the Python one exists in this repo, and that measurement should come first.


**`CONTRIBUTORS.md` now ships too.** It records contributions present in this repository and its
own text says it travels with the package — which was not true. A repo-root file cannot reach a
wheel through package-data (that matches only inside `AINDY/`), so it is declared twice:
`include CONTRIBUTORS.md` in `MANIFEST.in` for the sdist, and in `license-files` for the wheel,
where it lands as `dist-info/licenses/CONTRIBUTORS.md`. Verified in both artifacts.
