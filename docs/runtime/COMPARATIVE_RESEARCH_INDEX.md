---
title: "Comparative Research Index"
api_version: "1.0"
last_verified: "2026-08-18"
status: current
owner: "platform-team"
---
# Comparative Research Index

> **What this is.** Nineteen external systems have been audited against this runtime. This is the
> index of what each one produced, what it got wrong, and what has already been settled — so a
> finding is not re-derived, a correction is not re-made, and a declined proposal is not
> re-litigated. **It is also the checklist for the next system**, whichever that turns out to be.

**The research lives outside this repository**, in `C:\codev\<name> research\`. Each folder now
carries an `ACCURACY_CHECK_vs_aindy-runtime_2.4.0.md` recording which of its claims survived
verification against source at `v2.4.0`. This document is the in-repo summary; those are the
working papers.

---

## 1. Why external comparison produces a different class of finding

Everything else in `TECH_DEBT.md` was found by looking at this codebase and asking what is wrong
with it. These were found by taking a mature system of a **different shape** and asking what the
runtime could not express for it.

That finds **absent vocabulary rather than broken wiring** — and none of the entries below is a
defect. Nothing is failing today. They are the things a consumer unlike our current consumer would
hit immediately, and they do not surface from the inside. **This is the argument for continuing to
do it**, and the reason the next system studied is worth the time even if this runtime does not
change in between.

Three of the nineteen produced something no source audit could: **CrewAI/Nodus** had a running
implementation to measure rather than a codebase to read, **Claude Code** had a first-party
consumer (Claw) to measure the substrate claim against, and **LangGraph** had a showcase whose own
evaluation *volunteered* the gap rather than defending against it.

---

## 2. The nineteen, and what each produced

| Folder | Pin | Registry entries produced | Standing |
|---|---|---|---|
| **Hermes** | `v2.0.1`-era, 2026-08-14 | `EXEC-ENV-BIND-1`, `IDEM-11` (audit half), `AUTHORITY-VALUE-1`, `QUEUE-DURABILITY-CLASS-1`, `ORCHESTRATOR-SPLIT-1`, `AUDIT-CORRELATION-1` | **Most productive single source.** Six entries. Header stats accurate. |
| **Codex** | `75b557d` / `0e73acf` | `TOOL-SEAM-ISOLATION-1`, `CANCEL-REACH-1`, `HTTP-SCOPE-GAP-1`, `FLOW-PARALLEL-1`, `AUTHORITY-NEGOTIATION-1`, `EGRESS-INPROC-1`, `PROGRESS-CHANNEL-1`, `SCOPE-NAMING-1` | Its §10 enforcement matrix is still the clearest statement of where guarantees are hard vs advisory |
| **Claude Code** | `v2.1.0`, 2026-08-14/15 | `SUBSTRATE-WITNESS-1`, plus the command-transform model folded into `TOOL-SEAM-ISOLATION-1` | Contains the only measured first-party-consumer finding |
| **Aider** | `edd3a80` (`v2.1.0-1`) | `FS-SCOPE-1`, `EFFECT-PARTIAL-1`, `EFFECT-PRECONDITION-1`, `EFFECT-MANIFEST-1`, `EMBEDDED-FLOOR-1`, `PERF-BASELINE-1` | Six entries; the effect-model cluster |
| **MAF / AutoGen** | `d32bd5d` (`v2.1.0-3`) | `FLOW-GRAPH-SIGNATURE-1`, `WAIT-TYPED-CONTRACT-1`, `OTEL-GENAI-SEMCONV-1` | Supplied the worked reference design for `FLOW-PARALLEL-1` |
| **CrewAI / Nodus** | Nodus `v4.2.0-4` | Store 4 on `ORCHESTRATOR-SPLIT-1`; the `cwd=` chain on `GUEST-CONFINE-1`; `NODUS-UPGRADE-2` | Evidence, not hypothesis — a running showcase |
| **LangGraph** | Nodus `v4.2.0-4`, 2026-08-15 | `RECOVERY-GRANULARITY-1`; the merge-conflict sharpening on `FLOW-PARALLEL-1`; a 4th row on `ECOGAP-1`'s replay taxonomy | **The peer on our STRONGEST axis, and the only folder to produce a genuinely new gap** |
| **MetaGPT** | `edd3a80` (`v2.1.0-1`), 2026-08-15 | `COST-GOVERNOR-1` | **The last verified-but-unfiled gap in the corpus**; also the sharpest diagnosis of the recurring error (§4.7) |
| **GPT Engineer** | June 2026-06-24 | `RETRY-CONTEXT-1` | **Cleanest June lens audit** — the only one with no table-count claim and no nodus pin |
| **Google ADK** | June 2026-06-24 | *(none)* — `HOOK-PRECEDENCE-1` declined; second derivation of `OTEL-GENAI-SEMCONV-1` | Produced no new debt; every absorb item shipped, tracked, or declined |
| **Devika** | June 2026-06-24 | *(none)* | Diagnostic value only — see §4 |
| **Open Interpreter** | June 2026-06-23/26 | *(none)* | **A fork of the Codex monorepo** — not an independent witness on isolation; see the convergence caution in §5 |
| **OpenClaw** | `211ab9e4f` (`v2026.2.22`), audited 2026-08-18 | `INITIATOR-IDENTITY-1` | **The only inbound-event-driven comparand** — work arrives, it is not requested. Its one new finding could not have come from any other system in the corpus |
| **Pi** | `666d8972f` (`v0.84.0`), audited 2026-08-18 | `LEASE-FENCE-1` | **The only comparand that IS the layer we exclude** — and over 2 541 commits it grew a substrate underneath itself. See §5a |
| **OpenHands** | runtime `0896d11`, 2026-08-15 | `AUTHORITY-LIFETIME-1` | **The only peer that makes the same claim we do** — and the folder that corrected two of my own filings (§5 row 4; G2's witness list) |
| **SWE-agent** | June 2026-06-24/26 | `RETRY-CLASSIFY-1` | **The last isolation witness standing** — tools are executables uploaded *into* the deployment (`tools.py:252→266→269`); the host has nothing to call. Verified 2026-08-19 |
| **LiteLLM** (spend subsystem only) | `c696fdf`, MIT, audited 2026-08-19 | *(no new prefix)* — worked answers to `COST-GOVERNOR-1` (all four design questions) and `INITIATOR-IDENTITY-1` (the accounting half) | **The one hole, already solved.** reserve → call → reconcile |
| **DBOS Transact** | `e0b742c`, MIT, audited 2026-08-19 | *(no new prefix)* — worked references folded into **five** entries; one challenge to a sixth | **The same architectural bet, taken further.** Highest findings-per-LOC in the corpus: 31 650 lines, both sides source-verified. See §5d |
| **Temporal** | June 2026-06-24 | second witness for `LEASE-FENCE-1` | **The corpus's calibration instrument** — the only comparand that is *purely* substrate, so its 55–65% band is the one number not inflated by app-hosted content. Origin of the *replay vs re-run* framing |
| **Linux kernel** | Linux 7.1.0, 2026-06-27 | *(none)* — two design principles folded into `TOOL-SEAM-ISOLATION-1` and `EXEC-ENV-BIND-1` | **Different in kind: supplies the VOCABULARY for why five open entries are the right shape** — see §4a |

Provenance headers in `TECH_DEBT.md`: `AIDER-PORTABILITY-2026-08-17`, `MAF-REFERENCE-2026-08-17`,
`CREWAI-NODUS-2026-08-18`, `ADK-LENS-2026-08-18`, `LANGGRAPH-NODUS-2026-08-18`. Codex, Claude Code, Hermes and GPT Engineer
entries cite their source documents inline.

---

## 3. Settled — do not re-litigate

Each of these was proposed by at least one audit, considered, and closed with reasons. The
reasoning is in the linked entry; this table exists so the proposal is recognised on sight.

| Proposal | Verdict | Where the reasoning lives |
|---|---|---|
| **Kernel deterministic replay** (Temporal-style: record non-deterministic results, re-run code with them injected) | **Declined.** Determinism is a VM concern not a kernel one; forward-resume never re-executes code so the problem does not arise; it is a constraint on every line of workflow code rather than a feature | `ECOGAP-1`, which now carries a **four-way** taxonomy because seven audits have said "replay" meaning different things. **★ Row 4 (pending-writes) is NOT covered by this decline — see `RECOVERY-GRANULARITY-1`** |
| **First-non-`None`-wins hook precedence** (ADK) | **Declined.** It makes a handler's effect depend on registration order relative to handlers it cannot see — a silent override path in a system selling auditable authority | `HOOK-PRECEDENCE-1` |
| **A `sys.v1.repo.commit` / filesystem syscall** (Aider's first framing) | **Declined.** Binds the substrate to one resource class. The absorbable thing is a scope vocabulary, never a resource verb | `FS-SCOPE-1`; Hermes reached the same conclusion independently as its N3 |
| **`EffectPrecondition` as content-addressed snapshots inside the runtime** | **Declined in that form.** It makes the substrate authoritative over state it does not own. The adapter form — record the foreign system's own version token — passes | `EFFECT-PRECONDITION-1` |
| **A general dispatch hook system** (Claude Code) | **Declined.** An interception seam runs someone else's code in the kernel process, which the Tiered Isolation Contract reserves for Tier 1. A single narrow declared arbiter is the shape | `DISPATCH-ADMISSION-1` |
| **Absorbing context assembly / compaction / conversation state** | **Declined**, and reached separately by three audits | `WHAT_THE_RUNTIME_IS.md` §5 |
| **A model in a control-plane role** — an LLM deciding *who may act next* (MetaGPT `TeamLeader`, Codex guardian approver) | **Declined, derived twice independently.** Non-deterministic, unauditable, prompt-injectable, untestable. **A substrate that delegates *who may act next* to a model has no invariant left to offer.** The correct split is already ours: the LLM may *propose* a delegation; the runtime decides whether it is permitted (`agent_message_bus`'s typed `operation_accept`/`operation_reject`), records it, and recovers it | MetaGPT analysis §6; Codex analysis do-not-absorb list |

---

## 4. The recurring errors — check these first on the next system

Six of the seventeen June-era folders share a defect set. **Reading this list before starting a new audit is
worth more than anything else here**, because these are not one-off mistakes — they are what
happens structurally when a comparison is written from a sibling document instead of from source.

1. **★ Inventory read accurately, reachability read optimistically.** The single most common error,
   named independently by four documents. A mechanism exists, is cited correctly by file and line,
   and is **not wired to the seam being discussed**. Examples: sandbox tiers credited for tool
   execution (`TOOL-SEAM-ISOLATION-1`); a boot-time route AST proof with no call site
   (`ROUTE-AST-UNWIRED-1`); `EffectRecord` EXACTLY_ONCE cited as a delivered property while behind
   a default-off flag (`IDEM-11`). **Before citing a mechanism as evidence, confirm it executes on
   the path in question.**
2. **★ Figures that travel between documents change meaning.** `RUNTIME_MODULE_MAP.md` said
   *"27 runtime-owned ORM tables."* One June document copied it faithfully; the next rewrote it as
   *"27-table **TenantContext** schema"*, and three siblings written the same day inherited the
   tenancy framing. Five documents then scored a multi-tenancy guarantee none had checked. The
   number was also stale (36 today). **A figure that does not return to source will eventually be
   wrong in category as well as value.**
3. **The "already covered" sections are where the errors live.** Both errors
   `AUDIT-INVARIANTS-VERIFIED-1` found were in no-change rows, and Hermes's N-list has one refuted
   finding and one right-but-unwired credit out of nine. **Verify the guarantees, not just the
   gaps.**
4. **A correct grep can answer the wrong question.** Two documents concluded "OpenAI is
   hard-required for embeddings" from `grep 'def create_embedding' → one file`. The grep is
   correct; `create_embedding` is the OpenAI client's own function name, and the abstraction's
   method is `embed_one` on an `EmbeddingProvider` Protocol.
5. **Counts drift and are rarely re-derived.** Three documents gave three different wrong syscall
   counts (24, 24, 25) for a number that is a named constant — `SYSCALL_REGISTRY_MIN_COUNT` — in
   the file all three cited. One gave 533 `.py` files for a directory holding ~340.
7. **★ Placement mistaken for existence — the most dangerous variant, because there is no code to
   check.** The June MetaGPT lens audit wrote *"Token/$ ceiling is a kernel resource-governance
   concern"* — a **placement** claim, and correct — and filed the row as **Covered**, an
   **existence** claim, and false. There is no cost governor (`COST-GOVERNOR-1`). Unlike error #1,
   a reader who follows the citation finds a correct architectural statement and no contradiction.
   **Ask of every "Covered" row: does this describe where the capability belongs, or that it is
   there?**
6. **Version pins go stale silently.** `nodus-lang` was quoted at 3.0.2, 4.0.5, 4.1.0 and 4.2.0
   across the corpus. It is now `5.0.1` in this runtime and `5.0.4` upstream. The one document that
   named no version never went stale.

**Facts that were wrong in more than one folder and are now fixed at source:** the table count
(`RUNTIME_MODULE_MAP.md`), the provider/embedding claim (`ECOSYSTEM_CAPABILITY_GAPS.md` G3), the
MCP client's existence (`ECOGAP-4`'s superseded bullet), and the delegation-ceiling and
multi-tenancy overclaims (`WHAT_THE_RUNTIME_IS.md` §4/§6).

---

## 4a. The one folder that supplies vocabulary rather than gaps

The Linux kernel audit produced **no** debt entry and is the most useful document in the corpus for
a different reason: **five open entries each have a decades-tested precedent for why their shape
matters**, and it names them.

| Lesson | Principle | Entry it argues for |
|---|---|---|
| **4** | Authority is an explicit, **immutable, attached** object; mediate at a hook | `AUTHORITY-VALUE-1` — ours is a caller-constructible `list[str]` |
| **5** | Isolation is **orthogonal axes** (visibility / resources / authority), not a trust level | `EXEC-ENV-BIND-1` — settle the shape before the field list |
| **7** | Graded extensibility **by trust**, with different enforcement points per tier | the Tier 1 / Tier 2 model, which holds |
| **8** | Defer work into tiers **bounded by what each tier may do** | `FR-15` — the deferred tier is off by default |
| **10** | **Opaque handles, not direct references**, across a trust boundary | `TOOL-SEAM-ISOLATION-1` — `execute_tool` hands the tool a live DB session |

**★ And it is the cleanest instance of the §4.1 error in the corpus**, which is why it is worth
reading alongside its own successor. Its Lesson 4 states the principle correctly — *"explicit,
**immutable**, attached"* — and then credits us with satisfying it. Seven weeks later
`aindy-runtime-vs-linux.md` reached the opposite verdict on the same property: *"its mediation is
**by convention, not by structure**."* **Same system, same property, opposite conclusions — the
later one correct.** The June document read design intent; the August one read enforcement.

---

## 5. What the corpus says collectively

**★ Read this before citing convergence as evidence. The auditors are not independent.**

All twelve documents were produced by two agent harnesses (Codex and Claude Code), on one model
family, reading the same runtime — and **several explicitly read their predecessors**: the MAF
study cites four companions, the LangGraph study six. So "two documents agree" is close to
worthless on its own, and this file previously over-claimed on exactly that basis.

**The mechanism was already documented here and not applied.** §4.2 traces how *"27 runtime-owned
tables"* became *"27 tenant tables"* — one document copied faithfully, the next corrupted it, three
siblings inherited it in a single day. **That is the same channel that manufactures false
convergence.** An error propagates through it; so does agreement.

**So the distinction that matters is not how many documents said it — it is whether the agreement
lives in the systems or in the reasoning:**

| | What it means | Worth |
|---|---|---|
| **System-fact convergence** | Two or more **independent codebases** each *built* the mechanism. Verifiable in their source, auditor-independent. | **Real evidence** |
| **Auditor-reasoning convergence** | Two or more **documents** each *proposed* the primitive. Correlated priors, often with the earlier document in context. | **Weak — treat as one opinion** |
| **One fact, observed N times** | N documents noticed the same single property of *our* code. | **Not convergence at all** |

Re-scored on that basis:

| Primitive | Convergence type | Standing |
|---|---|---|
| **Pending-writes / completed-unit durability** | **System-fact ×2** — LangGraph and MAF each *implement* it. *(★ Corrected 2026-08-19: OpenHands was counted here on a sibling document's summary; its per-event blob log is row 1, which we already ship. **Cited without reading the system — the failure this very section warns about.**)* | Real, on two arrivals. `RECOVERY-GRANULARITY-1` |
| **Retry that carries the prior failure forward** | **System-fact ×2** — GPT Engineer's `_improve_loop`/`self_heal`, Aider's per-hunk failure reporting | **Real.** `RETRY-CONTEXT-1` |
| **OTel GenAI semconv alignment** | **System-fact ×2** — MAF and ADK both *emit* semconv spans | **Real.** `OTEL-GENAI-SEMCONV-1` |
| **Cost governor** | **System-fact ×1** + arithmetic — MetaGPT's `CostManager` exists; our four quotas contain no cost dimension. **Not convergence; a measurement.** | **Strong for a different reason** — `COST-GOVERNOR-1` |
| `EffectPrecondition` | **Mixed** — the *proposal* is auditor reasoning; the *reference* (Aider's Git dirty-commit + hash + guarded undo) is a system fact | Keep, discount the derivation count |
| Fan-out / join | **One fact, observed 4×** — `resolve_next_node` returns one successor. Four documents noticing it is one observation, not four. *(MAF and LangGraph **having** the mechanism is separate, and is system-fact evidence for the **shape**.)* | **Was inflated.** The gap is real; the "four independent derivations" framing was not |
| `ExecutionDurabilityTier` | **Auditor reasoning ×2** (Codex N2, Aider B5) — no system built this as such | **Treat as one opinion** |
| Supported embedded profile | **Auditor reasoning ×2** (Codex N8, Aider A6/B6), both reasoning about *our* deployment contract | **Treat as one opinion.** `EMBEDDED-FLOOR-1` still stands on its own measurement — `single-instance` declares `postgres: True` |

**★ What actually survives auditor dependence, and it is most of the registry:** every entry filed
from this corpus was **re-verified against source at `v2.4.0` before filing**, and the accuracy
checks record what did *not* survive. A finding grounded in a line of our code or theirs does not
care who wrote the document that pointed at it. **A finding grounded in agreement between
documents does.**

**And the narrower version of the same error, kept because it is easy to miss:**
`ECOSYSTEM_CAPABILITY_GAPS.md` G2 listed *"OpenHands/OI/SWE-agent"* as three witnesses that we are
behind on default-on sandboxing. **Open Interpreter is a fork of the Codex monorepo** — the
isolation crates it was credited for are `codex-rs`'s, read separately in the Codex folder. The
conclusion held; the count went three → two. **Before citing N arrivals, check that N systems are
N codebases — and that N documents are more than one auditor.**

**★ The layered version of the same check, which the OpenClaw folder answers cleanly.** OpenClaw
*embeds* Pi (`package.json` pins four `@mariozechner/pi-*` packages at `0.54.1`), so a capability
credited to OpenClaw could be Pi's. Checked: the **pinned-digest sandboxes** are OpenClaw's own
(`Dockerfile.sandbox*` at the repo root, outside `node_modules`), as are **cron/wakeup** and the
**52 skills**. So OpenClaw *is* an independent witness on isolation — unlike Open Interpreter.
**Do the check either way; it is cheap, and it came out differently in the two cases.**

**★ The attrition is worth stating as a number, because it is the strongest illustration in this
file.** `ECOSYSTEM_CAPABILITY_GAPS.md` G2 cited peers as *"materially ahead"* on default-on
hostile-safe sandboxing. Verified against their own source, one at a time:

| Cited witness | Verdict |
|---|---|
| Open Interpreter | **Discounted** — a fork of the Codex monorepo |
| OpenHands | **Materially weakened** — container per session with **default settings**; no seccomp, AppArmor, cap-drop or resource limits on the self-hosted path |
| SWE-agent | **Stands** — tools are executables installed *into* the deployment; the host has nothing to call |
| OpenClaw | **Stands** — bind mounts, network mode, seccomp and AppArmor validated before start |

**Two of four did not survive contact with their own source, and the row's *framing* was wrong as
well as its count** — on container hardening we are ahead of OpenHands on the self-hosted path.
**A cited peer advantage is a claim like any other, and it decays the same way.**

**The composite finding across the corpus: the authority model is better specified than the effect
model.** The runtime answers *who is allowed to do this* with cryptographic and transactional
rigour, and answers *what exactly was done, to what version of what, and how much of it succeeded*
with a two-state envelope. Two ports reached that from opposite directions.

**And the one result that is validation rather than gap, because it was tested rather than
argued:** a CrewAI crew expressed as a 39-line `.nd` flow survived four rounds of host deepening —
LLM provider, MCP transport, cross-process A2A with bearer auth, scope-addressed memory — with the
flow file unchanged. The composition boundary holds. The authority boundary has no equivalent
witness (`SUBSTRATE-WITNESS-1`).

---

## 5b. ★★ The headline result of fourteen audits: the substrate gets rebuilt in fragments

**[Observed across the corpus.]** Line up what every serious comparand actually *built*, and the
pattern is not "nobody needs a runtime." It is sharper and more useful:

> **Every serious agent system grows, buys, or hand-rolls part of a runtime. None of them assembles
> the whole set — and they each grow a *different* part, chosen by whichever hurt first.**

| System | Grew / bought | Does **not** have |
|---|---|---|
| **Pi** `v0.84.0` | durable sessions w/ **migrations**, **fenced writer leases**, sequences, branch tips; CBOR protocol; server; telemetry | isolation, authority, effects, scheduling, cost |
| **OpenClaw** `v2026.2.22` | **Docker isolation** w/ validated bind mounts, network mode, seccomp, AppArmor; **cron**; tool policy | durability beyond JSONL, effects, real authority (glob on names) |
| **Codex** | ~72 000 LOC of 3-OS **sandboxing/exec**; Starlark exec-policy; approval flow | durable execution (its rollout log is *conversation* replay), scheduling, effects |
| **MAF** | BSP graph, per-superstep checkpoints, graph-signature resume; **durability bought from Azure Durable Task** | authority (approval is opt-in middleware), isolation (none in core) |
| **LangGraph** | **durability** — reducers, versioned triggering, pending-writes-then-checkpoint, 3 durability modes | isolation, authority, tenancy |
| **MetaGPT** | **`CostManager` / `NoMoneyException`** — the corpus's only cost governor | durability, isolation, effects |
| **ADK** | event-sourced state fold, plugin hooks | durable checkpointing (its own `# TODO`), scheduling, idempotency enforcement, core isolation |
| **Temporal** | **durability, gold standard** | isolation, capability model, agent concerns |
| **Aider / Devika** | nothing — Git as system of record / two 0-byte sandbox files | everything (Aider by design; Devika by poverty) |

**★ The two-team version, which is the sharpest single instance.** OpenClaw pins Pi at `0.54.1`
(2026-02-22). Pi's `server`, `protocol`, `session-backends` and `telemetry` first appear on
**2026-07-21, 07-30, 08-05 and 08-05** — *five months later*. So this is not one team building a
substrate: it is **two teams, in one stack, growing different halves of a runtime without
coordinating**. OpenClaw took isolation and scheduling; Pi took transport and durable sessions.
**Neither has the other's half, and neither has authority, effects or cost governance.**

### What this licenses the runtime to claim, and what it does not

**Licensed:** the category is real, and it is validated by construction rather than by opinion —
nine independent teams each built part of it. **This is system-fact evidence, the strong kind by
§5's taxonomy**, and it is the single best answer to *"is the substrate claim true?"* that fourteen
audits produced.

**Not licensed:** *"and therefore they will adopt ours."* §5a is the counterweight — a serious
framework does not wait for a substrate, it grows one, and `SUBSTRATE-WITNESS-1` records that the
runtime has one first-party consumer testifying about the HTTP API. **Category validation and
adoption are different claims and only the first is evidenced.**

### Where the runtime actually stands on the six

Honest scoring, from this session's source verification:

| Concern | Status |
|---|---|
| **Durability** | **Have it** — the strongest axis; only LangGraph and Temporal are peers |
| **Scheduling** | **Have it** — durable, user-facing, restart-surviving; ahead of every comparand |
| **Authority** | **Have it, strongest in the corpus** — HMAC, TTL, plan-derived, ceiling-clamped |
| **Isolation** | **Built and mis-wired** — `TOOL-SEAM-ISOLATION-1`; Codex and OpenClaw are ahead *in enforcement* while behind in authority |
| **Effects / idempotency** | **Built and default-off** — `IDEM-11`; nobody else has it at all |
| **Cost governance** | **Absent** — `COST-GOVERNOR-1`. **The one concern where a comparand has something and we have nothing**, and it is MetaGPT, the least sophisticated system in the table |

**[Inferred]** Four of six are held, one is a wiring problem and one is a genuine hole. **No
comparand scores better than two.** That asymmetry — not any individual feature — is the result
worth carrying out of this corpus.

---

## 5a. ★ What Pi established, and it reframes the missing-adopter problem

Thirteen audits placed the **agent loop, provider abstraction and conversation state** app-side.
**Pi is that layer**, and it is the only comparand that could test the placement from the inside —
2 541 commits of independent evolution between the version OpenClaw pins and HEAD.

**[Observed]** What it did with that time:

| `v0.54.1` — 2026-02-22 | `v0.84.0` — 2026-08-07 |
|---|---|
| agent, ai, coding-agent, mom, pods, tui, web-ui | agent, ai, coding-agent, tui, **client, protocol, server, session-backends, telemetry**, evals |

It added a **CBOR wire protocol**, a **server with transports**, and **durable session backends
with schema migrations, per-session sequences, branch tips and fenced writer leases** — and kept
the 796-line loop. **It did not pivot. It grew a substrate underneath itself.**

**The placement survives**: the loop is model-shaped and belongs where the corpus put it. **What
did not survive is the corpus's silence about what happens when no substrate is adopted.**

> **The *loop* belongs app-side and stays there. The *substrate the loop needs* does not — and when
> none is offered, the loop's authors build one.** Pi's `server/`, `protocol/` and
> `session-backends/` are not framework concerns that wandered downward; they are runtime concerns
> that had nowhere else to go.

**★ This reframes `SUBSTRATE-WITNESS-1`.** That entry records the substrate claim has one
first-party witness testifying about the HTTP API — read as a *demand* problem. Pi shows the supply
side: **a serious agent framework does not wait for a substrate, it grows one.** Both halves are
real, and the second is not fixable by shipping features.

**And it is the best instance of system-fact convergence in the corpus** — Pi *built* leases,
sequences and migrations. No auditor proposed them; a team shipped them. By §5's taxonomy that is
the strong kind, and it is also how `LEASE-FENCE-1` was found: the excluded layer had a primitive
the substrate lacked.

---

## 5c. ★★ What seventeen systems say, taken together

Read this first if you read nothing else in this file.

### The finding

> **The runtime's problem is not capability. It is completion — and only one of the three
> completion buckets is blocked on anything external.**

Almost nothing the corpus surfaced is *"we cannot do X."* Nearly everything is one of three shapes:

| Shape | Examples | Blocked on |
|---|---|---|
| **Built, not wired** | isolation provider reaches `plugin_host.py` and not the tool seam; the boot-time route AST proof has no call site; `recommended_runtime_requirement` is served and read by nobody; the guest's `cwd=` is never set | **Deciding and doing.** Most ship default-off, so they do not even need soak to land |
| **Built at one granularity, not the next** | pending-writes at the flow node but not the agent step; a lease with expiry but no fence; a token bound to the clock but not the run; four quota dimensions and not the fifth | **Deciding and doing** |
| **Built, not on** | `IDEM-11`, durable continuation, `AINDY_CHILD_CONTEXT_CLAMP`, `FR-15 (a)`, `RTR-4`, `DB-NODUS-BUDGET-1`, `INFINITY-RUNTIME-1`, `NODUS-WARMPOOL-1` | **Soak — i.e. a consumer.** This bucket and only this bucket |

**★ The distinction matters for planning and is easy to get wrong.** It is tempting to say
*"everything is downstream of having no consumer."* **That is false for two of the three buckets.**
Wiring the tool seam, adding a fence column, setting `cwd=`, metering tokens, binding a token to
its run — none of that needs production traffic. **Only the flag flips do.**

### The chain, for the bucket that is blocked

> completion of the **flag** backlog → needs soak evidence → needs production traffic **through the
> flagged path** → no first-party consumer routes effects through any chokepoint
> (`SUBSTRATE-WITNESS-1`) → and there is no instrument to measure it with (`PERF-BASELINE-1`).

**The smallest action that unblocks the most** is still the one `SUBSTRATE-WITNESS-1` names: route
**only** Claw's outbound message delivery through `execute_tool` with a declared `EXACTLY_ONCE`.

### The one genuine hole

**Cost governance.** No meter, no cap, and tokens are the dominant real cost of every workload this
runtime exists to run. It is the only concern of six where a comparand has something and we have
nothing — and the comparand is MetaGPT, the least sophisticated system in the corpus.
`COST-GOVERNOR-1`.

### What is validated — do not re-litigate

- **The boundary.** The loop belongs app-side — tested *from the inside* against Pi, which **is** the
  loop. Composition belongs to Nodus — tested by a 39-line `.nd` flow surviving four host
  substitutions byte-for-byte. Model-in-the-control-plane refused twice, independently.
- **Authority.** Strongest in the corpus by a distance. OpenClaw's is glob-matching on tool names;
  Pi's delegation depth is a substring count on a caller-controlled string.
- **Durability.** Only LangGraph and Temporal are peers; only Temporal beats us.

### How the audits were wrong — consistently in our favour

Inventory read accurately, reachability read optimistically — **and the peers were overrated too.**
Two of four cited isolation witnesses did not survive their own source. Twelve documents asserted a
multi-tenancy guarantee from a stale figure in one of *our* files. Two concluded *"OpenAI is
hard-required for embeddings"* from a correct grep answering the wrong question.

### What seventeen systems could not tell us

Adoption. Interactive workloads — one comparand, one finding (`PROGRESS-CHANNEL-1`). Genuine
multi-tenant scale. And the auditors were not independent of each other, which is why three of six
convergence claims were demoted to *"one opinion"* (§5).

---

## 5d. ★ What auditing a true peer produced — and why it read differently

DBOS Transact (`e0b742c`, MIT, 56 files / 31 650 LOC) is the first comparand that solves **our**
problem with **our** substrate choice: durable execution in one Postgres, no separate orchestrator.
Temporal answered *"shard it"*; DBOS answered *"don't"*, which is what we answered.

**It opened no new prefix, and that is the point.** Every finding landed on an entry that already
existed — as **worked answers**, not new gaps:

| Entry | What the peer supplied |
|---|---|
| `RECOVERY-GRANULARITY-1` | Per-step result durability with replay-not-re-execute, **and** a cheaper identity (a monotonic ordinal) than the vector clock already on file — with the note that the ordinal is the *pre-fan-out* answer and should be built first |
| `EVENT-OUTBOX-1` | **A simpler fix than the one I filed**: no outbox, no relay, no window — because the event store *is* the workflow store, and ours already shares the same Postgres |
| `EMBEDDED-FLOOR-1` | SQLite and Postgres behind one shared implementation, ~120 lines of dialect apart — turning our inference into evidence, and naming pgvector as the real constraint |
| `FLOW-GRAPH-SIGNATURE-1` | A cheaper, coarser alternative (version string on the run vs topology hash) that *is* the trade the entry's open design question describes |
| `ECOGAP-5`, `IDEM-*`, `RETRY-CLASSIFY-1` | `automatic_backfill`; start-boundary `deduplication_id`; named error types |
| `PROGRESS-CHANNEL-1` | **A challenge, not a confirmation** — durable `streams` with offsets, the opposite of that entry's "best-effort by construction." Recorded as open, not resolved |

**★ Three lessons about method, which generalise past this target:**

1. **A peer on your own axis produces answers, not gaps.** Seventeen comparands solved adjacent
   problems and produced *entries*; the one that solved the same problem produced *implementations
   of entries already filed*. **If the backlog is the bottleneck rather than discovery — and §5c
   says it is — this is the higher-yield kind of audit.**
2. **It made one of my own filings smaller.** `EVENT-OUTBOX-1` proposed an outbox table and a
   relay; the peer showed the window closes with a connection argument. **Reading a peer is cheaper
   than reasoning alone, and it corrected downward.**
3. **And it challenged a constraint I had reasoned to alone.** `PROGRESS-CHANNEL-1` says progress
   must be best-effort; DBOS made it durable and deliberate. **One reasoned position against one
   shipped one is not a settled question**, and the entry now says so.

---

## 6. For the next system

0a. **★★ WORKED 2026-08-19 — the queue is clear. Do not re-derive it.** The canonical list is not
   twelve per-audit lists: it is the **consolidated absorb register** in
   `C:\codev\Ecosystem_Coverage_Analysis_v2.md` (deduped across all 12 projects), plus its gap
   register `G1-G6` (already tracked as `ECOGAP-1..6`). All 7 runtime bullets and their ~18
   sub-items were checked against the registry. **Result: 16 already tracked, 1 new
   (`EVENT-OUTBOX-1` — the "+ transactional outbox" half of the Temporal bullet), 1 recorded as a
   cross-reference (Devika's repair-handler stage, on `RETRY-CONTEXT-1`).** The two items found
   earlier — `COST-GOVERNOR-1` and `LEASE-FENCE-1` — were also from this register, which is why it
   was worth walking. **Historical note on the original entry, kept because the lesson holds:**

0b. **★ Being right in a document is not the same as being tracked.** Six lens
   audits each end with a §D list of *gaps that survive correction*. **Two items found in them so
   far had no registry entry**: `COST-GOVERNOR-1` (recorded as *Covered* on a sentence describing
   where the capability belonged) and `LEASE-FENCE-1` (named precisely on 2026-06-24 as
   *"monotonic-RangeID storage-CAS fence vs aindy's DB-lease, now real but weaker"* and filed
   eight weeks later from an unrelated system, believing it new). **The audits were right; being
   right in a document is not the same as being tracked.** The remaining §D lists have not been
   checked.
0. **★ Remember who wrote the corpus.** Twelve documents, two harnesses, one model family — see
   §5. A new document agreeing with an old one is **not** a second data point, especially if the
   old one was in its context. Ground findings in source on both sides, or treat them as opinion.
1. **Read §4 first.** Most of what goes wrong in these audits is structural, not careless.
2. **Pin the runtime commit and record it**, and re-verify rather than inheriting from a sibling
   document — that is where the compounding errors in §4.2 came from.
3. **Check §3 before proposing a primitive.** Six proposals are already settled.
4. **Check §5 before calling something new.** If a second unrelated system has derived it, say so —
   the convergence is the evidence.
5. **Prefer systems with a different shape.** The corpus has coding agents, an orchestration
   framework, a durability engine and a kernel. What it lacks is anything **interactive**
   (`PROGRESS-CHANNEL-1` came from the one interactive comparator and nothing else surfaced it) and
   anything **operating at genuine multi-tenant scale** — the two directions where our own claims
   are least tested. **★ Half of this was discharged by the OpenClaw audit: an inbound-driven,
   multi-channel consumer produced `INITIATOR-IDENTITY-1` immediately, which twelve
   user-initiated comparands never surfaced. The heuristic held — audit the shape you have not
   audited, not the system you find most interesting.**
5a. **★ Audit the whole stack, not one layer of it — a single-layer audit reports that layer's
   absences as the stack's.** `OPENCLAW_ON_NODUS_AUDIT.md` (2026-08-18) correctly finds that
   **Nodus has no scheduling at any layer** and concludes *"a port loses a working feature"*,
   leaving *"where does scheduling live?"* open. **`aindy-runtime` has exactly that feature** —
   `nodus_scheduled_jobs` (PG-backed, `user_id`/`cron`/`script`),
   `POST /platform/nodus/schedule`, and `restore_nodus_scheduled_jobs()` on startup. Every claim
   in that audit is true; the answer to its own open question is one layer down, in the layer that
   folder never audited. **Both named targets are now closed: OpenClaw
   (`OPENCLAW_ON_AINDY_RUNTIME_AUDIT.md` → `INITIATOR-IDENTITY-1`) and Pi
   (`PI_ON_AINDY_RUNTIME_AUDIT.md` → `LEASE-FENCE-1`), both 2026-08-18.**
6. **A folder that produces no new debt is a real result** — Google ADK and Devika both did, and
   both are recorded rather than discarded, so the same items are not re-proposed from the same
   source.
