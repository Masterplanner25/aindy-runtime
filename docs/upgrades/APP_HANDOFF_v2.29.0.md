---
title: "App Handoff — Runtime v2.29.0"
api_version: "1.0"
last_verified: "2026-10-29"
status: current
owner: "platform-team"
---

# App handoff: runtime v2.29.0

Pin bump `constraints.txt` `==2.28.2` → `==2.29.0` (your contract test moves the floor with it),
then **rebuild the image**. No ui-kit release: `@aindy/ui-kit` 2.1.1 stays current.

**What this release is.** One behaviour change. The dispatcher now **refuses** a payload `user_id`
that names anyone but the caller (#866, DEC-103), where until now it only counted it. The rest is
`setuptools` 84.1.0 (#865), two low, Windows-only advisories with no runtime exposure.

> ## ★ Before the rebuild: read the counter on 2.28.2. After it: confirm what you are running.
>
> ```bash
> # on the CURRENT (2.28.2) container, before recreating it — this closes window 4
> curl -s http://<api>/metrics/ | grep aindy_payload_identity_mismatch_total
> # expect: the HELP/TYPE lines and no samples. Any {syscall="…"} sample is a call that 2.29.0 REFUSES.
>
> # on the NEW container
> docker exec <api-container> python -c "import AINDY, AINDY._version as v, setuptools; print(v.__version__, list(AINDY.__path__), setuptools.__version__)"
> # expect: 2.29.0 ['/usr/local/lib/python3.11/site-packages/AINDY'] and setuptools >= 84.1.0
> # ONLY if you install the `security` extra; see the correction in §2.
> ```

---

## 0. Schema: none

No change under `AINDY/db/models/`. Alembic head stays **`0020`**, schema contract **`2026-09-20`**.
`bootstrap-schema` exits 0 on a current stack. The Upgrade Path Guard therefore passes trivially on
this release. Its `negative-control` job is the half that carries meaning here.

## 1. The dispatcher refuses a payload `user_id` that is not the caller's (#866, DEC-103)

| Payload `user_id` on an in-process `dispatch()` | 2.28.x | 2.29.0 (default) | `AINDY_PAYLOAD_IDENTITY_DISPATCH=observe` |
|---|---|---|---|
| absent / `None` / `""` | runs | runs | runs |
| equals `ctx.user_id` (any UUID spelling) | runs | runs | runs |
| names another user | runs, counted | **`TENANT_VIOLATION`, `failure_class: permission`; handler not run; counted** | runs, counted |

- The check runs right after "the caller has a user id", before the quota check and the
  idempotency gate, so a refused call never reaches the effect ledger or the handler.
- **The transports did not change.** MCP, the Nodus `sys()` seam and `POST /platform/syscall`
  already refused this (DEC-099, 2.28.0). What's new is that the dispatcher refuses it for every
  other caller: your flows, services, scheduler jobs and bridges.
- **The counter stays.** `aindy_payload_identity_mismatch_total{syscall}` keeps its name and
  labels and counts in both modes. On 2.29.0 a non-zero label is a **refused** call. Report it
  to us with the label.
- **The escape hatch** is `AINDY_PAYLOAD_IDENTITY_DISPATCH=observe`, for an operator whose code
  hits a path nobody exercised. Only `observe` (case-insensitive) disables refusal; a typo, `off`
  or `0` all refuse. Treat it as a rollout aid: read the counter, fix the caller, remove it.
- **An on-behalf call has no supported form yet.** If a job must act for another user, give it a
  per-user context (as your all-user scheduler loops already do), never a payload key or the
  flag. The designed form is `INITIATOR-IDENTITY-1`'s `subject`.

**The evidence for flipping**, from your readouts: window 1 read 0 / 1,496 dispatches, window 2
was lost, window 3 read 0 / 793. You checked all 93 in-process dispatch sites. The one that would
be refused, `apps/tasks/flows/tasks_flows.py:218` `watcher_signals_list_node`, is fixed in
A.I.N.D.Y. #506; #507 removes three payload `user_id` reads in `automation.list_*`. **Both ship in
this rebuild.** If either fix is not in the image you build, set the `observe` flag for that
rollout.

**Refused on purpose:** a flow whose whole state is its payload (`tasks_flows.py:7`,
`system_flows.py:20` `_syscall_node`), started through `/platform` with another user's id in its
state. That is the case the change exists to stop.

## 2. `setuptools` 84.0.0 → 84.1.0 (#865)

| Advisory | Severity | Runtime exposure |
|---|---|---|
| GHSA-xmj3-9gf7-h52w | low | **None.** On Windows, a crafted wheel's `namespace_packages.txt` could place an `__init__.py` outside the egg directory through the legacy `setup_requires` install path. The runtime never takes it, and the image is Linux. |
| GHSA-grgh-hr87-3jpw | (no global record yet; from the 84.1.0 release notes) | **None.** On Windows, `setuptools.archive_util` could extract a member outside the target directory. The runtime never calls it. |

> **Correction (2026-10-29, after publication; reported by the app).** As first written, this
> section said setuptools "arrives through us". It does not. `aindy-runtime` declares
> `setuptools>=84.1.0` only under its **`security` extra**. `AINDY/requirements.txt`'s exact
> `==84.1.0` is CI's lock file, not something a wheel install reads. A plain `pip install
> aindy-runtime` (or `aindy-runtime[mcp]`) keeps whatever setuptools the base image ships. The
> app's 2.28.2 container showed **79.0.1**, which also predates the CVE-2026-59890 floor (83.0.0).
> The runtime's own `Dockerfile` installs plain `aindy-runtime==2.29.0`, so it is affected too.
>
> To get the fixed setuptools, install **`aindy-runtime[security]`** (the app now installs
> `[mcp,security]`). It is a lower bound, so you get the newest release (84.2.1 on 2026-10-29),
> not exactly 84.1.0. The in-container check above expects `>= 84.1.0`. Exposure is unchanged
> either way: none on Linux. This is about what a scanner of your image reports.

Do not add ignores for either; take the `security` extra instead.

`publish.yml` job names and the `production` gate are unchanged, so your release watcher is
unaffected.

## 3. Asks

1. Read `aindy_payload_identity_mismatch_total` on the 2.28.2 container before recreating it
   (closes window 4).
2. Pin bump `==2.29.0`, with #506 and #507 in the same image (or set the `observe` flag).
3. Rebuild, run the in-container check above, and read the counter again after a day of normal
   traffic. Report any label.
