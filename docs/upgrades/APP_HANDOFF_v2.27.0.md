---
title: "App Handoff — Runtime v2.27.0"
api_version: "1.0"
last_verified: "2026-10-09"
status: current
owner: "platform-team"
---

# App handoff: runtime v2.27.0

Pin bump `constraints.txt` `==2.26.0` → `==2.27.0` (your contract test moves the floor with it).
No ui-kit release: `@aindy/ui-kit` 2.1.1 stays current.

**What you need to do:** bump the pin and rebuild. There is no migration. Then take your four
`pip-audit` ignores off (§1), and re-measure the latency that FR-52 found (§2).

**What this release is.** It answers both of your open runtime FRs. **FR-51:** python-jose is gone
(replaced by PyJWT) and pymongo is 4.18.2. **FR-52:** the per-request version check now runs once.
The rest is dependency bumps.

> ## ★ Confirm what you are actually running: in the container, printing the path.
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v; print(v.__version__, list(AINDY.__path__))"
> # expect: 2.27.0 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 0. Schema: none

No change under `AINDY/db/models/`. Alembic head stays **`0020`**, schema contract **`2026-09-20`**.
`bootstrap-schema` exits 0 on a current stack. The `Upgrade Path Guard` passes **trivially** on a
release like this, because there is no drift to find. Its negative control is the half that carries
meaning, and it was green.

## 1. FR-51: python-jose → PyJWT, pymongo 4.18.2 (#810, #814)

- **python-jose 3.5.0 is no longer installed.** CVE-2026-85394 (critical) has no fixed release, so
  the library was replaced by **`PyJWT[crypto]==2.15.1`**. **`ecdsa`, `rsa` and `pyasn1` leave the
  install with it.** If anything of yours imports one of those four without declaring it, it breaks
  on this pin. We checked your source on 2026-10-09 and found none.
- **Tokens are unchanged on the wire.** Header, claim order and signature are byte-identical.
  Sessions, verification links and reset links minted on 2.26.0 verify on 2.27.0. A jose-minted
  fixture of each is pinned in the runtime's tests, so nobody is logged out by the upgrade.
- **`pymongo==4.18.2`** — CVE-2026-96747 / 96748 / 96749, exactly the three you listed.
- **PyJWT carries its `crypto` extra.** Its HMAC guard against asymmetric key material returns early
  without `cryptography` (nodus-auth found this, def3ef8). The runtime had `cryptography` through a
  separate pin anyway; now the guard does not depend on that.
- **The runtime's `security-audit.yml` carries no exemptions now.** The ecdsa acceptance
  (CVE-2024-23342) went with jose.

**Ask:** remove your four FR-51 ignores (`GHSA-qx36-8mw2-4r3x`, `GHSA-v4x9-3549-crwv`,
`GHSA-vp6j-j7w5-5xjj`, `GHSA-3qf3-8w2g-rqmx`). Their stated removal condition is this adoption.

## 2. FR-52: the per-request ~3 s is gone (#814)

DEBT-COMPAT-1's consumer check ended every `load_plugins()` call, and the registry's getters call
that lazily: 26 times in one warm `GET /memory/nodes`. Each call read every installed
distribution's metadata. **It now runs once per plugin module set.** A different manifest or profile
is still checked, and `/api/version` serves the same `compatibility.consumers` records.

**Why our suites never saw it:** runtime-only boot loads no plugins, so `load_plugins()` returned
before reaching the check. The regression test now boots a plugin manifest and counts metadata scans
across one warm request through the real route: 26 before, 0 after. Your measurement is the one
that matters.

**Ask:** re-run your FR-52 measurement on 2.27.0. Use `GET /apps/scores/me` warm, the way you took
it (p50 3,780 ms as shipped vs 820 ms stubbed). Your soak row 2 latency check should pass again. The
`idle in transaction` you saw during live requests (2–5, each about one FR-52-length request) should
shrink with it.

## 3. Dependency bumps (#811, #813)

Runtime pins: `starlette` 1.7.0, `pyphen` 0.18.1, `regex` 2026.9.29, `mako` 1.4.3,
`charset-normalizer` 3.5.2. Platform UI: `@aindy/ui-kit` 2.1.1 (the version you already run),
`vite` 8.3.2, `source-map-js` 1.2.2. Native crate (build-time only): `pyo3` 0.29.3, `uuid` 1.27.0.
Nothing in the runtime imports `pyphen`. If you use it, its minor version moved.

**Release notes read, for each pin that moved. Three have a consequence worth naming:**

- **`regex`**: the 2026.7.19 → 2026.9.29 span fixes memory-safety bugs. These are a heap
  out-of-bounds write at pattern compile time (issue 611), a `count_one()` underflow (612) and a
  `Match.expand()` segfault (619). Nothing in the runtime or your source imports `regex`; it is a
  declared pin with no importer. It is not reachable from here, but take the bump if you add one.
- **`mako` 1.4.2**: closes a directory-traversal bypass in `TemplateLookup` (`C:/../../x`). It
  applies on **Windows** only. Mako runs only as Alembic's script template, so this is not reachable.
- **`starlette` 1.7.0**: background tasks now run only after the response is sent, **when
  `BaseHTTPMiddleware` is in the stack.** Neither repo uses `BaseHTTPMiddleware` (the runtime
  avoids it on purpose: `spa_fallback.py`). Your `BackgroundTasks` in `task_router.py` keep their
  current timing. Also new: invalid multipart input answers 400, and `anyio>=4` is now required
  (already met).

## 4. For the record: what your 2.25.0 readout unlocked

Your readout on 2026-10-09 met DEC-083's condition. `aindy_memory_recall_failures_total` read zero for
seven days, across 405 requests through the flagged path, with no pool exhaustion. **That clears
`AINDY_MEMORY_RECALL_OWN_SESSION` to default ON.** That flip is **not** in this release. It will
ship in its own release, with its own handoff, followed later by pgvector 0.5.0 (MEM-EXPAND-DEAD-1).
You already run the flag on, so it will change nothing for you.

## Asks, in one place

1. Pin `==2.27.0`, rebuild, and confirm the version and path with the command at the top.
2. Remove the four FR-51 `pip-audit` ignores (§1).
3. Re-run the FR-52 latency measurement (§2) and report it. That closes FR-52 on your side.
4. **Still owed from 2.26.0:** after your first real agent runs, grep for
   `RESOURCE_LIMIT_EXCEEDED` and report any hit with the run's step count.
