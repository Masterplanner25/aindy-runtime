# Contributors

Maintainer: **Shawn Knight** — Masterplan Infinite Weave.

This file records contributions from other people that are present in this
repository. It exists because a public thank-you and an in-source attribution are
different things, and only the second survives a `grep` by someone reading the code.

`aindy-runtime` is published to PyPI. Anyone who installs the package gets the code;
this file travels with it so the credit does too.

If your work is here and you are not listed, that is an omission, not a position —
please say so and it will be corrected.

---

## Architecture

### Cherokee Schill — `AINDY/memory/bridge.py` (the Memory Bridge)

The Memory Bridge exists because of a conversation with Cherokee. The contribution is
architectural rather than code: the decision to treat memory as **continuity and
authorship** rather than as storage came from her framing, and the module has carried
the line `Architected with Solon Protocol Logic | Continuity > Content` in its header
since v0.1.

Cherokee's own work is the **Ethical AI Framework**
(<https://github.com/Ocherokee/ethical-ai-framework>) — described there as "a
transparent, non-weaponizable, consent-based ethical AI framework designed to enforce
autonomy and accountability", and licensed MIT with an Ethical Autonomy Addendum. Her
subject is consent; that is the idea this project took from her.

At the time, her profile also described a **Relational AI Access Key (RAAK)** protocol
for consent-based access by AI systems, under a **Horizon Accord** initiative. That
description is recorded in this project's notes rather than verified against her
repository, and is named here only because it is what was read at the time.

What is not claimed: none of Cherokee's work is the ancestor of this runtime's
capability system. `AINDY/agents/capability_service.py` is an independent design driven
by an internal requirement (RTR-4), and no code or protocol of hers is used in it. The
debt is to the question — who may act, on whose authority, for what purpose — which
reached this project through her work roughly a year before the runtime enforced
anything.

---

## Adding to this file

Record what was contributed, what was retained, and what was changed. Vague credit is
worse than none: it names a person without letting anyone verify what they did.

Attribution in this repository lives in three places, and all three should agree:

1. this file,
2. the module docstring of the code in question,
3. any original documentation retained alongside it.
