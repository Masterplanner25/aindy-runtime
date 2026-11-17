---
title: "App Handoff — Runtime v2.36.1"
api_version: "1.0"
last_verified: "2026-11-16"
status: current
owner: "platform-team"
---

# App handoff: runtime v2.36.1

**A security patch that answers FR-69.** `dnspython` moves 2.9.0 → 2.9.2. The rest is the 2026-11-16 dependency
batch (#975). No runtime behaviour, route, scope or default changed. **No schema step**: Alembic stays at `0022` and
the schema contract at `2026-11-09`. No ui-kit or aindy-sdk release.

Bump the `constraints.txt` pin to `==2.36.1` (your contract test moves the floor with it), **rebuild the image**
(`mcp` shares it, so recreate both), and **remove your audit ignore for GHSA-vvf4-9x63-8xvv**.

> ## ★ After the rebuild: confirm what you are running
>
> ```bash
> docker exec <api-container> python -c "import AINDY, AINDY._version as v, importlib.metadata as m; print(v.__version__, m.version('dnspython'), m.version('python-dateutil'), list(AINDY.__path__))"
> # expect: 2.36.1 2.9.2 4.0.0 ['/usr/local/lib/python3.11/site-packages/AINDY']
> ```

---

## 1. ★ FR-69: `dnspython` 2.9.2 (#975)

| Advisory | Package | Fixed in | Severity | Runtime exposure |
|---|---|---|---|---|
| CVE-2026-128089 / GHSA-vvf4-9x63-8xvv | `dnspython` 2.9.0 | 2.9.2 | high, availability | **Nil in practice.** `from_text()` did not bound a WKS record's service number, so crafted zone-file text allocates without limit. Nothing in `AINDY/` imports `dns`; pymongo uses only the resolver (wire format, for `mongodb+srv`). Your assessment in FR-69 matches ours. |

**Still an exact pin, not the range you asked for.** The owner chose `==2.9.2`: DEC-102's range exception is for
certifi alone, because a CA bundle's newest release is the safest one by construction. dnspython has an API, so that
argument does not carry over. The cost you named stays: a future dnspython advisory needs another runtime patch. We
will cut one the same way.

**FR-69 closes** when you are on 2.36.1 and your audit runs clean with the ignore removed. Report both in your
upgrade note.

## 2. `python-dateutil` 4.0.0: read if your code calls `isoparse`

The runtime does not import `dateutil`; it arrives through `pendulum`. 4.0 requires Python ≥ 3.11 (the image is
3.11) and drops its `six` dependency. Its one behavioural change: **`parser.isoparse` (and `parse_isodate`,
`parse_isotime`, `parse_tzstr`) now require each numeric field to be exactly the expected number of ASCII digits.**
Strings that used to parse, such as `20200728121240`, `2014- 4-21`, `2_14-04-21`, or an offset like `+-1:00`, now
raise `ValueError`. `tzical` applies the same check to `TZOFFSETFROM`/`TZOFFSETTO`. Also fixed: `relativedelta(yearday=366)`
now lands on Dec 31 in leap years, and a cached `rrule` iterated twice no longer hangs.

If app code calls `isoparse` on input it does not control, check what it does with a `ValueError`.

## 3. The rest of the batch (#975)

| Package | From → to | Where |
|---|---|---|
| `idna` | 3.19 → 3.20 | runtime (Unicode 18; domain-length check in the incremental codec) |
| `regex` / `tzlocal` | 2026.9.29 → 2026.11.9 / 5.4.4 → 5.4.5 | runtime |
| `vite` / `@vitejs/plugin-react` / `postcss` | 8.3.6 → 8.3.8 / 6.2.1 → 6.3.0 / 8.5.32 → 8.5.33 | platform SPA, dev (shipped prebuilt in the wheel) |
| `cc` / `uuid` | 1.11.1 → 1.11.2 / 1.29.1 → 1.29.2 | native crate, build-time only |

**Not taken: SQLAlchemy 2.1.** It resolves a bare `postgresql://` URL to psycopg 3, and the runtime ships `psycopg2`,
so engine creation fails on PostgreSQL. The runtime stays on 2.0.52; the migration is deferred under
`DEP-UPGRADE-DEFERRED-1`. If you pin SQLAlchemy yourself, stay on 2.0.x.

## 4. Your register

- **FR-67 and FR-68: closed** on your 2.36.0 report (runtime #974). Thanks for the counts. The 1,904 ownerless nodes
  the backfill left alone are by design (DEC-119): a node with no owner has no tenant to put in its path, so it never gets a stored one.
- **FR-70** (a narrow credential for app routes): received and verified in source. It needs a decision between your
  two options, so it is not in this patch. It gets its own release.
- Still open from earlier handoffs, no new ask: the trace question in your 2.34.0 report (§5 there).
