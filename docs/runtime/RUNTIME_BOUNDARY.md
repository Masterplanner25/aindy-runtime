# Runtime Boundary

> Authored by Codex during non coding session. Needs review before repo commit and push.


This document defines what `aindy-runtime` owns, what it does not own, and how responsibility should be split between:

- `aindy-runtime`
- `aindy-sdk`
- `aindy-ui-kit`

Its purpose is to reduce scope drift, prevent the runtime from becoming a catch-all repo again, and make cross-repo decisions more repeatable.

This is a boundary document, not an implementation inventory.

---

## Canonical Definition

`aindy-runtime` is the execution substrate of the AINDY platform.

It owns the runtime responsibilities required to:

- accept and validate execution requests
- manage runtime lifecycle and startup
- execute and resume orchestrated work
- enforce runtime contracts such as tenant, capability, readiness, and deployment semantics
- expose stable runtime-facing health, version, and execution surfaces

It does **not** own every backend concern in the broader platform.

---

## Design Goal

The runtime should become:

> smaller in responsibility, stronger in guarantees

A mature runtime is not the place where every platform convenience accumulates.
A mature runtime is the place where execution-critical behavior is narrow, predictable, and well defended.

---

## Runtime Owns

These concerns belong in `aindy-runtime`.

### 1. Execution Substrate
- scheduler lifecycle
- wait/resume mechanics
- execution-unit lifecycle
- flow and runtime execution coordination
- recovery and rehydration behavior

### 2. Syscall and Capability Boundary
- syscall registration and dispatch
- syscall schema enforcement
- capability enforcement
- tenant enforcement in runtime execution paths
- idempotency/effect recording required for runtime correctness

### 3. Runtime Lifecycle and Boot
- startup ordering
- runtime-only deployment boot
- runtime dependency initialization needed for execution correctness
- runtime state publication needed for readiness and operability

### 4. Runtime Health and Readiness Contracts
- `/health`
- `/ready`
- `/api/version`
- degraded-mode signaling that reflects execution reality
- runtime-owned conditions that affect safe execution

### 5. Runtime Deployment Contract
- deployment-profile semantics
- required runtime dependencies for supported profiles
- runtime package/install/boot expectations
- runtime compatibility guarantees that external consumers depend on

### 6. Persistence Required For Runtime Correctness
- storage directly required for execution, resume, readiness, idempotency, or runtime recovery
- persistence that is part of execution truth, not business convenience

### 7. Isolation and Trust Enforcement
- tenant boundaries in execution paths
- extension capability boundaries
- trust-model enforcement that directly affects runtime safety claims

---

## Runtime Does Not Own

These concerns should not expand inside `aindy-runtime` unless there is a strong execution-substrate reason.

### 1. SDK Ergonomics
The runtime should not own:
- convenience wrappers for clients
- developer-friendly helper APIs for consumers
- typed consumer abstractions that exist to improve adoption rather than runtime correctness
- multi-language client ergonomics

Those belong in `aindy-sdk`.

### 2. UI Concerns
The runtime should not own:
- presentation-layer behavior
- component systems
- frontend state conventions
- visual health/status UX
- design-system behavior

Those belong in `aindy-ui-kit` and UI-consuming applications.

### 3. Product-Level Workflow UX
The runtime should not own:
- product-facing workflow authoring UX
- app/business process convenience surfaces
- user-facing orchestration editing abstractions unless they are required runtime contracts

### 4. Backend Convenience Surfaces That Are Not Runtime-Critical
The runtime should avoid owning:
- routes that exist mainly as app convenience wrappers
- business-domain aggregation APIs that do not define runtime contracts
- product-specific orchestration helpers that can live above the runtime

### 5. General Platform Gravity
The runtime should not become the default home for anything that is:
- shared
- useful
- backend-related

“Backend-related” is not the same thing as “runtime-owned.”

---

## `aindy-sdk` Owns

`aindy-sdk` should be the consumer-facing access layer for stable runtime capabilities.

It should own:

- typed client wrappers for stable runtime endpoints
- developer-facing abstractions over runtime contracts
- authentication/session/client ergonomics
- request/response helpers for stable public runtime APIs
- integration helpers that improve consumer adoption without changing runtime guarantees
- compatibility wrappers that absorb runtime evolution where the contract allows it

It should not own:

- execution truth
- runtime scheduling semantics
- authoritative health/readiness semantics
- tenant/capability enforcement rules
- behavior that redefines runtime guarantees instead of consuming them

---

## `aindy-ui-kit` Owns

`aindy-ui-kit` should be the presentation and interaction layer.

It should own:

- reusable UI components
- view-layer conventions
- runtime-facing visual states built on stable contracts
- health/readiness/status presentation
- operator/admin UI affordances that consume runtime APIs

It should not own:

- runtime execution semantics
- backend enforcement logic
- hidden dependence on unstable runtime internals
- coupling to runtime bootstrap details beyond documented API or status contracts

---

## Borderline Areas

These areas require active boundary discipline.

### 1. Agents
Agents may touch execution, orchestration, tools, and UI.

Runtime ownership is justified only for:
- execution-critical agent runtime behavior
- agent invocation semantics that are part of the execution substrate
- tenant/capability enforcement for agent execution

Agent authoring UX, consumer wrappers, and presentation patterns belong elsewhere.

### 2. Memory
Runtime ownership is justified only where memory behavior affects:
- execution correctness
- tenant safety
- resume/recovery semantics
- stable runtime contracts

Higher-level memory product behavior, authoring ergonomics, or app-specific memory experiences should not default into the runtime.

### 3. Platform Registry and Loader Behavior
Registry/loader behavior belongs in runtime when it is needed for:
- runtime boot correctness
- stable execution discovery
- readiness or deployment correctness

Registry behavior that mainly exists to support app-layer convenience should be treated as suspect runtime scope.

### 4. Route Surfaces
A route living in the runtime repo does not automatically mean it is runtime-owned.

A route is runtime-owned only if it is one of:
- execution contract
- runtime metadata contract
- runtime health/readiness contract
- runtime deployment/admin contract required for operating the runtime

All other routes should be reviewed as potential extraction or contraction candidates.

---

## Extraction Candidates

These are categories that should be reviewed first when reducing runtime scope.

### Candidate Type A: App/Business Convenience Routes
Questions to ask:
- Does this route define a runtime guarantee?
- Is it required for execution correctness or operation?
- Could the SDK or a higher application layer consume lower-level runtime primitives instead?

If not runtime-critical, it is a likely extraction candidate.

### Candidate Type B: Consumer Ergonomic Adapters
Questions to ask:
- Is this logic primarily making runtime easier to call?
- Is it translating runtime contracts into nicer client shapes?
- Does it help users more than it helps runtime correctness?

If yes, it likely belongs in `aindy-sdk`.

### Candidate Type C: Presentation-Oriented Health/Status Behavior
Questions to ask:
- Is this behavior needed by operators as a runtime contract?
- Or is it mainly a display/presentation concern?

If mainly presentation, it likely belongs in UI or SDK layers.

### Candidate Type D: Legacy Monolith Residue
Questions to ask:
- Is this here because it was extracted with the runtime?
- Or because it is genuinely part of the execution substrate?

Past co-location is not a valid long-term boundary reason.

---

## Boundary Decision Test

When deciding whether code belongs in `aindy-runtime`, ask these questions in order:

1. Does this directly affect execution correctness, startup correctness, tenant/capability enforcement, readiness truth, or runtime deployment contract?
2. If removed from the runtime repo, would the runtime lose a core guarantee rather than a convenience?
3. Is this a stable runtime-facing contract or an app/client/UI convenience layer?
4. Would keeping this in runtime make the runtime more trustworthy, or just more central?
5. Is the main consumer of this code the runtime itself, or downstream humans and applications?

If the answer trends toward convenience, presentation, or consumer ergonomics, it probably does not belong in the runtime.

---

## Allowed Runtime Expansion

New scope may be added to `aindy-runtime` when it does one or more of the following:

- strengthens execution guarantees
- reduces ambiguity in runtime ownership
- removes duplicated runtime-critical enforcement from other repos
- improves runtime operability or correctness
- formalizes a stable runtime contract external consumers need

New scope should be resisted when it mainly:

- improves app convenience
- improves client ergonomics
- improves UI composition
- centralizes ownership for convenience rather than correctness
- adds product logic without strengthening runtime guarantees

---

## Boundary Smells

These are warning signs that `aindy-runtime` is accumulating the wrong responsibilities.

- A route exists because “the backend needed somewhere to put it.”
- A module mainly helps client or UI developers but lives in the runtime.
- Runtime internals are treated as a supported SDK surface without contract discipline.
- UI and SDK release cadence is blocked by runtime-local convenience decisions.
- Product logic is justified as “platform” without being execution-critical.
- Health and readiness become presentation-heavy instead of operationally truthful.

---

## What Maturity Looks Like

A mature `aindy-runtime` should be:

- narrower than the current repo surface
- stronger in execution and readiness guarantees
- less attractive as a dumping ground for shared backend concerns
- easier to version against `aindy-sdk` and `aindy-ui-kit`
- more explicit about what is contract and what is implementation

The runtime should increasingly look like a substrate, not a general platform monolith.

---

## Review Checklist

Use this when reviewing a new module, route, service, or refactor.

- [ ] Is this execution-substrate-critical?
- [ ] Is this required for runtime startup, recovery, readiness, or enforcement?
- [ ] Is this defining a stable runtime contract?
- [ ] Could this live in `aindy-sdk` instead?
- [ ] Could this live in `aindy-ui-kit` instead?
- [ ] Is this legacy spillover rather than intentional runtime ownership?
- [ ] Does keeping this in runtime make the runtime stronger, or just larger?

---

## Future Companion Docs

This document should eventually align with:

- `EXECUTION_INVARIANTS.md`
- `SECURITY_POSTURE.md`
- `CROSS_REPO_COMPATIBILITY.md`
- `DEGRADED_MODE_MATRIX.md`

These documents answer different questions:

- `RUNTIME_BOUNDARY.md`: what the runtime owns
- `EXECUTION_INVARIANTS.md`: what runtime behavior must not drift
- `SECURITY_POSTURE.md`: what trust and isolation claims are actually true
- `CROSS_REPO_COMPATIBILITY.md`: what SDK/UI/runtime consumers may rely on
- `DEGRADED_MODE_MATRIX.md`: what remains safe under partial failure

