---
title: "Nodus 5.0.1 — Runtime Adoption Handoff"
api_version: "1.0"
last_verified: "2026-08-19"
status: current
owner: "platform-team"
---

# Nodus 5.0.1 — adoption handoff

Written from `aindy-runtime` for whoever is working in `Nodus` / `nodus-mcp`.

> ## ⚠️ Superseded pin, 2026-08-19 — the runtime now ships `nodus-lang==5.0.4`
>
> **This document is kept as written.** It records what was asked at 5.0.1 and why, and that
> reasoning is what makes the next major cheap. The note below is the correction, not a rewrite —
> everything under it still describes the 5.0.1 handoff.
>
> **The pin moved 5.0.1 → 5.0.4** (`aindy-runtime` #488). `nodus-mcp` is unchanged at `>=0.1.3`
> and resolves against it, so `aindy-runtime[mcp]` stays installable.
>
> ### ★ 5.0.3 was a security fix, and we filed the bump as routine
>
> Worth surfacing to whoever maintains Nodus, because the runtime side of it is a lesson about
> *us*, not about Nodus.
>
> `nodus-lang <= 5.0.2` bound `GLOBAL_MEMORY_STORE` at **import**, so every `NodusRuntime`
> constructed in one process shared a single guest memory dict. `memory_put`/`memory_get` are
> guest builtins reachable from any `.nd` script, so one script could read another's values.
> 5.0.3 gives each runtime its own store, with sharing opt-in via `memory_store=` /
> `share_process_state=True`. **That fix is correct and the opt-in shape is the right one.**
>
> **Why it mattered here specifically:** `AINDY/runtime/nodus_worker_pool.py` reuses worker
> processes across requests. Its docstring claimed a reused process *"never leaks state between
> runs"* because `run_one` rebuilds per-request state — but `run_one` cannot reset a module global
> living inside a dependency. It rebuilds what the runtime owns and nothing below it. Reproduced
> on our own import path before bumping: **5.0.1 prints `password123`, 5.0.4 prints `nil`.**
> Bounded by the warm pool being opt-in and off by default, so latent rather than live.
>
> **The runtime-side failure was one of process.** Our bump protocol is entirely about *how* to
> upgrade safely — pin every install site, check the resolver, re-verify confinement against the
> real VM — and says nothing about reading the intervening release notes to learn *what a release
> contains*. So a security fix was triaged P3-routine on nothing but the version distance. Rule
> added on our side: read the changelog before assigning severity, not after.
>
> **The one ask this generates for Nodus:** if a release fixes something with a security
> consequence, saying so in the release notes' first line makes it much harder for a downstream
> consumer to mis-triage. We found it by reading; a consumer bumping on version distance alone
> would not have.
>
> §4.1, §4.3 and §4.4 still stand. §1 and §4.2 remain resolved as recorded below. `GATED_BUILTINS`
> is unchanged in 5.0.4 and the confinement sweep still uses it.
>
> 5.0.4 itself unbreaks a `nodus-sdk` subclass property collision introduced by 5.0.3
> (`self.memory_store` vs a read-only property). **We do not depend on `nodus-sdk`**, so that half
> does not reach the runtime — noted only so the version jump is not mistaken for two fixes we
> needed.

> ## ✅ Resolved 2026-08-17 — both asks shipped the same day
>
> * **§1 (required)** — `nodus-mcp 0.1.3` floated `nodus-lang<5.0.0` to `>=4.0.0`. **Floated,
>   not raised**, so a future nodus major will not stall the runtime the same way.
> * **§4.2 (suggested)** — `nodus-lang 5.0.1` exposes **`GATED_BUILTINS`** as a public mapping
>   (`{flag: GatedBuiltinGroup}` with `names`, `arity`, `capability`, `description`). The
>   confinement sweep's source-scraping is deleted in favour of it.
>
> The runtime now pins `nodus-lang==5.0.1` and `nodus-mcp>=0.1.3`. Verified on 5.0.1: 31 gated
> builtins still blocked, deny-by-default intact, `std:sys` guard and builtin-override refusal
> sound.
>
> **One thing worth noting for next time:** discovery broke on 5.0.0 *and again* on 5.0.1 before
> the API landed — 5.0.0 moved the names into the `else:` branch's tuple, 5.0.1 replaced that
> with `block_group(...)`. Both breakages were loud rather than silent, which is the only reason
> scraping was tolerable while it lasted. §4.2 is now moot; §4.1, §4.3 and §4.4 still stand.
>
> Kept as written below — the reasoning is what makes the next major cheap.

---

## 1. Required: `nodus-mcp` blocks the upgrade

`nodus-mcp 0.1.2` declares:

```
nodus-lang<5.0.0,>=4.0.0
```

That cap makes nodus 5.0.0 unadoptable by the runtime — not just awkward, **unadoptable**:

```
$ pip install --dry-run "nodus-lang==5.0.0" "nodus-mcp>=0.1.2"
ERROR: Cannot install nodus-lang==5.0.0 and nodus-mcp==0.1.2
       because these package versions have conflicting dependencies.
ERROR: ResolutionImpossible
```

The runtime pins `nodus-lang` exactly and ships an optional `[mcp]` extra requiring
`nodus-mcp>=0.1.2`. So pinning 5.0.0 would make **`pip install aindy-runtime[mcp]` fail for
users**. It is not merely a CI problem, which matters because the tempting fix — isolate the MCP
tests so the cap stops constraining the main suite — produces a green build that publishes a
broken extra.

### What to do

**Release a `nodus-mcp` that accepts `nodus-lang>=5`.** Then the runtime bumps both packages in
one PR and #468 merges as-is.

### The change worth considering while you are in there

The `<5.0.0` upper bound looks prophylactic rather than earned — there is no recorded 5.x break
in nodus-mcp. A hard upper bound on a fast-moving first-party dependency **guarantees** this
stall on every major: each nodus major becomes a two-repo release train, with the runtime frozen
in between.

Options, roughly in order of preference:

1. **Float it** — `nodus-lang>=4.0.0` — and let nodus-mcp's own tests catch a real break. A cap
   earns its place when a break is *known*.
2. **Release in lockstep** with each nodus major.
3. Keep capping, and accept that adopting a nodus major is a two-step sequence (below).

---

## 2. The ordering, for future majors

There is no deadlock here — it just looks like one when CI goes red. The sequence:

1. `nodus-lang X.0.0` publishes. Nothing downstream can move first; a version has to exist to be
   depended on.
2. **`nodus-mcp` releases accepting `nodus-lang>=X`.** Possible immediately once step 1 lands.
3. The runtime bumps **both** packages in **one PR**, across **all three** declaration sites:
   `pyproject.toml`, `AINDY/requirements.txt`, and the `Install MCP extra` step in
   `runtime-ci.yml` (which installs the packages directly rather than through the extra, so a
   constraint fixed in only the first two is silently re-resolved by the third).

Both of this week's failures were that third site: one PR bumped only `pyproject.toml` and CI
tested nodus 4.1.0 for four months while the wheel required 4.2.0; the next bumped both files
and the MCP step resolved nodus-lang straight back down.

---

## 3. Good news: 5.0.0 adoption is otherwise clean

The adoption work is **done** and sitting in a rebased draft PR. Everything below was verified
against the real VM on 5.0.0, not inferred.

### Deny-by-default landed exactly right

The runtime had already done this by hand. `GUEST-CONFINE-1` (2026-08-15) found a guest script
could reach subprocess, network and host env without touching the dispatcher, capability token,
effect ledger or egress guard — demonstrated by writing a file on the host, reading the real
`PATH`, and doing real DNS. The fix was to pass `allow_subprocess=False, allow_network=False,
allow_env=False` at the single construction site.

Your release notes describe the same finding — *"the capability chokepoint was built and unused,
with the door propped open by registering subprocess and http by default"*. Two independent
audits, same conclusion. 5.0.0 fixes it at the source; our explicit arguments are now
belt-and-braces.

**Impact on us: none.** One construction site (`nodus_worker.py:343`), already passing all three.
The app monolith constructs `NodusRuntime` nowhere.

### The gated surface is unchanged

**31 builtins still blocked — 7 subprocess / 18 network / 6 env, identical to 4.1.0.** Neither
widened nor shrank across the major. Verified by executing each one through the real worker and
requiring a sandbox refusal.

### `.nodus/` write restriction does not reach us

The only `.nodus/` the runtime ships is `AINDY/nodus/stdlib/.nodus/deps.json`, read at import by
the module resolver and never written by a guest.

---

## 4. Four things you may want to change in Nodus

These are de-facto interfaces the runtime depends on. None is a bug; each is a place where a
downstream consumer is coupled to something you may not consider public.

### 4.1 Denial message text is load-bearing downstream

5.0.0 rephrased refusals from `... allow_subprocess=False ...` to:

```
Blocked: subprocess execution is not granted; pass allow_subprocess=True to NodusRuntime to allow it
```

Four of our confinement tests went red on wording while the guest was **fully confined** — the
refusals were firing correctly with `kind: 'sandbox'` and `capability_denied` events. That cost
an hour of "is the sandbox broken?" before the answer was "no, it is a rephrase."

We have since loosened our assertions to match the **flag name** rather than the sentence, which
is the right fix on our side. Worth knowing the text is consumed, though: the structured error
carries `kind` and the flag name, so if you ever want to make the contract explicit, those two
fields are the ones to promise.

### 4.2 Gated builtin names are only discoverable by scraping source

To assert *every* gated builtin is blocked (not just the three we demonstrated), we read the
names out of `nodus.builtins.registry` source with a regex. 5.0.0 restructured it — the names
moved from the `if` branch into the `else:` branch's `for _name in (...)` tuple — so our
discovery broke, and worse, it began capturing the flag name out of `_denied_reason(...)` as
three phantom "builtins" and reporting them as leaks.

**Suggestion: expose the mapping as data.** Something like
`nodus.builtins.registry.GATED_BUILTINS -> {"allow_subprocess": (...), ...}` would let consumers
assert whole-surface confinement without depending on your source layout. `SUBPROCESS`,
`NETWORK` and `ENV` already exist as capability labels; this is the adjacent list.

Low priority — we can keep scraping — but it is the kind of thing that breaks quietly on your
refactors and loudly on ours.

### 4.3 We depend on a private method

`nodus_worker.py:409` calls `runtime._get_active_vm()`. Private, so no compatibility promise, and
nothing else in our repo referenced it — it is now pinned by a test so a rename fails loudly
rather than at runtime.

If there is a supported way to reach the active VM, we will switch. If not, consider blessing it.

### 4.4 Builtin-override refusal is a security boundary for us

`register_function` refusing to override a builtin —

```
ValueError: Cannot override built-in function: syscall
```

— is what makes our `std:sys` fail-loud guard sound. Idiomatic `import "std:sys"` routes to
nodus's own 4-syscall stub rather than our dispatcher; because we cannot alias the builtin, we
install a guard that fails loudly instead of silently doing the wrong thing. **If overrides ever
became permitted, a guest could redefine `syscall` and walk past that guard.**

We had documented this refusal in a docstring and never asserted it. Now asserted against the
real VM for `print`, `len` and `syscall`.

---

## 5. Things 5.0.0 got right, worth keeping

- **`NodusRuntime.__init__` has no `**kwargs`.** This matters more than it looks: with a
  catch-all, a renamed confinement flag would be *silently swallowed* and the guest would run
  unconfined with every mock-based test still green. Without one, a rename raises `TypeError` and
  the embedder fails closed. We now assert the absence of the catch-all.
- **The confinement flags are keyword-only.** Positional acceptance would let an argument reorder
  silently change which boundary is being denied.
- **The denial names the flag.** Whatever the surrounding wording, saying *which* boundary
  refused is the part that makes the error actionable.

---

## 6. Status and links (`aindy-runtime`)

| PR | What | State |
|---|---|---|
| #467 | Nodus upgrade contract — the checklist, executable against the installed package | merged |
| #468 | `nodus-lang` 4.2.0 → 5.0.0 | **draft, blocked on `nodus-mcp`** |
| #469 | Guard: a pin that cannot be installed fails locally, naming the capping package | open |
| #470 | Docs: the ordering above | open |

The runtime stays on `nodus-lang==4.2.0` until a compatible `nodus-mcp` ships. #468 is rebased
and merges the day it does — the adoption work itself (31 builtins re-verified, discovery
retargeted, defaults assertion inverted) is complete.

The next runtime release (2.4.0) is **not** blocked by any of this; it ships on 4.2.0 and picks
up nodus 5 in a later release.
