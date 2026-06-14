# OpenClaw — Infinite Weave

This example shows how [OpenClaw](https://openclaw.ai)'s personal AI assistant pattern
maps to **aindy-runtime**, and what the Infinity Algorithm's execution layer adds
alongside OpenClaw's `pi-agent-core` embedded loop.

OpenClaw is a capable, model-agnostic personal assistant with a clean skill system,
multi-channel delivery, and a well-designed workspace model. This isn't a replacement
— it's a working integration showing how aindy-runtime's syscall layer, pgvector memory,
and scheduler slot in as a persistent backend complement to OpenClaw's frontend strengths.

## The Delta

| OpenClaw | Infinite Weave (aindy-runtime) |
|---|---|
| SOUL.md / IDENTITY.md injected from disk on every boot | Persisted as pgvector memory nodes; semantically retrievable, updatable without restart |
| JSONL session transcripts (`~/.openclaw/workspace/memory/`) | Every turn stored as a pgvector node — cross-session, scored, causally linked |
| Markdown skill files discovered at boot (`~/.openclaw/skills/`) | Python callables registered in aindy's tool registry; live-reloadable, versioned |
| `session-memory` hook saves context on `/new` | `sys.v1.memory.write` syscall — atomic, traced, idempotency-gated |
| Hooks system (`before_agent_start`, `after_tool_call`, …) | EventBus + SystemEvents — pub/sub across instances, Redis-backed |
| Cron tool for scheduling | `sys.v1.job.submit` → aindy SchedulerEngine (APScheduler, distributed) |
| Single-node `pi-agent-core` loop | AgentRun lifecycle (pending → approved → executing → completed), ExecutionUnit metrics, OTel tracing |
| Per-session context only | Cross-session memory scoring (impact score, usage count, causal depth) |
| No multi-agent | AgentCoordinator + multi-agent delegation via `sys.v1.agent.execute` |

## Files

| File | Description |
|---|---|
| `openclaw_agent.nd` | Nodus script — the agent turn: recall → route → persist → return |
| `openclaw_runner.py` | Python bootstrap + tool wiring + NodusRuntime entry point |
| `SOUL.md` | Sample soul / persona file (loaded as a memory node at bootstrap) |
| `IDENTITY.md` | Sample identity file (loaded as a memory node at bootstrap) |
| `AGENTS.md` | Sample agents / context file (loaded as a memory node at bootstrap) |

## How It Works

### 1. Bootstrap (one-time per user)

`openclaw_runner.py:bootstrap_workspace_memory()` reads your workspace files and
seeds them as high-significance memory nodes (significance=0.9):

```
SOUL.md      → node_type="soul",     tags=["soul","identity","persona","assistant","openclaw"]
IDENTITY.md  → node_type="identity", tags=["identity","persona","openclaw"]
AGENTS.md    → node_type="context",  tags=["context","workspace","agents","openclaw"]
```

**Live stack:** nodes are embedded and persisted in Postgres/pgvector at MAS path
`/memory/{user_id}/openclaw/{type}/bootstrap`. Retrievable across sessions, updateable
without server restart.

**Standalone mode:** nodes are loaded into an in-memory cache and served by
`recall_memory()` for the duration of the run — no pgvector required.

### 2. Skills → Tool Registry

Four OpenClaw skills are wired as aindy host functions:

| Function | OpenClaw equivalent | Production syscall |
|---|---|---|
| `recall_memory(query)` | JSONL transcript search | `sys.v1.memory.search` |
| `web_search(query)` | brave-search skill | HTTP → `sys.v1.memory.write` (cached) |
| `schedule_reminder(payload)` | cron tool / `/schedule` command | `sys.v1.job.submit` |
| `remember_turn(payload)` | session-memory hook | `sys.v1.memory.write` |

### 3. Agent Loop (`openclaw_agent.nd`)

The Nodus script runs one turn:

```
recall persona nodes from memory  →  build persona_text
recall past conversation turns    →  get history context
route message:
  search keyword   →  web_search()
  remind/schedule  →  schedule_reminder()
  default          →  acknowledge with history count
remember_turn()  →  persist this exchange
set_state(reply, persona_loaded, history_turns, turn_persisted)
print(reply)
```

## Running the Demo

### Standalone (no live aindy-runtime needed)

The bundled `SOUL.md` / `IDENTITY.md` / `AGENTS.md` are loaded automatically.

```bash
cd examples/openclaw

python openclaw_runner.py "hello, who are you?"
python openclaw_runner.py "search for the latest Python news"
python openclaw_runner.py "remind me to review the PR tomorrow"
```

Expected output (default branch):

```
[openclaw] workspace : /path/to/examples/openclaw
[openclaw] message   : 'hello, who are you?'

[bootstrap] SOUL.md -> standalone cache (585 chars)
[bootstrap] IDENTITY.md -> standalone cache (627 chars)
[bootstrap] AGENTS.md -> standalone cache (487 chars)
[runner] bootstrapped workspace memory: ['soul', 'identity', 'context']

[openclaw] reply:
I received your message: hello, who are you?

[openclaw] persona_loaded=True  history_turns=0  turn_persisted=True
```

### With a live aindy-runtime stack

Full pgvector memory persistence, real job scheduling, OTel tracing.

```bash
# 1. Start the stack (from repo root)
docker compose up -d

# 2. Run against your OpenClaw workspace (or omit to use the bundled samples)
export AINDY_OPENCLAW_WORKSPACE=~/.openclaw/workspace

# 3. Run — DATABASE_URL points at the compose stack
DATABASE_URL=postgresql://aindy:aindy@localhost:5432/aindy \
  python openclaw_runner.py "search for Python news"
```

The runner detects a live aindy-runtime and routes all four tool calls through the
syscall layer instead of the stubs.

## Extending This Example

The following are deferred but architecturally straightforward:

- **Channel delivery** — wire Telegram/Discord/Slack as extension syscalls so messages
  arrive via `sys.v1.event.emit` and replies are dispatched back through the channel layer.
- **Multi-agent coordination** — complex tasks delegate to specialist agents via
  `sys.v1.agent.execute`; results are aggregated by the coordinator script.
- **clawhub skill discovery** — map clawhub's skill registry to aindy's tool registry
  at bootstrap, keeping the skill surface in sync without manual wiring.
- **Memory compaction** — replace OpenClaw's compact-on-overflow with aindy's
  scheduled memory scoring job; high-significance nodes survive, low-significance
  ones decay.
