---
title: "Native Memory Bridge"
last_verified: "2026-08-13"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Native Memory Bridge

The native memory bridge is an optional C++/Rust/Python extension that accelerates memory scoring and semantic similarity computation. It is used by the memory retrieval pipeline when `USE_NATIVE_SCORER=true` (the default in `AINDY/config.py`) and the native module can be imported.

> **Verified against source 2026-08-13** (DOCS-STALE-1). **This document held up better than any
> other on that list.** The three-layer architecture, the full Python interface, every scoring
> coefficient, the module-discovery paths, the build helper and all six failure modes were
> checked line by line and are correct.
>
> Three things were not, all in the build-and-deploy half:
>
> - **`pyo3` is `0.29`, not `0.19`** — ten minor versions and several breaking API generations.
> - **CI now builds the crate.** The Deployment section still said it does not, naming a
>   workflow file that does not exist. `Native Crate Build (Rust)` has been a **required** check
>   since NATIVE-CI-1 closed 2026-08-02, so the document contradicted itself three paragraphs
>   after the note added in #394.
> - **"Rust 1.70+" is unsourced** and almost certainly too low for `pyo3 0.29`.
>
> `AINDY/memory/native/memory_bridge_rs/BUILD.md` names this file as its canonical reference, so
> it is a real downstream consumer of anything wrong here.

---

## Architecture

Three layers communicate through FFI:

```text
Python (AINDY runtime)
    |  calls via pyo3 Python extension module
    v
Rust (memory_bridge_rs)
    |  calls via extern "C" FFI
    v
C++ (memory_cpp/semantic.cpp)
    |- cosine_similarity()
    |- weighted_dot_product()
```

**Why Rust + C++?**

- The Python boundary is implemented with `pyo3` so the extension can expose normal Python classes and functions from a `cdylib`.
- The C++ math kernels already exist in `memory_cpp/semantic.cpp`, so Rust acts as the adapter layer instead of rewriting that code path.
- `cpp_bridge.rs` takes borrowed Rust slices (`&[f64]`) and passes `as_ptr()` plus `len` into the C++ functions. That keeps the FFI surface narrow and avoids per-element marshalling at the call site.

**What the Rust layer owns**

- `MemoryNode`
- `MemoryTrace`
- `score_memory_nodes(...)`
- Input-length validation for `semantic_similarity(...)` and `weighted_dot_product(...)`

`score_memory_nodes(...)` is pure Rust. It does not call into C++; the scoring formula and usage normalization are implemented directly in `src/lib.rs`.

**What the C++ layer owns**

- `cosine_similarity(const double* a, const double* b, size_t len) -> double`
- `weighted_dot_product(const double* values, const double* weights, size_t len) -> double`

The Rust wrapper in `cpp_bridge.rs` asserts matching slice lengths before calling the `unsafe extern "C"` functions. Empty slices are handled in Rust and return `0.0` without calling C++.

---

## Python Interface

Module name: `memory_bridge_rs`

### Classes

#### `MemoryNode`

Constructor:

```python
MemoryNode(content: str, source: str | None, tags: list[str])
```

Fields exposed to Python:

- `id: str` - generated UUID v4
- `timestamp: str` - current UTC timestamp in RFC3339 format
- `content: str`
- `source: str | None`
- `tags: list[str]`
- `children: list[MemoryNode]`

Methods:

- `link(child: MemoryNode) -> None` - appends a child node
- `to_dict() -> dict` - recursive dict export including children

#### `MemoryTrace`

Constructor:

```python
MemoryTrace()
```

State:

- `root_nodes: list[MemoryNode]`

Methods:

- `add_node(node: MemoryNode) -> None`
- `export() -> list[dict]` - exports all root nodes through `MemoryNode.to_dict()`
- `find_by_tag(tag: str) -> list[dict]` - recursive tag search across the tree

### Functions

#### `semantic_similarity(a, b) -> float`

```python
semantic_similarity(a: list[float], b: list[float]) -> float
```

- Requires equal-length vectors
- Returns cosine similarity from the C++ kernel
- Output range is `[-1.0, 1.0]`
- Raises `ValueError` if lengths differ

#### `weighted_dot_product(values, weights) -> float`

```python
weighted_dot_product(values: list[float], weights: list[float]) -> float
```

- Requires equal-length vectors
- Returns the weighted dot product from the C++ kernel
- Raises `ValueError` if lengths differ

#### `score_memory_nodes(...) -> list[float]`

```python
score_memory_nodes(
    similarities: list[float],
    recencies: list[float],
    success_rates: list[float],
    usage_frequencies: list[float],
    graph_bonuses: list[float],
    impact_scores: list[float],
    trace_bonuses: list[float],
    low_value_flags: list[bool],
) -> list[float]
```

All input lists must be the same length or the function raises `ValueError`.

For each node, the Rust scorer computes:

```text
success_weight = 0.25 if usage_frequency > 5.0 else 0.20
impact_bonus   = clamp(impact_score / 5.0, 0.0, 1.0) * 0.15
normalized_usage = clamp(log(1 + usage_frequency) / log(101), 0.0, 1.0)

score =
    similarities      * 0.40
  + recencies         * 0.15
  + success_rates     * success_weight
  + normalized_usage  * 0.10
  + graph_bonuses     * 0.15
  + impact_bonus
  + trace_bonuses

if low_value_flag:
    score *= 0.5
```

`normalize_usage(...)` uses natural log scale and caps the result to `[0.0, 1.0]` with `log1p(value) / log(101)`.

---

## Runtime Control

```text
USE_NATIVE_SCORER=true   # default
USE_NATIVE_SCORER=false  # force Python fallback
```

Current behavior is split across two places:

- `AINDY/config.py` defines `USE_NATIVE_SCORER: bool = True`
- `AINDY/runtime/memory/native_scorer.py` actually checks `os.getenv("USE_NATIVE_SCORER", "true")`

The fallback scorer lives in `AINDY/runtime/memory/scorer.py`.

Runtime flow:

1. `MemoryScorer.score(...)` calls the **module-level** `_score_nodes(prepared_nodes)` in
   `scorer.py:89` (*not* a method — corrected 2026-08-13), which calls
   `AINDY/runtime/memory/native_scorer.py::score_memory_nodes(...)`
2. The native scorer checks whether native scoring is enabled
3. It lazily imports `memory_bridge_rs` from `target/release` or `target/debug`
4. If the module is disabled, unavailable, or raises at runtime, it returns a fallback result
5. `scorer.py` then computes scores in pure Python with `_score_node_python(...)`

The Python fallback implements the same coefficients and the same usage normalization formula as the Rust scorer. The practical difference is performance, not scoring semantics.

The native bridge is therefore optional at runtime. If it is not built, the scorer still works.

---

## Build Requirements

| Tool | Version | Purpose |
|---|---|---|
| Rust toolchain | see note | Compile Rust crate |
| cargo | bundled with Rust | Build system |
| maturin | `>=1.0` (from the crate's `pyproject.toml`) | Build Python extension from Rust |
| C++ compiler | GCC/Clang/MSVC | Compile `semantic.cpp` via the `cc` crate |
| Python | 3.11+ | Target Python environment |

Crate configuration, read from `Cargo.toml` on 2026-08-13:

- **`pyo3 = "0.29"`, features `["extension-module"]`** — *corrected: this said `0.19`.*
  Ten minor versions apart, spanning several breaking pyo3 API generations, so the old figure was
  not a rounding error.
- also `serde 1.0` (`derive`), `uuid 1` (`v4`), `chrono 0.4` (`serde`); build-dependency `cc = "1"`
- crate type is `cdylib`, lib name `memory_bridge_rs`
- `build.rs` compiles `memory_cpp/semantic.cpp`
- build backend is `maturin`, bindings `pyo3` (crate-local `pyproject.toml`)

> **On the Rust version.** This table said **1.70+**. Nothing in the repo states a minimum:
> `Cargo.toml` declares no `rust-version`, and the CI job relies on the Rust preinstalled on
> `ubuntu-latest` rather than pinning a toolchain. Given `pyo3 0.29`, 1.70 is almost certainly
> too low — but rather than substitute one unsourced number for another, the requirement is left
> unstated. **Adding `rust-version` to `Cargo.toml` would make it checkable**, and `cargo build
> --locked` in CI would then enforce it.

### What CI builds

`Native Crate Build (Rust)` in `.github/workflows/runtime-ci.yml` runs
`cargo build --locked --release` on every PR and is a **required status check** on `main`
(NATIVE-CI-1, closed 2026-08-02). `--locked` is the point: it proves the committed `Cargo.lock`
is the one that builds.

It runs on Linux only, and there is **no `cargo test`** — pyo3's `extension-module` omits
libpython, so a test harness fails to *link*. The job proves the crate compiles; it asserts
nothing about behaviour. See the Validation section.

For a local quick-reference that defers to this document, see
`AINDY/memory/native/memory_bridge_rs/BUILD.md`.

---

## Build Instructions

### Linux / macOS

```bash
# Install Rust (if not installed)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Install maturin into the project's Python environment
pip install maturin

# Build and install the extension
cd AINDY/memory/native/memory_bridge_rs
maturin develop --release

# Or from the repo root:
maturin develop \
  --manifest-path AINDY/memory/native/memory_bridge_rs/Cargo.toml \
  --release
```

### Windows (PowerShell)

```powershell
# Run the helper script from the repo root:
.\AINDY\memory\native\memory_bridge_rs\rebuild_native.ps1

# Or manually:
cd AINDY\memory\native\memory_bridge_rs
cargo build --release
python -m maturin develop -m Cargo.toml --release
```

### Docker

Single-stage example:

```dockerfile
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

RUN pip install maturin

WORKDIR /app
COPY . .

RUN maturin develop \
    --manifest-path AINDY/memory/native/memory_bridge_rs/Cargo.toml \
    --release
```

Multi-stage example:

```dockerfile
FROM python:3.11-slim AS native-builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

WORKDIR /app
COPY . .
RUN pip install maturin \
    && maturin build \
        --manifest-path AINDY/memory/native/memory_bridge_rs/Cargo.toml \
        --release

FROM python:3.11-slim

WORKDIR /app
COPY . .
COPY --from=native-builder /app/AINDY/memory/native/memory_bridge_rs/target/wheels /tmp/wheels

RUN pip install /tmp/wheels/*.whl \
    && rm -rf /tmp/wheels
```

`maturin develop` installs directly into the active Python environment. `maturin build` produces a wheel that can be installed into the runtime image.

---

## Validation

After building, verify the extension loads and produces correct output:

```python
import memory_bridge_rs

score = memory_bridge_rs.semantic_similarity([1.0, 0.0], [1.0, 0.0])
assert abs(score - 1.0) < 1e-6, f"Expected 1.0, got {score}"

score = memory_bridge_rs.semantic_similarity([1.0, 0.0], [0.0, 1.0])
assert abs(score - 0.0) < 1e-6, f"Expected 0.0, got {score}"

print("Native bridge OK")
```

> **Corrected 2026-08-13.** This section read *"Focused tests that exist in this
> repository"* and named `tests/integration/test_memory_native_scorer.py` and
> `tests/integration/test_memory_bridge.py`. **Neither has ever existed** in this repo or in
> `aindy-apps-monolith`, and no file under `tests/` references `memory_bridge_rs` at all.
>
> This is consistent with `TECH_DEBT.md` **NATIVE-CI-1**, which records that the crate has no
> Rust tests either. The snippet above is the only executable check for the native path today.
>
> What CI *does* run, since NATIVE-CI-1 closed 2026-08-02, is `Native Crate Build (Rust)` in
> `.github/workflows/runtime-ci.yml` — `cargo build --locked --release`. It proves the crate
> compiles against the committed `Cargo.lock`; it asserts nothing about scoring behaviour.

---

## Failure Modes and Recovery

### Import failure on first use

`AINDY/runtime/memory/native_scorer.py` imports `memory_bridge_rs` lazily the first time `score_memory_nodes(...)` runs. Import does not happen at module import time.

- **Effect**: `native_scorer.py` returns `{"scores": None, "engine": "python", "fallback_used": True, "error": "unavailable"}` and `AINDY/runtime/memory/scorer.py` computes scores in pure Python.
- **Detection**: log line from `AINDY/runtime/memory/native_scorer.py` similar to `[MemoryNativeScorer] native bridge unavailable: ...`
- **Recovery**: set `USE_NATIVE_SCORER=false` to force the Python path, or rebuild the extension so `memory_bridge_rs` becomes importable

### Native scorer disabled

- **Effect**: the native scorer is bypassed intentionally and the Python scorer is used
- **Detection**: `native_scorer.py` reports fallback reason `disabled`
- **Recovery**: unset `USE_NATIVE_SCORER=false` or set `USE_NATIVE_SCORER=true`

### Runtime exception inside the native module

- **Effect**: `native_scorer.py` logs a warning, increments its error counter, and falls back to the Python scorer for that call
- **Detection**: warning log `[MemoryNativeScorer] native scoring failed, falling back to Python: ...`
- **Recovery**: leave the process running on the Python fallback or disable native scoring explicitly while investigating

### Wrong Python version

The extension is built against the Python ABI used by `maturin`.

- **Detection**: `ImportError` when importing `memory_bridge_rs` even though the build completed on another interpreter
- **Recovery**: rebuild the extension with the same Python version used by the runtime process

### C++ compiler missing

`build.rs` invokes the `cc` crate, which then calls the system C++ compiler.

- **Detection**: `cargo build` or `maturin develop` fails during the `build.rs` phase with a compiler lookup error
- **Recovery (Linux)**: install `build-essential` or `gcc-c++`
- **Recovery (macOS)**: run `xcode-select --install`
- **Recovery (Windows)**: install Visual Studio Build Tools with the C++ workload

### Segfault or incorrect results

The Rust wrappers assert length equality before calling C++, but the FFI call itself is still `unsafe`.

- **Detection**: process crash, native abort, or mathematically incorrect output from the validation script
- **Recovery**: set `USE_NATIVE_SCORER=false` to bypass the extension and investigate `AINDY/memory/native/memory_bridge_rs/memory_cpp/semantic.cpp`

---

## Deployment Notes

### CI/CD

> **Corrected 2026-08-13 — this paragraph was obsolete and named a file that does not exist.**
> It read: *"The current GitHub Actions workflow in `.github/workflows/ci.yml` does not install
> Rust, `cargo`, or `maturin`, and it does not build `memory_bridge_rs`."* There is no
> `.github/workflows/ci.yml`; the workflow is `runtime-ci.yml`, and since NATIVE-CI-1 closed on
> 2026-08-02 it **does** build the crate, as a required check. The statement also contradicted
> the note added to the Validation section above.

CI **compiles** the crate (`cargo build --locked --release`, Linux) but does not **install** it
into the Python environment — no `maturin develop` step exists. So the conclusion below still
holds, for a narrower reason than the old text gave:

Python test environments run on the fallback scorer because `memory_bridge_rs` is never
*installed* there, not because CI disables `USE_NATIVE_SCORER`. The distinction matters: a build
regression is now caught, a **scoring** regression still is not.

If native *behavioural* coverage is needed in CI — the build job added by NATIVE-CI-1 does
not provide it — a dedicated job would install Rust, build the extension, and run tests that
**would first have to be written**; the files below are illustrative names, not existing paths.

Example:

```yaml
native-scorer-test:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: dtolnay/rust-toolchain@stable
    - uses: actions/setup-python@v5
      with:
        python-version: "3.11"
    - run: pip install maturin pytest
    - run: maturin develop --manifest-path AINDY/memory/native/memory_bridge_rs/Cargo.toml --release
    - run: pytest tests/integration/test_memory_native_scorer.py -q   # ← does not exist yet
```

Note the job would also need `maturin develop` (as above) rather than `cargo build` — installing
the extension is exactly the step the current CI job omits.

### Production deployment

- Build the extension during image build, not at container start
- Keep the build Python and runtime Python versions aligned
- Prefer a multi-stage image so Rust and the C++ toolchain stay out of the final runtime layer
- If the extension is not present at runtime, the system still works through the Python fallback path in `AINDY/runtime/memory/scorer.py`
