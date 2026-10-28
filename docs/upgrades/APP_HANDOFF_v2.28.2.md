---
title: "App Handoff — Runtime v2.28.2"
api_version: "1.0"
last_verified: "2026-10-27"
status: current
owner: "platform-team"
---

# App handoff: runtime v2.28.2

Pin bump `constraints.txt` `==2.28.1` → `==2.28.2` (your contract test moves the floor with it),
then **rebuild the image**. No ui-kit release: `@aindy/ui-kit` 2.1.1 stays current.

**What this release is.** A security patch. Two advisories published 2026-10-27 hit packages that
reach you only through the runtime: **pymongo** (#862) and **dnspython** (#860). The rest is the
2026-10-26 dependency batch (#860). No runtime behaviour, route, scope or default changed.

> ## ★ Confirm what you are actually running: in the container, printing the path.
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v, pymongo, dns.version, dateutil; print(v.__version__, list(AINDY.__path__), pymongo.version, dns.version.version, dateutil.__version__)"
> # expect: 2.28.2 ['/usr/local/lib/python3.11/site-packages/AINDY'] 4.18.3 2.9.0 3.0.0
> ```

---

## 0. Schema: none

No change under `AINDY/db/models/`. Alembic head stays **`0020`**, schema contract **`2026-09-20`**.
`bootstrap-schema` exits 0 on a current stack. The Upgrade Path Guard therefore passes trivially on
this release. Its `negative-control` job is the half that carries meaning here.

## 1. The two advisories

Your `constraints.txt` pins only `aindy-runtime` (checked 2026-10-27), so both packages arrive
through us, and **your own pip-audit will flag a 2.28.1 image for both.** Adopting 2.28.2 and
rebuilding clears them. Do not add ignores for them.

| Advisory | Package | Fixed in | Severity | Runtime exposure |
|---|---|---|---|---|
| CVE-2026-106435 / GHSA-98qm-wvgf-w4cf | `pymongo` 4.18.2 | 4.18.3 | high, availability | **Nil in practice.** The C-extension BSON decoder reads out of bounds on a malformed Regex element in an `mmap` / array buffer. Nothing in the runtime calls `bson.decode*`, and the driver's own transport is not affected. |
| CVE-2026-114605 / GHSA-45c3-73f7-pv4m | `dnspython` 2.8.0 | 2.9.0 | high, availability | **Conditional.** A crafted DNS message burns CPU in name decompression. The runtime reaches `dns` only through pymongo resolving a `mongodb+srv://` `MONGO_URL`. You are exposed if your `MONGO_URL` is an SRV URL **and** an attacker can answer that lookup. |

**How it surfaced:** pip-audit on runtime PR #861 (docs-only) went red at 2026-10-27 ~16:00Z, minutes
after the pymongo advisory reached OSV. The dnspython fix was already on `main` (#860 had merged it
as a routine bump the same morning), so our audit only ever showed pymongo. The dnspython half was
found by reading its release notes for this release, not by a check.

## 2. `python-dateutil` 3.0.0: read if your code uses `dateutil.tz`

The runtime does not import `dateutil`; it arrives through `pendulum`, which uses only
`dateutil.parser` as a fallback. 3.0's breaking changes are all in `dateutil.tz`:

- `tz.tzfile` rewritten from `zoneinfo`. **Times inside a DST gap now follow PEP 495**: e.g.
  `datetime(2003, 4, 6, 2, 30, tzinfo=tz.gettz("America/New_York"))` is now -05:00, was -04:00.
- `dateutil.tz.TZPATHS` removed (use `TZPATH` / `reset_tzpath`).
- `gettz` with a relative key that leaves every `TZPATH` directory now **raises** by default.
- `dateutil.zoneinfo` deprecated; no bundled tz database, so data comes from the system or `tzdata`.
- On Windows, `gettz("UTC")` returns a `tzfile`, not a `tzwin`.

If app code calls `dateutil.tz` directly, check it against these before rebuilding.

## 3. Dependency batch of 2026-10-26 (#860), the rest

| Package | From → to | Where |
|---|---|---|
| `alembic` | 1.20.0 → 1.20.1 | runtime (autogenerate fixes only) |
| `ruff` | 0.16.9 → 0.17.2 | lint only |
| `vite` / `postcss` | 8.3.4 → 8.3.5 / 8.5.30 → 8.5.31 | platform SPA, dev (shipped prebuilt in the wheel) |
| `cc` / `uuid` | 1.8.0 → 1.10.0 / 1.28.0 → 1.29.0 | native crate, build-time only |
| `actions/setup-python`, `pypa/gh-action-pypi-publish` | 7.0.0 → 7.1.0, 1.14.2 → 1.14.3 | CI only. `publish.yml` job names and the `production` gate are unchanged, so your release watcher is unaffected. |

**Not taken:** `openai` 3.x. It moved to `httpx2`; deferred under `DEP-UPGRADE-DEFERRED-1`. The
runtime stays on openai 2.46.0.
