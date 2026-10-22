---
title: "App Handoff — Runtime v2.28.1"
api_version: "1.0"
last_verified: "2026-10-22"
status: current
owner: "platform-team"
---

# App handoff: runtime v2.28.1

Pin bump `constraints.txt` `==2.28.0` → `==2.28.1` (your contract test moves the floor with it),
then **rebuild the image**. No ui-kit release: `@aindy/ui-kit` 2.1.1 stays current.

**What this release is.** A security patch that answers **FR-54** (filed 2026-10-22):
`certifi==2026.7.22` carried **PYSEC-2026-4320 / GHSA-6q48-6c7w-9gw2**. The rest is the
2026-10-19 dependency batch (#845). No behaviour, route, scope or default changed.

> ## ★ Confirm what you are actually running: in the container, printing the path.
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v, certifi; print(v.__version__, list(AINDY.__path__), certifi.__version__)"
> # expect: 2.28.1 ['/usr/local/lib/python3.11/site-packages/AINDY'] 2026.10.19 (or newer)
> ```

---

## 0. Schema: none

No change under `AINDY/db/models/`. Alembic head stays **`0020`**, schema contract **`2026-09-20`**.
`bootstrap-schema` exits 0 on a current stack.

## 1. FR-54: `certifi >= 2026.10.19`, a lower bound on purpose (#846, DEC-102)

certifi before 2026.10.18 bundles "emSign ECC Root CA - G3". Eight eMudhra intermediates that chain
to it have compromised, revoked keys, so whoever holds one can issue a certificate for any hostname
that requests/httpx would trust. 2026.10.18 dropped every emSign root and 2026.10.19 restored the
unaffected ones. OSV records `fixed 2026.10.19`.

**What you need to do:**
- **Rebuild the image.** A container keeps the bundle it was built with, and the advisory asks for
  a rebuild of every image that vendors certifi.
- **Remove your pip-audit ignore for PYSEC-2026-4320.** Its removal condition (an adoption that
  brings certifi ≥ 2026.10.19) is met by this release.
- **Nothing in `constraints.txt` besides the runtime pin.** You do not pin certifi yourselves
  (checked 2026-10-22); it arrives through us.

**Why a range, not `==2026.10.19`:** a CA bundle is the one dependency whose newest release is the
safest by construction. With an exact pin you could not take a fixed bundle until we released. Now
the next certifi release installs without a runtime release. If you later add a certifi
constraint of your own, keep it a lower bound too, or it re-creates the problem from your side.

**How we missed it:** our pip-audit last ran at 2026-10-20 03:32Z, before the advisory reached OSV,
and Dependabot raised no alert. Your report was the first signal. #846's own pip-audit run
installed certifi 2026.10.19 and passed.

## 2. Dependency batch of 2026-10-19 (#845)

| Package | From → to | Where |
|---|---|---|
| `pydantic` | 2.14.0 → 2.14.1 | runtime |
| `alembic` | 1.19.2 → 1.20.0 | runtime (the Upgrade Path Guard exercises it) |
| `pendulum` | 3.2.0 → 3.3.0 | runtime |
| `MarkupSafe` | 3.0.3 → 3.0.4 | runtime |
| `tzdata` | 2026.3 → 2026.5 | runtime, data only |
| `react` / `react-dom` | 19.3.0 → 19.3.1 | platform SPA (shipped prebuilt in the wheel) |
| `postcss` | 8.5.29 → 8.5.30 | platform SPA, dev |
| `pyo3` / `cc` | 0.29.3 → 0.30.1 / 1.7.0 → 1.8.0 | native crate, build-time only |

None of these is a security fix. react-router is unchanged (7.x, DEP-UPGRADE-DEFERRED-1). If your
own pins name any of the runtime packages above, move them with this release.
