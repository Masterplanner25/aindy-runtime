---
title: "Authentication System Audit"
api_version: "1.0"
last_verified: "2026-06-06"
status: current
owner: "platform-team"
---
You are performing a STRICT AUTHENTICATION SYSTEM AUDIT.

This is NOT a design task.
This is NOT a recommendation task (yet).

Your job is to determine:

"What authentication system currently exists in this codebase,
where it lives, and how it is actually used."

---

## CONTEXT

This system (A.I.N.D.Y.) includes:

- FastAPI routes
- execution pipeline (execute_with_pipeline)
- async job system
- agent runtime
- platform APIs
- potential JWT-based authentication

There is concern that authentication logic may be:

- spread across layers
- incorrectly placed
- duplicated
- leaking into runtime or app logic

---

## OBJECTIVE

Identify the CURRENT STATE of authentication:

1. Where JWT/auth logic exists
2. How it is used
3. Whether it is correctly layered
4. Where violations exist

---

## SEARCH INSTRUCTIONS

Search the entire repo for:

- get_current_user
- Depends(...)
- jwt / JWT / token / decode / encode
- Authorization headers
- auth/
- security/
- user_id handling
- request.user or equivalents

---

## OUTPUT FORMAT

---

# 1. AUTH SYSTEM COMPONENTS

List ALL auth-related components:

- files (e.g., AINDY/auth/jwt.py)
- functions (e.g., get_current_user)
- middleware
- dependencies

---

# 2. AUTH FLOW (ACTUAL)

Describe the real flow:

Example:

Request
→ JWT decoded in X
→ current_user created
→ passed into route
→ passed into pipeline

Be precise and trace actual code paths.

---

# 3. LAYER CLASSIFICATION

For each auth component, classify:

- Runtime layer
- Platform layer
- App layer

---

# 4. VIOLATIONS (CRITICAL)

Identify ANY cases where:

- runtime code handles auth directly
- JWT decoding appears outside auth modules
- apps perform auth instead of platform
- multiple auth implementations exist

---

# 5. USER CONTEXT PROPAGATION

Determine:

- how user_id is passed into:
    - routes
    - pipeline
    - async jobs
    - memory system

Is it consistent?

---

# 6. DUPLICATION / FRAGMENTATION

Identify:

- duplicate auth logic
- multiple ways of getting user
- inconsistent token handling

---

# 7. FRONTEND CONNECTION (if present)

If frontend exists:

- how does it authenticate?
- where does it send tokens?
- is it aligned with backend expectations?

---

# 8. CURRENT STATE SUMMARY

Answer clearly:

- Is auth centralized? (YES / PARTIAL / NO)
- Is auth correctly placed? (YES / PARTIAL / NO)
- Is auth consistent across the system? (YES / PARTIAL / NO)

---

# 9. FINAL VERDICT

In one sentence:

"What is wrong (if anything) with the current auth system?"

---

## IMPORTANT

- Do NOT suggest improvements yet
- Do NOT redesign
- Only describe what EXISTS
- Be precise and reference real code locations
- Call out problems explicitly

This is a factual audit, not a proposal.
