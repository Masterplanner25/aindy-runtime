"""
config.py - Centralized environment configuration for A.I.N.D.Y.

All runtime settings are sourced from process environment variables.
"""

import logging
import os
from datetime import datetime, timezone
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator, model_validator
from pathlib import Path

utcnow = lambda: datetime.now(timezone.utc)
logger = logging.getLogger(__name__)

# Resolve the .env file location. Operators (notably containerised deployments)
# can override the default AINDY/.env path with AINDY_ENV_FILE to mount config
# at a stable, version-independent path (e.g. /etc/aindy/.env).
#
# This must be read from the raw environment here because
# SettingsConfigDict(env_file=...) is evaluated at class-definition time,
# before any Settings instance exists — self.AINDY_ENV_FILE is not yet
# available when model_config is being constructed.
_DEFAULT_ENV_FILE = Path(__file__).parent / ".env"
_ENV_FILE: str = os.getenv("AINDY_ENV_FILE") or str(_DEFAULT_ENV_FILE)


def _resolve_env_file() -> str:
    """
    Return the .env file path the Settings class will read.

    Precedence:
    1. ``AINDY_ENV_FILE`` environment variable (explicit operator override).
    2. ``AINDY/.env`` relative to this file (package default).

    Empty-string values are treated as unset and fall through to the default,
    so ``export AINDY_ENV_FILE=`` clears the override rather than attempting
    to open an empty-path file. Consistent with ``resolve_event_bus_redis_url``.

    Extracted as a function so the resolution logic is unit-testable
    without reload gymnastics.
    """
    return os.getenv("AINDY_ENV_FILE") or str(Path(__file__).parent / ".env")

def _read_version() -> str:
    import json, pathlib
    _vf = pathlib.Path(__file__).parent / "version.json"
    try:
        return json.loads(_vf.read_text(encoding="utf-8"))["version"]
    except Exception:
        return "1.0.0"


# --------------------------------------------------------------------
# Base Settings
# --------------------------------------------------------------------
class Settings(BaseSettings):
    # --- Core runtime variables ---
    ENV: str = "development"
    TESTING: bool = False
    TEST_MODE: bool = False
    DATABASE_URL: str = ""
    MONGO_URL: str | None = None
    PERMISSION_SECRET: str = ""  # Deprecated — HMAC removed; kept for backward compat
    OPENAI_API_KEY: str = ""
    DEEPSEEK_API_KEY: str | None = None
    # DeepSeek is OpenAI-API-compatible but on its own host. Without this the
    # OpenAI SDK defaults to api.openai.com, so DeepSeek calls hit the wrong
    # endpoint (surfaced by the AGENT-HARDEN-7 contract test).
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    # AGENT-HARDEN-5: cross-provider LLM fallback. LLM_PROVIDER is the primary;
    # get_llm_client_chain() fails over to each provider named in
    # LLM_FALLBACK_PROVIDERS (comma-separated, in order) on a breaker-open / call
    # failure. Empty LLM_FALLBACK_PROVIDERS = single-provider behavior (unchanged).
    LLM_PROVIDER: str = "openai"
    LLM_FALLBACK_PROVIDERS: str = ""
    OPENAI_CHAT_TIMEOUT_SECONDS: float = 30.0
    OPENAI_EMBEDDING_TIMEOUT_SECONDS: float = 15.0
    OPENAI_MAX_RETRIES: int = 3
    OPENAI_RETRY_BACKOFF_BASE_SECONDS: float = 1.0
    AINDY_AGENT_PLANNER_BACKEND: str = "runtime_local"
    AINDY_AGENT_PLANNER_MODEL: str = "gpt-4o"
    AINDY_AGENT_PLANNER_TEMPERATURE: float = 0.3
    # RTR-1 Phase 2e: when true (and AINDY_AGENT_EXECUTION_BACKEND=nodus_vm), the
    # planner inserts a human-approval WAIT step before the first high-risk step,
    # so the run pauses mid-plan for approval before doing something risky. Default
    # off — no behavior change. Ignored on the AGENT_FLOW backend (which has no wait).
    AINDY_AGENT_WAIT_BEFORE_HIGH_RISK: bool = False
    AINDY_EVENT_HANDLER_TIMEOUT_SECONDS: float = 5.0
    AINDY_PLUGIN_SANDBOX_RUNNER: str = "auto"
    AINDY_PLUGIN_CONTAINER_RUNTIME: str = "docker"
    AINDY_PLUGIN_CONTAINER_IMAGE: str = ""
    AINDY_PLUGIN_CONTAINER_IMAGE_DIGEST: str = ""
    AINDY_PLUGIN_CONTAINER_RUNTIME_SOURCE: str = ""
    AINDY_PLUGIN_CONTAINER_RUNTIME_TRUST_ISSUER: str = ""
    AINDY_PLUGIN_CONTAINER_RUNTIME_SIGNING_STATUS: str = "unverified"
    AINDY_PLUGIN_CONTAINER_RUNTIME_BASE_COMPATIBILITY: str = ""
    AINDY_PLUGIN_CONTAINER_REQUIRED_BASE_COMPATIBILITY: str = ""
    AINDY_PLUGIN_CONTAINER_TRUSTED_SOURCES: str = ""
    AINDY_PLUGIN_CONTAINER_TRUSTED_ISSUERS: str = ""
    AINDY_PLUGIN_CONTAINER_REQUIRE_SIGNATURE_VERIFICATION: bool = False
    AINDY_PLUGIN_CONTAINER_NO_NEW_PRIVILEGES: bool = True
    AINDY_PLUGIN_CONTAINER_DROP_ALL_CAPABILITIES: bool = True
    AINDY_PLUGIN_CONTAINER_DISABLE_NETWORK: bool = True
    AINDY_PLUGIN_CONTAINER_READ_ONLY_ROOTFS: bool = True
    AINDY_PLUGIN_CONTAINER_PIDS_LIMIT: int = 64
    AINDY_PLUGIN_CONTAINER_MEMORY_LIMIT: str = "256m"
    AINDY_PLUGIN_CONTAINER_CPU_LIMIT: float = 1.0
    AINDY_PLUGIN_CONTAINER_CPU_SHARES: int = 256
    AINDY_PLUGIN_CONTAINER_SECCOMP_PROFILE: str = ""
    AINDY_PLUGIN_CONTAINER_APPARMOR_PROFILE: str = ""
    AINDY_PLUGIN_CONTAINER_SELINUX_LABEL: str = ""
    AINDY_PLUGIN_CONTAINER_WRITABLE_TMP: bool = True
    AINDY_PLUGIN_CONTAINER_TMPFS_SIZE: str = "64m"
    AINDY_PLUGIN_CONTAINER_PLUGIN_MOUNT_PATH: str = "/plugin-root"
    AINDY_PLUGIN_CONTAINER_WORKDIR: str = "/tmp"
    AINDY_PLUGIN_STRONG_SANDBOX_LAUNCHER: str = "aindy-sandbox-vm"
    AINDY_PLUGIN_STRONG_SANDBOX_IMAGE: str = ""
    AINDY_PLUGIN_STRONG_SANDBOX_IMAGE_DIGEST: str = ""
    AINDY_PLUGIN_STRONG_SANDBOX_RUNTIME_SOURCE: str = ""
    AINDY_PLUGIN_STRONG_SANDBOX_RUNTIME_TRUST_ISSUER: str = ""
    AINDY_PLUGIN_STRONG_SANDBOX_RUNTIME_SIGNING_STATUS: str = "unverified"
    AINDY_PLUGIN_STRONG_SANDBOX_RUNTIME_BASE_COMPATIBILITY: str = ""
    AINDY_PLUGIN_STRONG_SANDBOX_REQUIRED_BASE_COMPATIBILITY: str = ""
    AINDY_PLUGIN_STRONG_SANDBOX_TRUSTED_SOURCES: str = ""
    AINDY_PLUGIN_STRONG_SANDBOX_TRUSTED_ISSUERS: str = ""
    AINDY_PLUGIN_STRONG_SANDBOX_REQUIRE_SIGNATURE_VERIFICATION: bool = False
    AINDY_PLUGIN_STRONG_SANDBOX_MEMORY_LIMIT: str = "512m"
    AINDY_PLUGIN_STRONG_SANDBOX_CPU_LIMIT: float = 1.0
    AINDY_PLUGIN_STRONG_SANDBOX_PIDS_LIMIT: int = 64
    AINDY_PLUGIN_STRONG_SANDBOX_PLUGIN_MOUNT_PATH: str = "/plugin-root"
    AINDY_PLUGIN_STRONG_SANDBOX_WORKDIR: str = "/work"
    FLOW_WAIT_TIMEOUT_MINUTES: int = 30
    STUCK_RUN_THRESHOLD_MINUTES: int = 45
    AINDY_WATCHDOG_INTERVAL_MINUTES: int = 2

    # --- Auth ---
    # SECRET_KEY rotation:
    # 1. Generate: python -c "import secrets; print(secrets.token_hex(32))"
    # 2. Set in .env. All active JWTs will be invalidated on next restart.
    # 3. Do not reuse old keys. Minimum 32 characters required in non-dev environments.
    SECRET_KEY: str = "dev-secret-change-in-production"
    AINDY_API_KEY: str | None = None
    AINDY_SERVICE_KEY: str | None = None
    # Admin bootstrap: if set, the user with this email is elevated to is_admin=True
    # on every boot (grant-only — unsetting this var never revokes admin).
    # Flow: register via POST /auth/register first, then set this var and restart.
    # The same effect is available post-deploy via: aindy-runtime auth promote-admin <email>
    AINDY_BOOTSTRAP_ADMIN_EMAIL: str | None = None
    STRIPE_SECRET_KEY: str | None = None
    STRIPE_WEBHOOK_SECRET: str | None = None

    @field_validator("SECRET_KEY")
    @classmethod
    def reject_insecure_secret_key(cls, v: str) -> str:
        env_name = os.getenv("ENV", "development").lower()
        is_test = env_name == "test" or os.getenv(
            "TEST_MODE", "0"
        ).lower() in {"1", "true", "yes"}
        is_dev = env_name in {"dev", "development"}
        if not is_test:
            _BAD = {"secret", "changeme", "your-secret-key", "REPLACE_THIS"}
            if v.startswith("REPLACE_THIS") or v in _BAD:
                raise ValueError(
                    "SECRET_KEY is set to an insecure placeholder. "
                    "Generate a real key with: "
                    'python3 -c "import secrets; print(secrets.token_hex(32))"'
                )
        if v == "test-secret-key" and not (is_test or is_dev):
            raise ValueError(
                "SECRET_KEY must not use the known weak test value outside test/development."
            )
        if len(v) < 32 and not (is_test or is_dev):
            raise ValueError(
                "SECRET_KEY must be at least 32 characters outside test/development."
            )
        return v

    @field_validator("OPENAI_API_KEY")
    @classmethod
    def reject_placeholder_openai_api_key(cls, v: str) -> str:
        env_name = os.getenv("ENV", "development").lower()
        is_test = env_name == "test" or os.getenv(
            "TEST_MODE", "0"
        ).lower() in {"1", "true", "yes"} or os.getenv(
            "TESTING", "0"
        ).lower() in {"1", "true", "yes"}
        if is_test:
            return v
        normalized = (v or "").strip()
        bad_values = {"your-key-here", "sk-placeholder", "changeme", "replace_me"}
        if not normalized or normalized.lower() in bad_values:
            if env_name == "production":
                raise ValueError("OPENAI_API_KEY is not set or is a placeholder")
            logger.warning("OPENAI_API_KEY is not set or is a placeholder; OpenAI features may be unavailable")
        return v

    @field_validator("ENFORCE_EXECUTION_CONTRACT")
    @classmethod
    def default_contract_enforcement_for_tests(cls, v: bool) -> bool:
        return bool(v)

    VERSION: str = Field(default_factory=_read_version, exclude=True)
    API_VERSION: str = Field(
        default="1.0.0",
        description=(
            "Semantic version of the API contract. Increment MAJOR on breaking changes, "
            "MINOR on additive changes, PATCH on bug fixes. "
            "Frontend and SDK must declare a compatible minimum version."
        ),
    )
    API_MIN_CLIENT_VERSION: str = Field(
        default="1.0.0",
        description=(
            "Minimum client version this API supports. Clients declaring a version "
            "below this will receive a version-mismatch warning in response headers."
        ),
    )

    # --- Optional runtime options ---
    # Logging configuration (read directly via os.getenv - not in Settings
    # to avoid circular import with log setup which runs before settings load):
    #   LOG_FORMAT=json   - force JSON output (default in production)
    #   LOG_FORMAT=text   - force plain text (default in development)
    #   LOG_LEVEL=INFO    - root log level (DEBUG, INFO, WARNING, ERROR)
    # Worker process health probe port (read via os.getenv in worker entry points):
    #   WORKER_HEALTH_PORT=8001  - async job worker
    #   WORKER_HEALTH_PORT=8002  - memory ingest worker
    #   WORKER_HEALTH_PORT=8003  - metric writer worker
    LOG_LEVEL: str = "INFO"
    AINDY_ENV_FILE: str | None = None
    # Override the .env file location. Default: AINDY/.env relative to the
    # package. Set to a stable path (e.g. /etc/aindy/.env) for containerised
    # deployments where the bind-mount target must not change across versions.
    # Note: the actual env_file used by Settings is resolved at class-definition
    # time via _ENV_FILE / _resolve_env_file() above, not from this field.
    # This field exists for introspection and documentation only.
    REDIS_URL: str | None = None
    AINDY_REQUIRE_REDIS: bool = False
    AINDY_CACHE_BACKEND: str = "redis"

    # --- Database connection pool defaults (non-SQLite only) ---
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT: int = 30       # seconds to wait for a connection
    DB_POOL_RECYCLE: int = 1800     # recycle connections older than 30 min
    DB_STATEMENT_TIMEOUT_MS: int = Field(
        default=30000,
        description=(
            "PostgreSQL statement_timeout in milliseconds. Applied to all "
            "connections in non-test environments. Set to 0 to disable. "
            "Default: 30000 (30 seconds). Test environments use 10000 (10s)."
        ),
    )
    DB_IDLE_IN_TRANSACTION_TIMEOUT_MS: int = Field(
        default=30000,
        description=(
            "PostgreSQL idle_in_transaction_session_timeout in milliseconds. "
            "Closes sessions that hold a transaction open without issuing "
            "queries. Default: 30000 (30 seconds)."
        ),
    )

    # --- Execution transport ---
    # "thread"      — ThreadPoolExecutor (default; single-process only).
    # "distributed" — DistributedQueue via Redis (multi-process / multi-host).
    EXECUTION_MODE: str = "thread"
    AINDY_QUEUE_NAME: str = "aindy:jobs"
    AINDY_ASYNC_JOB_WORKERS: int = 10
    AINDY_ASYNC_QUEUE_MAXSIZE: int = 100    # max pending jobs before rejection
    AINDY_MEMORY_INGEST_QUEUE_MAX: int = 500
    AINDY_SHUTDOWN_TIMEOUT_SECONDS: int = 30
    AINDY_WORKER_HEALTH_PORT: int = 8001
    AINDY_WORKER_LIVENESS_TIMEOUT_SECONDS: int = 60
    AINDY_JOB_WARN_CAPACITY: bool = True
    MAX_QUEUE_SIZE: int = Field(
        default_factory=lambda: int(
            os.getenv("MAX_QUEUE_SIZE", os.getenv("AINDY_ASYNC_QUEUE_MAXSIZE", "100"))
        )
    )
    AINDY_QUEUE_SATURATION_THRESHOLD: int = Field(
        default_factory=lambda: int(
            os.getenv(
                "AINDY_QUEUE_SATURATION_THRESHOLD",
                os.getenv("MAX_QUEUE_SIZE", os.getenv("AINDY_ASYNC_QUEUE_MAXSIZE", "100")),
            )
        )
    )
    AINDY_ASYNC_MAX_CONCURRENT_GLOBAL: int = 0
    AINDY_ASYNC_MAX_CONCURRENT_PER_USER: int = 0
    USE_NATIVE_SCORER: bool = True
    ENFORCE_EXECUTION_CONTRACT: bool = True
    # INFINITY-RUNTIME-1 Gap 1: inject recalled memory into the agent planner
    # prompt. Off by default — flip after app-side soak (mirrors the nodus_vm
    # opt-in discipline) so planner prompts/plan quality don't shift silently.
    AINDY_PLANNER_MEMORY_INJECTION: bool = False
    # INFINITY-RUNTIME-1 Gap 5: async jobs join the Infinity loop. Off by default
    # so infra jobs (embedding ingestion, metric writing) don't start producing
    # memory nodes + score events until an operator opts in after soak. When on,
    # _execute_job_inline activates the async-execution context (so EXECUTION_*
    # events persist and auto-capture writes memory) and emits SCORE_COMPUTED.
    AINDY_ASYNC_JOB_LOOP_CLOSURE: bool = False
    # RTR-4 Gap (a): require an inter-agent accept/reject handshake before a
    # delegated child run executes. Off by default — current behavior dispatches
    # the child straight to `approved`. When on, the child is held at
    # `awaiting_delegation` until the delegate calls `respond_to_delegation`.
    AINDY_DELEGATION_HANDSHAKE: bool = False
    # RTR-5: runtime-driven autonomous trigger→plan→execute window. Off by default
    # — when off, an autonomous "execute" decision is only evaluated/queued (current
    # behavior). When on, `run_execute_window` actually composes create_run →
    # execute_run in a bounded loop. Opt-in after soak (mirrors nodus_vm discipline).
    AINDY_AUTONOMOUS_EXECUTE_WINDOW: bool = False
    # Max create→plan→execute iterations per window (bound on autonomous action).
    AINDY_AUTONOMOUS_MAX_ITERATIONS: int = 3
    # Max concurrent active runs (flow+agent) before the window declines to start
    # another iteration — admission cap via count_active_executions.
    AINDY_AUTONOMOUS_MAX_ACTIVE_RUNS: int = 1
    # Cooldown between window iterations, seconds (0 = none; capped at 30).
    AINDY_AUTONOMOUS_COOLDOWN_SECONDS: int = 0
    # INFINITY-RUNTIME-1 Deliverable C: act on a post-execution NextAction. Off by
    # default — when off, a `trigger_execution` decision is only recorded
    # (NEXT_ACTION_CHOSEN, current behavior). When on, an app-sourced
    # `trigger_execution` with an objective dispatches ONE bounded follow-up run
    # (create_run→execute_run), reusing the admission + approval rails. The runtime
    # never acts on its own runtime-default decision. Opt-in after soak.
    AINDY_NEXT_ACTION_ACTING: bool = False
    # Max NextAction follow-up chain depth (net-new rail): a run reached via N
    # trigger_execution hops from a root will not dispatch another once N == this
    # cap — prevents a hook that always returns trigger_execution from self-
    # perpetuating. The window's max-iterations bounds one window, not a chain.
    AINDY_NEXT_ACTION_MAX_CHAIN: int = 3
    # Max concurrent active runs (flow+agent) before a NextAction follow-up is
    # declined — admission cap via count_active_executions (0 disables the cap).
    AINDY_NEXT_ACTION_MAX_ACTIVE: int = 1
    # ECOGAP-1 Phase 1: transparent crash continuation of non-waiting flows. Off by
    # default — when off, a stuck running/executing FlowRun is failed on restart
    # (current behavior). When on, a flow whose name is registered
    # continuation-safe is re-driven from its last-committed node via
    # PersistentFlowRunner.resume() instead of failed. Opt-in after soak.
    AINDY_DURABLE_CONTINUATION: bool = False
    # DUR-3: default-safe continuation. When on (and AINDY_DURABLE_CONTINUATION is on),
    # continuation applies to ALL flows/agents — the per-flow/per-agent continuation-safe
    # DECLARATION is no longer required, because DUR-1/2/2b/2c make a re-run's runtime-
    # mediated effects (memory, syscalls, tools) at-most-once. A flow/agent with raw
    # un-mediated side effects must be excluded via mark_flow_continuation_unsafe /
    # mark_agent_type_continuation_unsafe. Off by default (declaration still required).
    AINDY_DURABLE_CONTINUATION_ALL: bool = False
    # Max continuation attempts before a crash-looping flow is dead-lettered.
    AINDY_DURABLE_CONTINUATION_MAX_ATTEMPTS: int = 3
    # ECOGAP-1 Phase 2a: run each agent tool step as its own segment so crash
    # continuation (Phase 2) resumes at STEP granularity — a crash re-runs only the
    # in-flight step, not the whole segment, and completed steps are skipped. Off by
    # default: it trades one subprocess VM run per step for finer-grained durability.
    AINDY_DURABLE_STEP_GRANULARITY: bool = False
    SKIP_MONGO_PING: bool = False
    MONGO_REQUIRED: bool = False
    MONGO_HEALTH_TIMEOUT_MS: int = 5000
    MONGO_CONNECT_TIMEOUT_MS: int = 3000
    MONGO_SOCKET_TIMEOUT_MS: int = 5000
    MONGO_SERVER_SELECTION_TIMEOUT_MS: int = 3000
    MONGO_MAX_POOL_SIZE: int = 10
    MONGO_MIN_POOL_SIZE: int = 1

    # --- Environment loading config ---
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Validators ---
    @field_validator("DATABASE_URL")
    @classmethod
    def ensure_postgres(cls, v: str) -> str:
        if not v:
            return v
        allow_sqlite = os.getenv("AINDY_ALLOW_SQLITE", "0").lower() in {
            "1",
            "true",
            "yes",
        }
        if os.getenv("TEST_MODE", "0").lower() in {"1", "true", "yes"}:
            allow_sqlite = True
        if allow_sqlite:
            return v
        if not v.startswith("postgres"):
            raise ValueError("DATABASE_URL must be a valid PostgreSQL URI")
        return v

    @field_validator("MONGO_URL")
    @classmethod
    def ensure_mongo_url(cls, v: str) -> str:
        normalized = (v or "").strip()
        skip_ping = os.getenv("SKIP_MONGO_PING", "0").lower() in {"1", "true", "yes"}
        mongo_required = os.getenv("MONGO_REQUIRED", "0").lower() in {"1", "true", "yes"}
        if not normalized:
            if skip_ping or not mongo_required:
                return ""
            raise ValueError("MONGO_URL is required when MONGO_REQUIRED=true")
        return normalized

    @model_validator(mode="after")
    def validate_stuck_run_threshold(self) -> "Settings":
        if self.STUCK_RUN_THRESHOLD_MINUTES <= self.FLOW_WAIT_TIMEOUT_MINUTES:
            raise ValueError(
                f"STUCK_RUN_THRESHOLD_MINUTES ({self.STUCK_RUN_THRESHOLD_MINUTES}) "
                f"must be greater than FLOW_WAIT_TIMEOUT_MINUTES "
                f"({self.FLOW_WAIT_TIMEOUT_MINUTES}). "
                "Legitimately waiting flows would be incorrectly recovered."
            )
        return self

    # --- Helper properties ---
    @property
    def is_dev(self) -> bool:
        return self.ENV.lower() in ("dev", "development")

    @property
    def is_prod(self) -> bool:
        return self.ENV.lower() in ("prod", "production")

    @property
    def is_testing(self) -> bool:
        return self.TESTING or self.TEST_MODE or self.ENV.lower() == "test"

    @property
    def requires_redis(self) -> bool:
        """True when the deployment mode requires Redis (non-dev, non-test, or explicit flag)."""
        return self.AINDY_REQUIRE_REDIS or self.ENV.lower() not in ("dev", "development", "test")


# --------------------------------------------------------------------
# Initialize Global Settings
# --------------------------------------------------------------------
settings = Settings()


def resolve_execution_mode() -> str:
    """Effective async-execution transport — ``"thread"`` or ``"distributed"``.

    RTR-2: an explicit ``EXECUTION_MODE`` env var always wins. When it is unset,
    **production defaults to ``"distributed"``** (durable) so a prod deploy that
    forgets to set it fails fast at queue init (``get_queue`` raises without
    ``REDIS_URL``) instead of silently running thread-mode jobs that are lost on
    restart. Dev/test stay on ``"thread"``. Reads ``os.getenv`` directly (as the
    prior call sites did) so the value must be a real environment variable, not
    ``.env``-only.
    """
    raw = os.getenv("EXECUTION_MODE")
    if raw:
        return raw.strip().lower()
    return "distributed" if settings.is_prod else "thread"

# --------------------------------------------------------------------
# Logging Initialization
# --------------------------------------------------------------------
log_path = Path("logs")
try:
    log_path.mkdir(exist_ok=True)
except PermissionError:
    # Subprocess workers (runtime_callback_worker) run from site-packages where
    # the cwd is not writable. FileHandler is guarded with OSError below.
    pass

def _build_log_handler(use_file: bool, log_file: Path) -> list[logging.Handler]:
    handlers: list[logging.Handler] = []
    if use_file:
        try:
            handlers.append(logging.FileHandler(log_file))
        except OSError:
            pass
    handlers.append(logging.StreamHandler())
    return handlers

_log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
_handlers = _build_log_handler(
    use_file=True,
    log_file=log_path / f"aindy_{settings.ENV}.log",
)

if settings.is_prod:
    # Structured JSON — one JSON object per line
    from pythonjsonlogger import jsonlogger  # noqa: PLC0415
    _fmt = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    for _h in _handlers:
        _h.setFormatter(_fmt)
else:
    _plain_fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    for _h in _handlers:
        _h.setFormatter(_plain_fmt)

logging.basicConfig(level=_log_level, handlers=_handlers)
logging.getLogger(__name__).info(
    "Loaded %s environment from process environment variables", settings.ENV
)


