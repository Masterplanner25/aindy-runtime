"""auth/api_key_auth.py — Platform API key scope constants."""
from __future__ import annotations


class Scopes:
    FLOW_READ       = "flow.read"
    FLOW_EXECUTE    = "flow.execute"
    MEMORY_READ     = "memory.read"
    MEMORY_WRITE    = "memory.write"
    AGENT_RUN       = "agent.run"
    EXECUTION_READ  = "execution.read"
    WEBHOOK_MANAGE  = "webhook.manage"
    PLATFORM_ADMIN  = "platform.admin"

    ALL: list[str] = [
        FLOW_READ,
        FLOW_EXECUTE,
        MEMORY_READ,
        MEMORY_WRITE,
        AGENT_RUN,
        EXECUTION_READ,
        WEBHOOK_MANAGE,
        PLATFORM_ADMIN,
    ]
