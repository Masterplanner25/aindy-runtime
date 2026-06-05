import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# ---------------------------------------------------------------------------
# Alembic config object
# ---------------------------------------------------------------------------
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# Runtime ORM metadata — import all models so metadata is fully populated.
# ---------------------------------------------------------------------------
# Must set DATABASE_URL before importing AINDY modules.
os.environ.setdefault("DATABASE_URL", "postgresql://localhost/aindy")
os.environ.setdefault("AINDY_ALLOW_SQLITE", "false")
os.environ.setdefault("OPENAI_API_KEY", "sk-alembic-placeholder")
os.environ.setdefault("DEEPSEEK_API_KEY", "ds-alembic-placeholder")
os.environ.setdefault("SECRET_KEY", "alembic-secret-key")
os.environ.setdefault("AINDY_API_KEY", "alembic-api-key")

from AINDY.db.database import Base  # noqa: E402
import AINDY.db.models  # noqa: E402 — registers all runtime ORM tables

# Stub for apps-layer tables referenced by runtime FKs (not tracked by this Alembic).
# Required so SQLAlchemy can resolve FK targets during autogenerate/check comparison.
from sqlalchemy import Column, Table  # noqa: E402
from sqlalchemy.dialects.postgresql import UUID as _UUID  # noqa: E402

if "memory_nodes" not in Base.metadata.tables:
    Table("memory_nodes", Base.metadata, Column("id", _UUID(as_uuid=True), primary_key=True))

target_metadata = Base.metadata

# ---------------------------------------------------------------------------
# Runtime-owned table names — used by include_object to exclude monolith
# app tables from runtime Alembic autogenerate.
# ---------------------------------------------------------------------------
_RUNTIME_TABLES = {
    "agent_capability_mappings",
    "agent_events",
    "agent_registry",
    "agent_runs",
    "agent_steps",
    "agent_trust_settings",
    "agents",
    "background_task_leases",
    "capabilities",
    "dynamic_flows",
    "dynamic_nodes",
    "effect_records",
    "event_edges",
    "event_outcomes",
    "execution_units",
    "flow_history",
    "flow_runs",
    "job_logs",
    "memory_metrics",
    "memory_node_history",
    "memory_trace_nodes",
    "memory_traces",
    "nodus_scheduled_jobs",
    "nodus_trace_events",
    "platform_api_keys",
    "request_metrics",
    "system_events",
    "system_health_logs",
    "system_state_snapshots",
    "user_identity",
    "users",
    "waiting_flow_runs",
    "webhook_subscriptions",
}


def include_object(obj, name, type_, reflected, compare_to):
    """Only track runtime-owned tables; ignore everything else."""
    if type_ == "table":
        return name in _RUNTIME_TABLES
    return True


def get_url() -> str:
    return os.environ["DATABASE_URL"]


def run_migrations_offline() -> None:
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="alembic_version_runtime",
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        {"sqlalchemy.url": get_url()},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table="alembic_version_runtime",
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
