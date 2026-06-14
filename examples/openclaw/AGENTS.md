# Agents

## Primary: Claw

Role: Personal AI assistant — research, scheduling, memory, task execution
Skills: recall_memory, web_search, schedule_reminder, remember_turn
Syscalls: sys.v1.memory.read, sys.v1.memory.write, sys.v1.job.submit

## Delegation

Complex tasks can be routed to specialist agents via `sys.v1.agent.execute`.
The aindy AgentCoordinator dispatches to the right agent and aggregates results.
Register specialist agents in the aindy platform at `/platform/registry`.
