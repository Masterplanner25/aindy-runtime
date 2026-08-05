# A.I.N.D.Y. Isolation Model — Tiered Contract Plan

**Date:** 2026-05-23
**Decision:** Tiered Isolation Contract (Tier 1: trusted operator /
  Tier 2: third-party extension)
**Status:** Planning — no implementation has begun

---

## The Architectural Decision

aindy-runtime adopts a Tiered Isolation Contract with two explicit tiers. Tier 1
covers first-party and trusted-operator code. Kernel-resident callables —
syscalls, jobs, event handlers, agent tools, planner backends, flow strategies,
manifest bootstrap modules for `runtime-built-in` and `first-party-app`, and the
core manifest/bootstrap registry mutation path — are legitimate kernel code. They
run in the main interpreter because that is what kernel code does. They are not
exceptions to an isolation rule; they are the rule for this tier. Capability
mediation gates what each caller may register, but after registration the callable
runs as trusted kernel code with full interpreter access. This is intentional by
design, not a residual compromise.

Tier 2 covers external third-party extension authors. Every surface accessed by
third-party code is and must remain behind the isolated plugin-host boundary. The
runtime routes all third-party dynamic plugin node execution through the
plugin-host subprocess channel managed by `AINDY.platform_layer.plugin_host` and
the sandbox runner interface defined in `AINDY.platform_layer.sandbox_runner`. No
third-party Python ever executes in the kernel process. No exceptions. Third-party
manifest bootstrap is explicitly blocked. Third-party plugin node execution goes
through a subprocess boundary with explicit capability grants, allowlist-only
environment exposure, and a provenance admission gate. The plugin-host boundary is
a real execution boundary, not a policy promise.

This two-tier model is the honest production contract for aindy-runtime today. The
runtime is not claiming OS-level process isolation for Tier 1 code — and it should
not, because Tier 1 code is owned and deployed by the same operator running the
runtime. The runtime is also not claiming a full hypervisor-grade sandbox for
Tier 2 code — the current runners range from `insecure_dev_subprocess` (a
containment improvement, not a sandbox) to `containerized_oci` and
`strong_sandbox_vm` (Linux-only, with published assurance limitations). What the
model does claim is an unambiguous, defensible, and verifiable boundary: Tier 1
code runs kernel-resident because it is trusted, and Tier 2 code never runs
kernel-resident because it is not. Future work may deepen sandbox assurance for
Tier 2 or externalize specific Tier 1 surfaces, but neither is required for this
milestone's production contract to be defensible.

---

## Current State of the Isolation Model

| Surface | Current execution model | Tier | Correct under tiered contract? |
|---|---|---|---|
| manifest bootstrap — `runtime-built-in` | `capability-confined-in-process-exception` | Tier 1 | **No** — code is correct, label is wrong; should be `kernel-resident` |
| manifest bootstrap — `first-party-app` | `capability-confined-in-process-exception` | Tier 1 | **No** — code is correct, label is wrong; should be `kernel-resident` |
| manifest bootstrap — `external-third-party` | unsupported / blocked | N/A | Yes — correctly blocked |
| manifest declarative entries — any owner | `kernel-resident` (parsing + validation only) | Tier 1 | Yes |
| registry kernel-callables — `runtime-built-in` (syscalls, jobs, event handlers, agent tools, planner backends, flow strategies, response adapters, route guards) | `kernel-resident` | Tier 1 | Yes — code is correct; "extension-like" framing in docs is wrong |
| registry kernel-callables — `first-party-app` (same categories) | `kernel-resident` | Tier 1 | Yes — code is correct; same docs issue |
| runtime callback workers — `runtime-built-in` (startup hooks, planner context providers, run-tool providers, trigger evaluators, completion hooks, capability-definition providers) | `isolated-externalized` | Tier 1 | Yes |
| runtime callback workers — `first-party-app` (same categories) | `isolated-externalized` | Tier 1 | Yes |
| dynamic plugin nodes — `runtime-built-in` | `kernel-resident` | Tier 1 | Yes |
| dynamic plugin nodes — `first-party-app` | `isolated-externalized` | Tier 2 (for execution) | Yes |
| dynamic plugin nodes — `external-third-party` | `isolated-externalized` | Tier 2 | Yes |
| webhook nodes — any owner | `isolated-externalized` (network boundary) | Tier 2 | Yes |
| webhook subscriptions — any owner | `isolated-externalized` (outbound delivery) | Tier 2 | Yes |
| dynamic flows — any owner | `kernel-resident` (data-only, no extension Python) | Tier 1 | Yes |

---

## Gap Classifications

### Gap 1 — Manifest/bootstrap in-process exceptions not externalized

**Tier relevance:** Tier 1 only

**Resolution path:** CONTRACT CLARIFICATION

**Rationale:** The bootstrap code for `runtime-built-in` and `first-party-app`
runs in-process. This behavior is correct for Tier 1. The problem is the language
describing it. In `AINDY/platform_layer/extension_execution_model.py` (lines
66-83), the surface notes for both `manifest-bootstrap:runtime-built-in` and
`manifest-bootstrap:first-party-app` describe this as the "residual in-process
privileged exception" and as code that "remains an explicit in-process exception."
`docs/runtime/EXTENSION_TRUST_MODEL.md` also uses "privileged in-process
exception" and "explicit bootstrap exception" throughout its operational guidance
section. Under the tiered model, none of this is an exception — it is the
intentional design for Tier 1. The code runs correctly; the labels are wrong.

**Done condition:** `docs/runtime/EXTENSION_TRUST_MODEL.md` and the surface notes
in `extension_execution_model.py` no longer use "residual exception," "privileged
exception," or "explicit exception" to describe runtime-built-in or first-party-app
bootstrap execution. The language states explicitly that Tier 1 bootstrap code is
intentional kernel code, not a compromise.

---

### Gap 2 — Extension-like runtime surfaces still kernel-resident by design

**Tier relevance:** Tier 1 only

**Resolution path:** CONTRACT CLARIFICATION

**Rationale:** The surface matrix in `extension_execution_model.py` (lines 127-178)
correctly assigns `kernel-resident` to `registry-kernel-callable:runtime-built-in`
and `registry-kernel-callable:first-party-app`. The examples listed — syscalls,
jobs, event handlers, response adapters, route guards, agent tools, planner
backends, flow strategies — are all legitimate kernel-resident Tier 1 callables.
The code is correct. The problem is in how docs frame these surfaces.
`docs/runtime/EXTENSION_TRUST_MODEL.md` lists them under "Trusted Extension
Classes" and discusses them alongside extension-specific hardening language. Under
the tiered model, these are not extensions at all — they are kernel code registered
by trusted operators. The "extension-like" framing in the document structure implies
these surfaces require the same scrutiny as extension code, which they do not.
`docs/runtime/EXTENSION_CAPABILITIES.md` correctly scopes capability confinement
to external third-party surfaces but does not explicitly name the Tier 1/Tier 2
distinction, leaving the boundary implicit.

**Done condition:** `docs/runtime/EXTENSION_TRUST_MODEL.md` explicitly distinguishes
Tier 1 kernel-resident callables from Tier 2 extension surfaces and does not list
syscalls, jobs, event handlers, agent tools, planner backends, or flow strategies
under the extension hardening or "trusted extension" sections. `EXTENSION_CAPABILITIES.md`
names the Tier 1/Tier 2 distinction explicitly.

---

### Gap 3 — Strong-sandbox live verification is worker-level proof, not kernel-level proof

**Tier relevance:** Tier 2 only

**Resolution path:** SCOPE A COMPLETE, SCOPE B DEFERRED

**Rationale:** The `_verify_post_launch_state` function in
`AINDY/platform_layer/plugin_host.py` (lines 343-466) performs post-launch
verification via an authenticated RPC probe to the worker subprocess. The worker
self-reports its isolation state (`import_guard_active`, `filesystem_guard_active`,
`network_guard_active`, and mount/network policy checks). The certification in
`AINDY/platform_layer/sandbox_certification.py` (lines 24-40) defines the required
evidence fields and checks for their presence in the verification result. The
limitation is correctly disclosed: `plugin_host.py` line 459 states "Post-launch
verification is limited to live worker continuity and guard-state checks, not
blanket proof of ongoing kernel enforcement." `EXTENSION_TRUST_MODEL.md`
corroborates this in its Assurance Reporting section. The disclosure is accurate.
Closing this gap fully would require kernel-observable verification of worker
constraints (reading cgroups membership, confirming seccomp activation, verifying
namespace separation) from a component that is not the worker itself — dedicated
sandbox launcher infrastructure not in scope for this milestone.

**Status note:** Scope B1 complete: unprivileged kernel-observable evidence is
now collected via `/proc/<pid>/status` (seccomp), `/proc/<pid>/cgroup`, and
`/proc/<pid>/ns/` after worker launch on Linux. `verification_method`
transitions to `kernel-observable` when evidence is available.
`assurance_ceiling` transitions to `kernel-observable-verified` on Linux hosts
where evidence is collected. Scope B2 (privileged launcher with BPF filter
introspection) remains a future option if the threat model requires it - no
current condition to reopen.

**Why deferred:** The limitation is already accurately disclosed and does not
affect the tiered model's binary claim (Tier 2 surfaces are externalized; they are
not kernel-resident). The strength of the Tier 2 sandbox is a separate question from
whether the tier boundary exists.

**Condition to reopen:** The runtime gains a dedicated sandbox launcher process with
OS-level verification of worker constraints (cgroups reads, seccomp activation
checks, Linux namespace membership verification) that does not rely on the worker's
self-report. This is infrastructure work requiring a privileged sandbox launcher.

---

### Gap 4 — Strong-sandbox support is Linux-only

**Tier relevance:** Tier 2 only

**Resolution path:** PARTIALLY CLOSED — container-grade closed (C2, 2026-05-24);
strong-sandbox remains deferred (C3)

**Rationale:** `AINDY/platform_layer/sandbox_runner.py` defines
`STRONG_SANDBOX_SUPPORTED_HOST_PLATFORMS = (PLATFORM_LINUX,)` and
`HOSTILE_THIRD_PARTY_SUPPORTED_HOST_PLATFORMS = (PLATFORM_LINUX,)`. The platform
capability matrix is published via `/api/version`. `docs/runtime/EXTENSION_TRUST_MODEL.md`
documents the full platform matrix (Linux, Windows, macOS, Other).

**Container-grade (C2) — CLOSED 2026-05-24:** Windows and macOS hosts with Docker
Desktop or Podman configured for Linux containers now reach
`container-sandbox-certified`. `_detect_linux_container_backend` detects the Linux
container backend at startup and gates `production_safe_third_party_plugin_execution`
on container backend semantics rather than host OS. See `C2_SANDBOX_AUDIT.md` NF-1
through NF-8. Live-verified on Windows + Docker Desktop.

**Strong-sandbox (C3) — DEFERRED:** `strong-sandbox-tier` and `hostile-third-party`
profile support remains Linux-only. Closing this requires platform-specific sandbox
runtimes (Windows Containers, WSL-mediated isolation, macOS Virtualization.framework)
— infrastructure investments outside current scope. See TECH_DEBT.md C3 entry.

**Condition to reopen C3:** A non-Linux host platform gains a supported sandbox runner
type with assurance class `strong-sandbox-tier`, verified through the shared worker
policy certification suite (`tier_status: certified` at `strong-sandbox-certified`).

---

### Gap 5 — Execution-model matrix still has three categories including capability-confined-in-process-exception

**Tier relevance:** Tier 1 only

**Resolution path:** CODE CHANGE

**Rationale:** `AINDY/platform_layer/extension_execution_model.py` (lines 12-16)
defines three execution model class constants:
`EXECUTION_MODEL_KERNEL_RESIDENT = "kernel-resident"`,
`EXECUTION_MODEL_ISOLATED_EXTERNALIZED = "isolated-externalized"`, and
`EXECUTION_MODEL_CAPABILITY_CONFINED_IN_PROCESS_EXCEPTION = "capability-confined-in-process-exception"`.
The surface matrix (lines 51-370) assigns this third class to
`manifest-bootstrap:runtime-built-in` (line 57) and `manifest-bootstrap:first-party-app`
(line 75). The `execution_model_classes` list in the published contract (lines 28-49)
exposes all three classes to consumers of `/api/version`. `tests/unit/test_runtime_public_contract.py`
asserts the presence of `"capability-confined-in-process-exception"` at lines 105
and 132. Under the tiered model, this third category is architecturally wrong:
bootstrap code for Tier 1 is kernel-resident trusted code, not a "capability-confined
exception." The distinction the third category attempts to draw — "in-process but
confined by capability checks at registration time" — conflates registration-time
mediation with execution-time confinement. Under the tiered model, these are two
different things. Registration-time capability checks are registration gates. After
registration, Tier 1 callables run kernel-resident. There is no third tier.

**Done condition:** `EXECUTION_MODEL_CAPABILITY_CONFINED_IN_PROCESS_EXCEPTION`
is removed from `extension_execution_model.py`. The `execution_model_classes` list
in the published contract exposes exactly two classes: `kernel-resident` and
`isolated-externalized`. Bootstrap surfaces (`manifest-bootstrap:runtime-built-in`,
`manifest-bootstrap:first-party-app`) are reclassified to `kernel-resident`.
The `registration_boundary` field is renamed or supplemented to clarify that
for Tier 1 surfaces it describes a registration-capability gate, not an execution
isolation boundary. `tests/unit/test_runtime_public_contract.py` no longer asserts
`"capability-confined-in-process-exception"` and instead asserts the two-tier
model.

---

### Gap 6 — First-party capability confinement is mediation, not isolation

**Tier relevance:** Tier 1 only

**Resolution path:** CONTRACT CLARIFICATION

**Rationale:** `docs/runtime/EXTENSION_CAPABILITIES.md` already states correctly:
"Capability confinement currently applies to external third-party execution surfaces
that cross the isolated plugin-host or contract-driven webhook boundary. It does not
apply to trusted internal Python code: runtime-built-in, first-party-app." However,
the `extension_execution_model.py` surface matrix uses
`registration_boundary = EXECUTION_MODEL_CAPABILITY_CONFINED_IN_PROCESS_EXCEPTION`
for `registry-kernel-callable:runtime-built-in` (line 133) and
`registry-kernel-callable:first-party-app` (line 159). This field name and value
imply that capability confinement applies to these surfaces at execution time, which
is wrong. The capability check governs what may be registered; it does not confine
execution after registration. Similarly, the notes for bootstrap surfaces say "plus
runtime-owned registration capability checks" (line 59) and "restricted runtime-owned
registration allowlist" (line 75) — the word "registration" is correct, but the
`registration_boundary` field value using the capability-confined constant
contradicts the text. This gap is primarily resolved by Gap 5's code change
(removing the constant and renaming the field), supplemented by an explicit
clarifying sentence in EXTENSION_CAPABILITIES.md.

**Done condition:** `EXTENSION_CAPABILITIES.md` contains an explicit sentence
distinguishing registration-time capability gates (Tier 1) from capability
confinement (Tier 2). The `registration_boundary` field in the execution model
surface matrix for Tier 1 surfaces uses a value that names the gate pattern without
implying isolation (e.g., `"registration-capability-gate"` or an equivalent
descriptive string). This gap is automatically closed when Gap 5's code change is
complete and EXTENSION_CAPABILITIES.md receives its doc update.

---

### Gap 7 — Attestation/certification scope does not cover in-process bootstrap exceptions

**Tier relevance:** Both (scope exclusion is correct; reason stated uses wrong framing)

**Resolution path:** CONTRACT CLARIFICATION

**Rationale:** `extension_execution_model.py` lines 339-367 defines `attestation_scope`
with `excluded_surface_ids` correctly listing `manifest-bootstrap:runtime-built-in`,
`manifest-bootstrap:first-party-app`, `registry-kernel-callable:runtime-built-in`,
`registry-kernel-callable:first-party-app`, and `dynamic-plugin-node:runtime-built-in`.
The exclusion is correct under the tiered model — Tier 1 surfaces don't need sandbox
attestation because they are trusted kernel code, not extension code requiring a
sandbox boundary. However, the note in lines 360-363 states: "Plugin sandbox
attestation and certification describe isolated plugin-host execution only. They do
not cover kernel-resident or capability-confined in-process bootstrap surfaces."
The phrase "capability-confined in-process bootstrap surfaces" is the old framing.
Under the tiered model, the correct reason is: "Tier 1 trusted-operator surfaces are
excluded because they are kernel code deployed by the operator, not extension code
requiring a process isolation boundary. Attestation applies only to Tier 2
externalized surfaces." The exclusion itself requires no code change; the note and
any corresponding doc text need updating to use tiered model vocabulary.

**Done condition:** The `attestation_scope.plugin_sandbox_attestation.notes` field
in the execution model contract states the Tier 1/Tier 2 exclusion rationale using
tiered model vocabulary, not "capability-confined" or "exception" language.
`docs/runtime/EXTENSION_TRUST_MODEL.md` Assurance Reporting section reflects this
same framing.

---

## Work Plan

### Section A — Contract Clarification Items

These gaps close through documentation updates and naming changes only. No behavior
changes. No test changes (unless tests directly assert the wrong terminology, which
is covered under B).

---

**A1 — Retire "residual exception" and "privileged exception" language (Gaps 1, 2, 7)**

*Files changed:*
- `docs/runtime/EXTENSION_TRUST_MODEL.md`

*What changes:*

The document currently groups `runtime-built-in` and `first-party-app` bootstrap
under "Trusted Extension Classes" and describes first-party manifest bootstrap as "the
remaining explicit privileged exception set." Under the tiered model, this framing
is wrong: Tier 1 bootstrap code is intentional kernel code, not an exception to a
more-isolated baseline. The section labeled "Trusted Extension Classes" should be
restructured to:

1. Introduce the Tier 1 / Tier 2 vocabulary explicitly at the top of the document.
2. Describe Tier 1 surfaces (manifest bootstrap for `runtime-built-in` and
   `first-party-app`, kernel-resident callables, runtime callback workers,
   `dynamic-plugin-node:runtime-built-in`) as kernel code deployed by the operator,
   not as exceptions.
3. Remove "residual exception," "privileged in-process exception," and "explicit
   bootstrap exception" from all locations.
4. Add language to the Assurance Reporting section clarifying that Tier 1 surfaces
   are excluded from attestation because they ARE the kernel, not because they are
   legacy exceptions awaiting future externalization.
5. Update the Operational Guidance section — specifically the line "Treat first-party
   manifest bootstrap as the remaining explicit privileged exception set" — to instead
   say that Tier 1 bootstrap code is the intentional in-process registration path for
   trusted-operator boot wiring.

*Done condition:* No occurrence of "residual exception," "privileged exception," or
"explicit bootstrap exception" in EXTENSION_TRUST_MODEL.md. The Tier 1 / Tier 2
distinction is named explicitly in the document's ownership model section.

---

**A2 — Add Tier 1 / Tier 2 vocabulary to capability and surface docs (Gaps 2, 6)**

*Files changed:*
- `docs/runtime/EXTENSION_CAPABILITIES.md`
- `docs/runtime/PUBLIC_RUNTIME_SURFACES.md`

*What changes in EXTENSION_CAPABILITIES.md:*

1. Add a "Tier Model Scope" subsection to the Scope section that explicitly names:
   - Tier 1: `runtime-built-in` and `first-party-app` callables — trusted internal
     code, not capability-confined
   - Tier 2: `external-third-party` surfaces behind the plugin-host boundary —
     capability-confined
2. Add an explicit sentence: "The registration-time capability checks for Tier 1
   callables are registration gates. They control what may be registered; they are
   not execution-time confinement. After registration, Tier 1 callables execute as
   kernel-resident trusted code with no runtime capability mediation."
3. Update the manifest-bootstrap entry in the Enforcement section from
   `authority_model: trusted-internal-ambient-authority` to make clear this is
   Tier 1 kernel-resident execution, not capability-confined.

*What changes in PUBLIC_RUNTIME_SURFACES.md:*

1. Add a sentence to the Extension Registration Surfaces section noting that the
   extension registration surfaces listed belong to one of two tiers: Tier 1
   (kernel-resident, trusted-operator) or Tier 2 (externalized, plugin-host
   isolated). The existing surface list already implies this; the sentence makes it
   explicit.
2. Remove any language that implies kernel-resident callables are "extension-like"
   or in a transitional state awaiting externalization.

*Done condition:* EXTENSION_CAPABILITIES.md explicitly distinguishes registration
gates (Tier 1) from execution confinement (Tier 2). PUBLIC_RUNTIME_SURFACES.md
names the two tiers in the Extension Registration Surfaces section.

---

**A3 — Update attestation scope note vocabulary (Gap 7)**

*Files changed:*
- `docs/runtime/EXTENSION_TRUST_MODEL.md` (already covered in A1, but this is a
  distinct sentence change in the Assurance Reporting section)

*What changes:*

The Assurance Reporting section currently explains what assurance class, attestation,
and certification tier mean. It does not explicitly state why Tier 1 surfaces are
outside attestation scope. Add a paragraph after the profile expectations table that
states: "Tier 1 trusted-operator surfaces — manifest bootstrap for `runtime-built-in`
and `first-party-app`, kernel-resident callables, and runtime-built-in plugin nodes
— are excluded from plugin sandbox attestation. These surfaces are trusted kernel
code deployed by the same operator running the runtime. They do not require a
process isolation boundary, and sandbox attestation is therefore not applicable to
them."

*Done condition:* The Assurance Reporting section explains the Tier 1 attestation
exclusion in tiered model terms, without using "exception" language.

---

### Section B — Code Change Items

---

**B1 — Remove third execution model class and reclassify bootstrap surfaces (Gap 5, Gap 6)**

*Current behavior:* `extension_execution_model.py` defines
`EXECUTION_MODEL_CAPABILITY_CONFINED_IN_PROCESS_EXCEPTION` and assigns it as the
`execution_model_class` for `manifest-bootstrap:runtime-built-in` and
`manifest-bootstrap:first-party-app`. It also uses this constant as the
`registration_boundary` value for `registry-kernel-callable:runtime-built-in`,
`registry-kernel-callable:first-party-app`, `manifest-bootstrap:runtime-built-in`,
and `manifest-bootstrap:first-party-app`. The `execution_model_classes` list in
the published contract includes all three categories. The note at the bottom of the
`attestation_scope` block references "capability-confined in-process bootstrap
surfaces."

*Target behavior:* `extension_execution_model.py` defines exactly two execution
model class constants: `EXECUTION_MODEL_KERNEL_RESIDENT` and
`EXECUTION_MODEL_ISOLATED_EXTERNALIZED`. Bootstrap surfaces use
`EXECUTION_MODEL_KERNEL_RESIDENT` as their `execution_model_class`. The
`registration_boundary` field for Tier 1 surfaces uses a descriptive string such
as `"registration-capability-gate"` that names the registration-time mediation
without implying execution-time isolation. The `execution_model_classes` list in
the published contract exposes exactly two classes. The `attestation_scope` note
uses Tier 1 / Tier 2 vocabulary.

*Files affected:*
- `AINDY/platform_layer/extension_execution_model.py`
  - Remove `EXECUTION_MODEL_CAPABILITY_CONFINED_IN_PROCESS_EXCEPTION` constant
    (line 14)
  - Update `execution_model_classes` list (lines 28-49) to expose only two classes
  - Update `manifest-bootstrap:runtime-built-in` and `manifest-bootstrap:first-party-app`
    entries: change `execution_model_class` from the removed constant to
    `EXECUTION_MODEL_KERNEL_RESIDENT`; update `notes` to remove "residual" and
    "exception" language; update `registration_boundary` from the removed constant
    to `"registration-capability-gate"` (or equivalent)
  - Update `registry-kernel-callable:runtime-built-in` and
    `registry-kernel-callable:first-party-app` entries: change `registration_boundary`
    from the removed constant to `"registration-capability-gate"`
  - Update `attestation_scope.plugin_sandbox_attestation.notes` (line 360-363) to
    use Tier 1 / Tier 2 vocabulary
  - Update the module-level `operator_note` (line 367-370) to name the two-tier
    model

*Estimated scope:* Small (< 50 lines changed). All changes are within one file.
The logic does not change — only constant values, string labels, and the
`execution_model_classes` list.

*Done condition:* `"capability-confined-in-process-exception"` does not appear
anywhere in `extension_execution_model.py`. Bootstrap surfaces appear in the surface
matrix with `execution_model_class = "kernel-resident"`. The published contract's
`execution_model_classes` array contains exactly two entries. The `registration_boundary`
field for Tier 1 surfaces does not use a value that implies isolation.

---

**B2 — Update public contract tests for two-tier model (Gap 5)**

*Current behavior:* `tests/unit/test_runtime_public_contract.py` asserts at lines
105 and 132 that `"capability-confined-in-process-exception"` appears in the
execution model contract.

*Target behavior:* Those assertions are replaced with assertions that:
1. The contract contains exactly two execution model classes: `"kernel-resident"`
   and `"isolated-externalized"`.
2. Both `manifest-bootstrap:runtime-built-in` and `manifest-bootstrap:first-party-app`
   surfaces have `execution_model_class = "kernel-resident"`.
3. `"capability-confined-in-process-exception"` does not appear anywhere in the
   contract.

*Files affected:*
- `tests/unit/test_runtime_public_contract.py` (lines 105, 132 and surrounding
  context)

*Estimated scope:* Small (< 20 lines changed). No new test logic; existing
assertions updated.

*Done condition:* `pytest -m runtime_only tests/unit/test_runtime_public_contract.py`
passes with the updated assertions. The test file does not reference
`"capability-confined-in-process-exception"` anywhere.

---

### Section C — Deferred Items

---

**C1 — Strong-sandbox live verification (Gap 3)**

**Why deferred:** The limitation is real — post-launch verification in
`plugin_host.py` relies on the worker's self-report via authenticated RPC, not on
kernel-level observation of cgroups, seccomp, or namespace state. However, the
limitation is accurately disclosed in both code and docs. The tiered model's binary
boundary claim (Tier 2 is externalized) is not affected by this limitation. Closing
the gap fully requires a dedicated privileged sandbox launcher with OS-level
observability into worker constraints — infrastructure work outside this milestone.

**Status note:** Scope B1 complete: unprivileged kernel-observable evidence is
now collected via `/proc/<pid>/status` (seccomp), `/proc/<pid>/cgroup`, and
`/proc/<pid>/ns/` after worker launch on Linux. `verification_method`
transitions to `kernel-observable` when evidence is available.
`assurance_ceiling` transitions to `kernel-observable-verified` on Linux hosts
where evidence is collected. Scope B2 (privileged launcher with BPF filter
introspection) remains a future option if the threat model requires it - no
current condition to reopen.

**Where to track:** Open an issue: "Strengthen strong_sandbox_vm post-launch
verification with kernel-observable constraint evidence."

**Condition to reopen:** The runtime has a sandbox launcher process that can confirm
worker cgroup membership, seccomp filter activation, and Linux namespace separation
without relying on the worker's self-reported isolation state.

---

**C2 — Cross-platform container-grade sandbox (Gap 4) — CLOSED 2026-05-24**

**Reopen condition met:** Windows + Docker Desktop in Linux-containers mode now passes
the shared worker policy certification suite at `container-sandbox-certified`
(`container-grade-sandbox` assurance class). `sandbox_certification_profile` returns
`tier_status: certified` with all four attestation fields launch-verified. NF-1 through
NF-8 in `C2_SANDBOX_AUDIT.md` document the implementation and contract decisions.
`EXTENSION_TRUST_MODEL.md` Supported Platform Sandbox Matrix updated (NF-8).

**Remaining gap — C3:** Strong-sandbox and `hostile-third-party` profile support
remains Linux-only. `STRONG_SANDBOX_SUPPORTED_HOST_PLATFORMS = (PLATFORM_LINUX,)` and
`HOSTILE_THIRD_PARTY_SUPPORTED_HOST_PLATFORMS = (PLATFORM_LINUX,)` are unchanged.
See TECH_DEBT.md C3 entry.

**Condition to reopen C3:** A non-Linux host platform gains a supported sandbox runner
type with assurance class `strong-sandbox-tier`, verified through the shared worker
policy certification suite (`tier_status: certified` at `strong-sandbox-certified`).

---

## Execution Order

Items are ordered by: dependencies first, contract clarification before code changes,
higher-risk code changes last.

| # | Item | Section | Scope | Depends On | Done Condition |
|---|---|---|---|---|---|
| 1 | Retire "exception" language in EXTENSION_TRUST_MODEL.md | A1 | Small (doc edits) | None | No "residual/privileged/explicit exception" in EXTENSION_TRUST_MODEL.md; Tier 1 / Tier 2 named |
| 2 | Add Tier 1 / Tier 2 vocabulary to EXTENSION_CAPABILITIES.md | A2 | Small (doc edits) | None | Registration gate vs confinement named explicitly; Tier model scope added |
| 3 | Add Tier 1 / Tier 2 vocabulary to PUBLIC_RUNTIME_SURFACES.md | A2 | Small (doc edits) | None | Extension registration surfaces section names the two tiers |
| 4 | Update attestation scope note in EXTENSION_TRUST_MODEL.md | A3 | Small (1 paragraph) | Item 1 | Assurance Reporting section states Tier 1 exclusion rationale in tiered vocabulary |
| 5 | Remove third execution model class; reclassify bootstrap surfaces | B1 | Small (< 50 lines in one file) | Items 1–4 | `capability-confined-in-process-exception` removed; bootstrap surfaces = `kernel-resident`; two-class model |
| 6 | Update public contract tests for two-tier model | B2 | Small (< 20 lines) | Item 5 | Tests pass; no reference to old constant |

Items 1–4 have no dependencies between them and can be done in any order or
in parallel. Item 5 must follow all A items. Item 6 must follow item 5.

---

## Governance Doc Updates Required

These updates are part of the done condition for the work items above, not
separate items.

| Document | Change | Triggered by |
|---|---|---|
| `docs/runtime/EXTENSION_TRUST_MODEL.md` | Introduce Tier 1 / Tier 2 vocabulary; retire "exception" language throughout; restructure ownership model section; add Tier 1 attestation exclusion rationale in Assurance Reporting | Items 1, 4 |
| `docs/runtime/EXTENSION_CAPABILITIES.md` | Add Tier Model Scope subsection; add explicit sentence on registration gate vs confinement; update manifest-bootstrap enforcement note | Item 2 |
| `docs/runtime/PUBLIC_RUNTIME_SURFACES.md` | Add two-tier naming to Extension Registration Surfaces section; remove "extension-like" framing from kernel-resident callables | Item 3 |

The following docs require no changes as part of this plan:

| Document | Reason |
|---|---|
| `docs/runtime/EXTENSION_ABI.md` | ABI versioning and manifest shape are unchanged |
| `docs/runtime/EXTENSION_PROVENANCE.md` | Provenance policy and integrity contract are unchanged |
| `docs/runtime/DEPLOYMENT_PROFILES.md` | Profile enforcement, assurance requirements, and sandbox matrix are unchanged |
| `docs/runtime/PUBLIC_API_CONTRACT.md` | Import boundary contract is unchanged |
| `docs/runtime/CI_OWNERSHIP.md` | CI ownership is unchanged |
| All other `docs/runtime/*.md` | Out of scope |

---

## What This Plan Does NOT Change

The following files, behaviors, test suites, and CI steps are correct as-is under
the tiered model and must not be touched during this plan's execution.

**Source files — unchanged:**
- `AINDY/platform_layer/plugin_host.py` — plugin host lifecycle, attestation, heartbeat, post-launch verification
- `AINDY/platform_layer/sandbox_runner.py` — runner implementations, platform matrix, assurance class definitions
- `AINDY/platform_layer/sandbox_certification.py` — certification tier logic, evidence field requirements
- `AINDY/platform_layer/deployment_contract.py` — profile enforcement, hostile-third-party gating
- `AINDY/platform_layer/extension_policy.py` — owner class validation, prefix enforcement, bootstrap module name validation
- `AINDY/platform_layer/extension_abi.py` — ABI version policy
- `AINDY/platform_layer/extension_provenance.py` — provenance derivation and integrity
- `AINDY/platform_layer/extension_provenance_inventory.py` — inventory reporting
- `AINDY/platform_layer/extension_boundary.py` — context sanitization
- `AINDY/platform_layer/extension_capabilities.py` — capability set enforcement for Tier 2
- `AINDY/platform_layer/extension_worker.py` — worker subprocess management
- `AINDY/platform_layer/extension_runtime_api.py` — extension-side runtime API
- `AINDY/platform_layer/registry.py` — registration logic and capability audit
- `AINDY/platform_layer/node_registry.py` — dynamic node registration and plugin isolation enforcement
- `AINDY/platform_layer/bootstrap_contract.py` — dependency graph validation
- `AINDY/platform_layer/bootstrap_graph.py` — boot order resolution
- `AINDY/platform_layer/runtime_callback_host.py` — callback worker dispatch
- `AINDY/platform_layer/runtime_callback_worker.py` — callback worker subprocess
- `AINDY/platform_layer/public_contract.py` — contract metadata assembly
- `AINDY/kernel/syscall_dispatcher.py` — syscall dispatch, capability enforcement, tenant isolation
- `AINDY/kernel/syscall_registry.py` — syscall registration
- `AINDY/kernel/event_bus.py` — event bus implementation
- `AINDY/kernel/resource_manager.py` — quota enforcement
- `AINDY/kernel/circuit_breaker.py` — circuit breaker
- `AINDY/kernel/scheduler/` — all scheduler engine files

**Test files — unchanged:**
- `tests/unit/test_plugin_host.py`
- `tests/unit/test_plugin_sandbox_certification.py`
- `tests/unit/test_sandbox_runner.py`
- `tests/unit/test_extension_abi.py`
- `tests/unit/test_extension_hardening.py`
- `tests/unit/test_extension_ownership.py`
- `tests/unit/test_extension_provenance.py`
- `tests/unit/test_extension_boundary_contract.py`
- `tests/unit/test_syscall_contract.py`

**Behaviors — unchanged:**
- All sandbox runner assurance classes and certification tiers
- Post-launch verification logic and evidence fields
- Deployment profile enforcement and hostile-third-party admission
- Bootstrap module prefix validation and owner class inference
- Plugin node isolation path (plugin host subprocess boundary)
- Webhook node and subscription URL validation
- Dynamic flow shape and size validation
- Provenance admission gate for external third-party nodes
- The Linux-only strong sandbox restriction (deferred, not fixed)
- The worker-level proof limitation of post-launch verification (deferred, not fixed)
