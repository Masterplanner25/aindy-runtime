---
title: "Runtime Release Checklist"
last_verified: "2026-08-19"
api_version: "1.0"
status: current
owner: "platform-team"
---
# Runtime Release Checklist

Operational verification checklist for each `aindy-runtime` release.  Run these steps
against the built artifact in a clean environment before publishing.  This is a
verification checklist — not a release-gate policy (see `RELEASE_GATES.md` for that).

> **Before tagging or bumping the version:** confirm the full CI pipeline has passed on
> the exact commit being tagged — not just the three required branch-protection checks
> (Lint, Docs Validation, Runtime Contracts).  The full pipeline includes:
>
> - `Integration Tests (PostgreSQL + Redis)` — Alembic migration + integration suite
> - `Platform UI Build` — Vite build produces a clean dist
> - `Runtime Package Build` — wheel builds and passes `twine check`
> - `Install Smoke Test` — wheel installs cleanly, entry points and sandbox surface verified
>
> All four must be green on the release commit before creating the tag.

---

## Pre-Publish (Source + Artifact)

### 1. Schema Contract

```bash
python scripts/check_schema_version.py
# Expected: "Schema version baseline updated." or "Schema version matches baseline."
# Fail: schema drift — bump SCHEMA_CONTRACT_VERSION and re-run
```

### 2. Unit Test Suite

```bash
pytest tests/unit/ -q
# Expected: 0 failures (9 known CLI subprocess failures are pre-existing — binary not on PATH)
```

### 3. Build Artifacts

```bash
python -m build
python -m twine check dist/*
# Expected: PASSED for both wheel and sdist
```

### 4. Installed-Artifact Smoke

```bash
pip install --prefix=/tmp/aindy-install dist/aindy_runtime-*.whl
AINDY_INSTALL=/tmp/aindy-install/lib/python*/site-packages
PYTHONPATH=$AINDY_INSTALL $AINDY_INSTALL/bin/aindy-runtime --version
# Expected: prints the version string, exit 0
PYTHONPATH=$AINDY_INSTALL $AINDY_INSTALL/bin/aindy-runtime --help
# Expected: shows help, exit 0
```

### 5. Packaging Assertions

```bash
pytest tests/unit/test_runtime_packaging.py -v
# Covers: wheel includes runtime_plugins.json, version.json, nodus stdlib assets
# Covers: console_scripts, dist-info METADATA fields
```

---

## Post-Deploy Verification (Runtime)

### 6. Health Endpoints

```bash
curl -s http://localhost:8000/health | jq '.status'
# Expected: "healthy" (or "degraded" in partial-infra profiles)

curl -s http://localhost:8000/ready | jq '.status'
# Expected: "ready" (non-503)

curl -s http://localhost:8000/health/deep | jq '.checks | keys'
# Expected: includes "database", "nodus", "syscall_registry"

curl -s http://localhost:8000/health/deep | jq '.checks.syscall_registry'
# Expected: {"status": "ok", "count": >= 22, "minimum_expected": 22}
# (SYSCALL_REGISTRY_MIN_COUNT — the floor; grows as syscalls are added.)
```

### 7. Version Endpoint

```bash
curl -s http://localhost:8000/api/version | jq '{
  version: .data.version,
  boot_mode: .data.system.runtime.boot_mode,
  breaking_change_policy: .data.compatibility.breaking_change_policy,
  min_client_version: .data.compatibility.min_client_version
}'
# Expected: all fields non-empty; boot_mode is "runtime-only" in platform-only profile
```

### 8. Syscall Registry Count

```bash
curl -s http://localhost:8000/health/deep | jq '.checks.syscall_registry.count'
# Expected: integer >= 22 (SYSCALL_REGISTRY_MIN_COUNT)
```

### 9. Readiness Gate

```bash
curl -o /dev/null -w "%{http_code}" http://localhost:8000/ready
# Expected: 200 after startup completes
# 503 during startup (restore_pending or startup_incomplete) is correct and expected
```

### 10. Watcher Endpoint (SDK Compatibility)

```bash
curl -s -X POST http://localhost:8000/watcher/signals \
  -H "X-API-Key: $AINDY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"signal_type": "activity.detected", "user_id": "test-uid", "metadata": {}}' \
  | jq '.status'
# Expected: "ok" or validation error (not 404)
```

### 11. Platform SPA

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/platform/
# Expected: 200

curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/platform/assets/does-not-exist.js
# Expected: 404 (not 200 HTML fallback — asset 404 discrimination is load-bearing)
```

### 12. Docker Compose Stack

```bash
# Default profile: api + postgres only (redis/mongo require --profile full/social)
docker compose up -d
docker compose ps
# Expected: api and postgres "Up" (redis/mongo absent is correct for default profile)

docker compose exec api aindy-runtime --version
# Expected: version string

docker compose logs api 2>&1 | grep -E "startup complete|boot_mode"
# Expected: startup complete message present
```

---

## Cross-Repo Compatibility Checks

Run before any release that touches stable surfaces (see `CROSS_REPO_COMPATIBILITY.md`).

### 13. SDK Contract Assertions

```bash
pytest tests/unit/test_cross_repo_compatibility.py -v -k sdk
# Verifies: /api/version envelope shape, watcher endpoint path, stable syscall names
```

### 14. UI Contract Assertions

```bash
pytest tests/unit/test_cross_repo_compatibility.py -v -k ui
# Verifies: ROUTES constants match served endpoints, boot_mode field presence
```

### 15. Compatibility Metadata

```bash
pytest tests/unit/test_runtime_compatibility_metadata.py -v
# Verifies: recommended_runtime_requirement, compatible_runtime_major
```

---

## Sandbox Escape Gate

### 16. Sandbox Escape Suite

**Automated (Linux):** `.github/workflows/sandbox-escape-linux.yml` runs the full
suite on every `v*` tag (and `workflow_dispatch`) on `ubuntu-latest`, where Docker
uses a native Linux-containers backend so no tests skip. **Confirm this job is
green on the release tag** — it is the primary gate. It uploads
`sandbox_escape_results.json` as the `linux-sandbox-escape-results` artifact.

**macOS backend certification:** `.github/workflows/macos-sandbox.yml`
(`workflow_dispatch`, macOS runner) — run when sandbox controls change to
re-certify the macOS Docker backend (C3 Phase 4). Not a per-release gate.

**Manual / local fallback** (Docker with Linux containers mode):

```bash
pytest -m sandbox_escape -v
# Expected: all non-skipped tests PASS (17 total, 0 FAIL)
# Skips are acceptable for Linux-kernel-only tests on non-Linux Docker backends.
# FAIL on any test = release is blocked until root cause is resolved and re-run passes.
```

Inspect the posture summary programmatically:

```python
from AINDY.platform_layer.sandbox_runner import sandbox_escape_test_posture
posture = sandbox_escape_test_posture()
assert posture["posture"] == "all_pass", posture["operator_note"]
print(f"Covered vectors: {posture['coverage']}")
print(f"Last run: {posture['last_run']} on {posture['host_platform']}")
```

**Gate condition:** `posture["posture"] == "all_pass"` — no FAIL results in any category.
Skipped tests (Linux-only controls on non-Linux backends) do not block the gate.

**Audit trail:** Append a new entry to `docs/runtime/SANDBOX_ESCAPE_AUDIT.md` after each
pre-release run. Include platform, Docker version, image digest, commit, and the summary line
from the test output. The artifact `tests/sandbox/sandbox_escape_results.json` is the
machine-readable record.

---

## nginx Proxy Profile

### 17. Proxy Profile Smoke Test

Verify that the nginx `proxy` profile starts and can reach the API. Requires
Docker with Linux containers mode.

```bash
docker compose --profile proxy up -d
docker compose ps nginx
# Expected: nginx container "Up", ports 0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp

# Plain HTTP through nginx
curl -s -o /dev/null -w "%{http_code}" http://localhost/ready
# Expected: 200 (nginx proxies to api:8000/ready)

curl -s -o /dev/null -w "%{http_code}" http://localhost/platform/
# Expected: 200

docker compose down
```

Note: HTTPS (nginx.tls.conf) is not gate-checked in CI — it requires valid certs
that cannot be generated in a clean-room environment. Verify manually before any
deployment that introduces TLS termination.

---

## Release Notes Verification

- [ ] **Fold in the changelog fragments first:** `python scripts/assemble_changelog.py`,
  then `--check` to confirm none are stranded. Entries live in `changelog.d/` as one file
  per PR (they cannot conflict); assembly is a release step, never a per-PR CI gate —
  fragments are *supposed* to exist during development.
- [ ] `## Unreleased` is **complete**, then promoted to the version heading.

  Entries should already be there: per the CHANGELOG protocol in `CLAUDE.md`, each PR writes
  its own entry when it lands. This step **verifies** that; it is not the place to author them.
  If `Unreleased` looks thin, reconcile it against `git log vX.Y.Z..main --no-merges` **before
  cutting** — measured 2026-08-15, only 1 of the 50 commits since `v2.0.1` had touched the
  file, and every prior release window shows the same shape.
- [ ] **On promotion, delete any sentence that describes what has *not yet* been done.**

  Status notes are correct under `## Unreleased` and **false the moment it becomes a version
  heading**, because the promotion PR is the one that resolves them. This is not hypothetical:
  the `2.1.0` entry shipped saying *"The Dockerfile builder-stage pin is still `2.0.1`"* — in
  the very commit that bumped it to `2.1.0`. Written in #425 where it was true; promoted
  verbatim in #434 where it was not.

  Sweep for "still", "not yet", "pending", "must be bumped before". **Write what a change *is*,
  not what remains outstanding** — the first survives promotion, the second cannot.
- [ ] Breaking changes to stable surfaces are explicitly documented
- [ ] Schema contract version bump is noted if any model changed
- [ ] **Read the `Upgrade Path Guard` result, including its `negative-control` job.**

  The guard installs the previous released wheel, builds its schema, then runs this build's
  `bootstrap-schema` over that database — the state no other job reaches. **On a release with
  no runtime schema change it passes trivially**, because there is no drift to detect; a broken
  guard and a clean release are indistinguishable there. `negative-control` injects synthetic
  drift and requires exit 3, so it is the half that carries meaning on such a release. **Say
  which case applied in the handoff** — "the guard was green" means different things in each.
- [ ] **★ If runtime-owned schema changed, the app handoff SAYS SO and names the step.**

  `FR-14`: an additive runtime column makes a bare `bootstrap-schema` exit non-zero, which
  under `set -e` + `restart: unless-stopped` is a **crash loop** — it took a live stack down
  on 2.1.0. The handoff for that release said *"nothing to backfill and no data to prepare"*,
  which was true **about data** and read to a deployer as "nothing to do".

  So when `AINDY/db/models/` or `memory_persistence.py` changed, the handoff must state:
  *existing deployments must run `bootstrap-schema --reconcile`* (or branch on **exit code 3**).
  Check with `git diff vX.Y.Z..HEAD -- AINDY/db/models/ AINDY/memory/memory_persistence.py` —
  a non-empty diff makes this mandatory, not a judgement call.

  **This is the FR-8 shape twice over: the upgrade path is not exercised against an existing
  database before release.** CI builds a fresh one, where `create_all` produces the new columns
  and there is nothing to reconcile — so no green check can see this class of failure.
- [ ] **★ If any route started enforcing a scope, the app handoff NAMES the scopes.**

  `HTTP-SCOPE-GAP-1`. The app team's one stated condition on scope rollout: *name the scopes in
  the handoff for the release that enforces them*, because the alternative is scattered 403s
  that read to them as a frontend bug and get debugged in the wrong repo.

  Check with `git diff vX.Y.Z..HEAD -- AINDY/routes/ | grep enforce_api_key_scope` — a non-empty
  result makes this mandatory, not a judgement call. State the **route → scope** table and,
  separately, whether an ordinary JWT session already holds them: sessions derive scopes from
  the user row, so the exposed callers are almost always **platform API keys**, and *"no session
  is affected"* is not the same claim as *"no caller is affected"*.

  A new gate is at its least proven when it is newest: `test_every_enforced_scope_is_held_by_an_ordinary_session`
  fails in CI if a gate an ordinary user cannot satisfy is added, but it says nothing about the
  keys an operator issued last year.
- [ ] **★ If a first-party ecosystem pin moved, all THREE sites moved with it.**

  `nodus-lang` and `nodus-mcp` are declared in `pyproject.toml`, `AINDY/requirements.txt`, **and**
  the `Install MCP extra` step in `runtime-ci.yml` — which installs those packages directly
  rather than through the extra, so a constraint fixed in only the first two is silently
  re-resolved by the third.

  Two failures came from exactly this: #451 bumped `nodus-lang` in `pyproject.toml` only, so CI
  tested 4.1.0 for four months while the wheel required 4.2.0; and #468's bump to 5.0.0 was
  resolved back down by `nodus-mcp`'s `<5.0.0` cap installed in the third site.

  `tests/unit/test_dependency_pin_agreement.py` covers the first two sites and reports which
  installed package forbids a pin. Check the workflow step by hand.

- [ ] **Adopting a new `nodus-lang` major requires a compatible `nodus-mcp` first.**

  Sequence, not a deadlock: nodus publishes → `nodus-mcp` releases accepting the new major →
  the runtime bumps **both** in one PR. Pinning a nodus major that `nodus-mcp` caps below makes
  `pip install aindy-runtime[mcp]` **uninstallable** (`ResolutionImpossible`), so a green CI run
  achieved by isolating the MCP tests would be shipping a broken extra. See `MCP-SDK-2X-1`.

- [ ] **★ Every dependency pin that moved this cycle had its release notes read.**

  Not "does it resolve" and not "do the tests pass" — **what does the release contain.** List the
  pins that moved (`git diff vX.Y.Z..HEAD -- pyproject.toml AINDY/requirements.txt`) and confirm
  the intervening notes were read for each. For a multi-version jump that means *every* release
  in the span, not just the target: the fix is often in the middle one.

  **The failure this exists to prevent, measured 2026-08-19:** `NODUS-UPGRADE-2` was filed
  *"P3, routine"* on the sole basis that the pin was three patch releases behind. `nodus-lang`
  5.0.3 was a **cross-runtime guest-memory disclosure fix** — `GLOBAL_MEMORY_STORE` bound at
  import, so every `NodusRuntime` in a process shared one guest memory dict, reachable from any
  `.nd` script via `memory_put`/`memory_get`. It was found only because someone read the
  changelog before bumping. **A severity assigned from version distance is not an assessment.**

  **★ Why the rest of this section could not have caught it.** Everything above is about *how*
  to bump safely — three sites, resolver traps, `nodus-mcp` sequencing, confinement re-verified
  against the real VM. All of it ran, all of it passed, and all of it would have passed just as
  well on a bump that silently carried a security fix nobody knew about. **A protocol that only
  covers mechanics cannot produce a severity**, and severity is what decides whether a bump is a
  routine PR or something an operator must be told about.

  Two consequences when the notes say a release fixed something with a security consequence:

  - The changelog fragment gets the `00-` operator-read prefix, and says plainly whether the
    runtime was exposed and under what conditions. `00-488-nodus-lang-5.0.4.md` is the worked
    example.
  - **Check whether any of our own claims depended on the broken behaviour.** For that one,
    `nodus_worker_pool.py`'s docstring asserted a reused process *"never leaks state between
    runs"* — false on the affected pins, because `run_one` rebuilds the state the runtime owns
    and cannot reset a module global inside a dependency. **Upstream bugs invalidate downstream
    docstrings, and nothing greps for that.**

  Reproduce before believing either way. Upstream notes are a lead, not evidence: 5.0.1 printed
  `password123` and 5.0.4 printed `nil` on our own import path, which is what made the severity
  a fact rather than a reading. Pin it with a test that fails on the old version — the guard for
  this one is mutation-tested at 2/11.

- [ ] Compatibility window stated for `aindy-sdk` and `aindy-ui-kit`
- [ ] TECH_DEBT.md updated for any newly closed or opened entries
- [ ] `sandbox_escape_test_posture()["posture"] == "all_pass"` for this release platform
- [ ] **After the tag publishes: expect ONE spurious `Boot Smoke` failure on the next push to
  `main`, and re-run it.** The job's own *"is this version published?"* step reads PyPI's **JSON
  API**, while `pip` resolves against the **simple index** — different endpoints with different
  CDN propagation. For a couple of minutes after publish the check says "published, proceed" and
  pip says `No matching distribution found`. Observed on `v2.2.0` (published 08:31, failed 08:33,
  passed on re-run). It is a **required** check, so this red-lines unrelated PRs until re-run.
  Confirm propagation with `curl -s https://pypi.org/simple/aindy-runtime/ | grep <version>`
  before assuming it is anything else.
- [ ] Dockerfile builder stage version pin bumped to match new release (`pip install "aindy-runtime==X.Y.Z"` in Stage 1)
