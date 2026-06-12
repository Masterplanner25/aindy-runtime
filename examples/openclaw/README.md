# OpenClaw — Infinite Weave Port

This example ports the core [OpenClaw](https://openclaw.ai) personal AI assistant
pattern to **aindy-runtime**, showing what the Infinity Algorithm adds on top of
vanilla OpenClaw's `pi-agent-core` embedded loop.

## The Delta

| Vanilla OpenClaw | Infinite Weave (aindy-runtime) |
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

## How It Works

### 1. Bootstrap (one-time per user)

`openclaw_runner.py:bootstrap_workspace_memory()` reads your OpenClaw workspace files
and seeds them as high-significance memory nodes (significance=0.9):

```
SOUL.md      → node_type="soul",     tags=["soul","identity","persona","openclaw"]
IDENTITY.md  → node_type="identity", tags=["identity","persona","openclaw"]
AGENTS.md    → node_type="context",  tags=["context","workspace","openclaw"]
```

These nodes live in Postgres/pgvector at MAS path `/memory/{user_id}/openclaw/{type}/bootstrap`
and are recalled by the agent on every turn via `sys.v1.memory.read`.

### 2. Skills → Tool Registry

Four OpenClaw skills are wired as aindy host functions:

| Function | OpenClaw equivalent | Production syscall |
|---|---|---|
| `recall_memory(query)` | JSONL transcript search | `sys.v1.memory.search` |
| `web_search(query)` | brave-search skill | HTTP → `sys.v1.memory.write` (cached) |
| `schedule_reminder(payload)` | cron tool / `/schedule` command | `sys.v1.job.submit` |
| `remember_turn(payload)` | session-memory hook | `sys.v1.memory.write` |

### 3. Agent Loop (`openclaw_agent.nd`)

The Nodus script runs the single-turn loop:

```
recall persona nodes from memory
recall past conversation turns
route message → web_search | schedule_reminder | default
remember_turn → persist this exchange to pgvector
set_state("reply", ...)
```

## Running the Demo

Standalone (no live aindy-runtime — stubs return placeholder data):

```bash
cd examples/openclaw
python openclaw_runner.py "search for the latest Python news"
python openclaw_runner.py "remind me to review the PR tomorrow"
python openclaw_runner.py "hello, who are you?"
```

With a live aindy-runtime stack (full pgvector memory persistence):

```bash
# 1. Start the stack
docker compose up -d

# 2. Point at your OpenClaw workspace
export AINDY_OPENCLAW_WORKSPACE=~/.openclaw/workspace

# 3. Run
DATABASE_URL=postgresql://... python openclaw_runner.py "search for Python news"
```

## Full Sweep (post-spike)

The following are deferred but architecturally straightforward once the spike is validated:

- **Channel delivery** — wire Telegram/Discord/Slack as extension syscalls so messages
  arrive via `sys.v1.event.emit` and replies are dispatched back through the channel layer.
- **Multi-agent coordination** — complex tasks delegate to specialist agents via
  `sys.v1.agent.execute`; results are aggregated by the coordinator script.
- **clawhub skill discovery** — map clawhub's skill registry to aindy's tool registry
  at bootstrap, keeping the skill surface in sync without manual wiring.
- **Memory compaction** — replace OpenClaw's compact-on-overflow with aindy's
  scheduled memory scoring job; high-significance nodes survive, low-significance
  ones decay.
