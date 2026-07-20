from __future__ import annotations

import argparse
import os
import sys
from typing import NoReturn

from AINDY._version import __version__


def __getattr__(name: str):
    """Lazy-load ``app`` so importing this module does not pull in the database layer.

    uvicorn resolves ``AINDY.runtime_only:app`` via getattr after import, so the
    attribute must be reachable from the module namespace — but it does not need to be
    defined at import time. Deferring the import of AINDY.main keeps ``--help``,
    ``--version``, and ``sandbox`` from triggering database engine creation.
    """
    if name == "app":
        from AINDY.main import app as _app
        globals()["app"] = _app
        return _app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _format_sandbox_summary(payload: dict) -> str:
    """Render sandbox payload as a concise human-readable summary (~20 lines)."""
    lines: list[str] = []
    w = lines.append

    posture = payload.get("plugin_sandbox_posture", {})
    platform_data = payload.get("plugin_sandbox_platform", {})
    sv = payload.get("sandbox_verification_posture", {})
    escape = payload.get("escape_test_posture", {})
    trusted = payload.get("trusted_python_execution", {})

    current = posture.get("current", {})
    req = posture.get("requirement_status", {})
    env = platform_data.get("current_environment", {})
    backend = platform_data.get("current_container_backend_detection", {})

    platform_label = env.get("label") or platform_data.get("current_platform", "unknown")
    highest_tier = env.get("highest_supported_assurance_class", "unknown")
    prod_safe = env.get("production_safe_third_party_plugin_execution", False)
    reqs_met = req.get("assurance_class_satisfied", False) and req.get("certification_tier_satisfied", False)

    w(f"aindy-runtime sandbox  (v{__version__})")
    w("")
    w(f"Platform:             {platform_label}")
    w(f"Highest sandbox tier: {highest_tier}")
    w(f"Production-safe:      {'YES' if prod_safe else 'NO'}")
    w("")

    runtime_name = backend.get("runtime", "unknown")
    linux_backend = backend.get("linux_container_backend", False)
    backend_note = backend.get("operator_note") or backend.get("detection_error") or ""
    w("Container backend:")
    w(f"  Runtime:          {runtime_name}")
    w(f"  Linux containers: {'YES' if linux_backend else 'NO'}")
    if backend_note:
        w(f"  Note:             {backend_note}")
    w("")

    runner = current.get("runner_type", "unknown")
    assurance = current.get("assurance_class", "unknown")
    cert_tier = current.get("certification_tier", "unknown")
    cert_status = current.get("certification_status", "unknown")
    w(f"Active runner:        {runner}")
    w(f"Assurance class:      {assurance}")
    w(f"Certification tier:   {cert_tier}  [{cert_status}]")
    w(f"Requirements met:     {'YES' if reqs_met else 'NO'}")
    w("")

    # Sandbox verification (DB-layer: kernel-observable vs worker-self-report)
    sv_method = sv.get("verification_method", "skipped" if sv.get("skipped") else "unknown")
    sv_ceiling = sv.get("assurance_ceiling", "")
    w(f"Sandbox verification: {sv_method}" + (f"  [{sv_ceiling}]" if sv_ceiling else ""))

    # Escape test suite posture (from tests/sandbox/sandbox_escape_results.json)
    ep = escape.get("posture", "not_run")
    ep_last = escape.get("last_run", "")
    ep_host = escape.get("host_platform", "")
    ep_gaps = escape.get("gaps", [])
    ep_note = escape.get("operator_note", "")
    w(f"Escape test posture:  {ep}" + (f"  (last run: {ep_last}, platform: {ep_host})" if ep_last else ""))
    if ep_note and ep != "not_run":
        w(f"  note: {ep_note}")
    if ep_gaps:
        for gap in ep_gaps[:3]:
            w(f"  gap: {gap}")

    w("")
    trusted_count = trusted.get("total_count", 0)
    exec_model = trusted.get("execution_model", "")
    w(f"Trusted Python extensions: {trusted_count}  ({exec_model})" if exec_model else f"Trusted Python extensions: {trusted_count}")

    degraded = env.get("degraded_modes", [])
    if degraded:
        w("")
        w("Degraded modes:")
        for mode in degraded[:4]:
            w(f"  - {mode}")

    w("")
    w("For full machine-readable output: aindy-runtime sandbox --json")
    return "\n".join(lines)


def _run_sandbox_check(output_json: bool = False) -> NoReturn:
    """Print sandbox posture. Human-readable by default; --json for raw JSON output."""
    import json
    import sys as _sys
    from AINDY.platform_layer.deployment_contract import (
        get_api_runtime_conditions,
        plugin_sandbox_assurance_posture,
    )
    from AINDY.platform_layer.extension_runtime_inventory import trusted_python_execution_inventory
    from AINDY.platform_layer.sandbox_runner import (
        sandbox_escape_test_posture,
        sandbox_platform_capability_matrix,
    )

    try:
        from AINDY.platform_layer.health_service import sandbox_verification_posture
        _sv_posture = sandbox_verification_posture()
    except Exception:
        _sv_posture = {"skipped": True, "reason": "database not configured"}

    try:
        posture = plugin_sandbox_assurance_posture()
        payload = {
            "plugin_sandbox_posture": posture,
            "plugin_sandbox_platform": sandbox_platform_capability_matrix(),
            "sandbox_verification_posture": _sv_posture,
            "escape_test_posture": sandbox_escape_test_posture(),
            "trusted_python_execution": trusted_python_execution_inventory(),
            "runtime_conditions": get_api_runtime_conditions(),
        }
        req_status = posture.get("requirement_status", {})
        satisfied = bool(
            req_status.get("assurance_class_satisfied", False)
            and req_status.get("certification_tier_satisfied", False)
        )
        if output_json:
            print(json.dumps(payload, indent=2))
        else:
            print(_format_sandbox_summary(payload))
        raise SystemExit(0 if satisfied else 1)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"sandbox check failed: {exc}", file=_sys.stderr)
        raise SystemExit(2)


def _promote_admin(email: str) -> NoReturn:
    """Grant is_admin=True to the user with the given email. Grant-only — never demotes."""
    from AINDY.config import settings
    if not settings.DATABASE_URL:
        print(
            "error: DATABASE_URL is not set.\n"
            "Set DATABASE_URL before running this command.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    try:
        from AINDY.db.database import SessionLocal
        from AINDY.db.models.user import User
    except Exception as exc:
        print(f"error: could not import database layer: {exc}", file=sys.stderr)
        raise SystemExit(2)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if user is None:
            print(
                f"error: no user with email {email!r}.\n"
                "Register first via POST /auth/register, then re-run this command.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        if user.is_admin:
            print(f"ok: {email!r} is already admin. No change made.")
            raise SystemExit(0)
        user.is_admin = True
        db.commit()
        print(f"ok: granted is_admin=True to {email!r}.")
        raise SystemExit(0)
    except SystemExit:
        raise
    except Exception as exc:
        # Detect DB connection failures and surface a clean message rather than
        # leaking SQLAlchemy / psycopg2 internals.
        exc_str = str(exc)
        if any(k in exc_str for k in ("OperationalError", "could not connect", "could not translate", "Connection refused")):
            print(
                "error: could not connect to database.\n"
                "Check that DATABASE_URL points to a reachable PostgreSQL instance.\n"
                f"  detail: {exc.__cause__ or exc}",
                file=sys.stderr,
            )
        else:
            print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
    finally:
        db.close()


def _bootstrap_schema(reconcile: bool) -> NoReturn:
    """Build runtime-owned tables from packaged metadata and stamp alembic_version_runtime.

    The clean-ownership deploy primitive (APP-DEPLOY-1): builds ONLY the runtime's own
    tables (never app tables) from packaged ORM metadata, then stamps the runtime's
    Alembic version table to head so a create_all-built database has a proper baseline
    for future runtime upgrades. Idempotent. Requires DATABASE_URL.
    """
    from AINDY.config import settings
    if not settings.DATABASE_URL:
        print(
            "error: DATABASE_URL is not set.\n"
            "Set DATABASE_URL before running this command.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    try:
        from AINDY.db.database import engine
        from AINDY.db.schema_contract import ensure_runtime_schema
        from AINDY.db.alembic_head import (
            RUNTIME_ALEMBIC_VERSION_TABLE,
            stamp_runtime_alembic_head,
        )
    except Exception as exc:
        print(f"error: could not import database layer: {exc}", file=sys.stderr)
        raise SystemExit(2)

    try:
        # (a) Build/reconcile the runtime-owned tables. Reuses the exact path the
        # server runs on a blank DB at startup, scoped to runtime-owned tables only.
        report = ensure_runtime_schema(
            engine,
            allow_bootstrap=True,
            allow_reconcile=reconcile,
        )
        if report.bootstrapped:
            print("ok: bootstrapped runtime-owned tables from packaged metadata.")
        elif report.reconciled:
            print("ok: reconciled runtime-owned tables to packaged metadata.")
        else:
            print("ok: runtime-owned tables already present (no table changes).")

        if not report.ok:
            print(
                f"error: runtime-owned schema is not ready: {report.summary()}\n"
                "Re-run with --reconcile for an additive column/index fix, or perform "
                "the required offline migration before retrying.",
                file=sys.stderr,
            )
            raise SystemExit(1)

        # (b) Stamp the runtime's Alembic baseline (the half the app layer cannot do).
        rev = stamp_runtime_alembic_head(engine)
        print(f"ok: stamped {RUNTIME_ALEMBIC_VERSION_TABLE} to revision {rev}.")
        raise SystemExit(0)
    except SystemExit:
        raise
    except Exception as exc:
        exc_str = str(exc)
        if any(k in exc_str for k in ("OperationalError", "could not connect", "could not translate", "Connection refused")):
            print(
                "error: could not connect to database.\n"
                "Check that DATABASE_URL points to a reachable PostgreSQL instance.\n"
                f"  detail: {exc.__cause__ or exc}",
                file=sys.stderr,
            )
        else:
            print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)


def _prune_cascade_debris(*, yes: bool, batch_size: int) -> NoReturn:
    """Delete memory nodes created by the RT-MEMTXN-LEAK-1 capture cascade.

    Deployments that ran a version before the fix accumulated memory nodes recording
    nothing but the runtime's own embedding jobs starting. Scoped by
    ``extra.event_payload.task_name`` — precisely the set the fixed capture path now
    refuses to create — so no user- or app-authored memory can match. Dry-run unless
    --yes. Requires DATABASE_URL.
    """
    from AINDY.config import settings
    if not settings.DATABASE_URL:
        print("error: DATABASE_URL is not set.", file=sys.stderr)
        raise SystemExit(1)

    try:
        from AINDY.memory.cascade_cleanup import prune_cascade_debris
    except Exception as exc:
        print(f"error: could not import cleanup layer: {exc}", file=sys.stderr)
        raise SystemExit(2)

    try:
        report = prune_cascade_debris(dry_run=not yes, batch_size=batch_size)
    except Exception as exc:
        print(f"error: cascade-debris cleanup failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

    for item in report["breakdown"]:
        scope = "global" if item["global"] else "owned"
        print(
            f"  {item['count']:>7}  {item['task_name']}  {item['event_type'] or '-'}  ({scope})"
        )

    if not report["matched"]:
        print("ok: no cascade debris found — nothing to remove.")
        raise SystemExit(0)

    if not yes:
        print(
            f"dry-run: {report['matched']} node(s) match "
            f"({report['global']} global, {report['owned']} owned). "
            "Re-run with --yes to delete."
        )
        raise SystemExit(0)

    print(
        f"ok: deleted {report['deleted']} node(s) in {report['batches']} batch(es) "
        f"of {batch_size}. Child rows removed by ON DELETE CASCADE."
    )
    raise SystemExit(0)


def _reembed_memory(*, yes: bool, no_drain: bool, dry_run: bool) -> NoReturn:
    """Re-embed all memory nodes with the configured provider (ECOGAP-3 Phase 1).

    Run after changing AINDY_EMBEDDING_PROVIDER / AINDY_EMBEDDING_DIMENSIONS: alters the
    pgvector column to the new dimension and regenerates every vector. Destructive
    (drops existing embeddings) — intended with traffic stopped. Requires DATABASE_URL +
    PostgreSQL.
    """
    from AINDY.config import settings
    if not settings.DATABASE_URL:
        print("error: DATABASE_URL is not set.", file=sys.stderr)
        raise SystemExit(1)

    try:
        from AINDY.memory.embedding_migration import reembed_all_memory_nodes
        from AINDY.memory.embedding_providers import (
            EmbeddingProviderError,
            resolve_embedding_column_dimensions,
        )
    except Exception as exc:
        print(f"error: could not import migration layer: {exc}", file=sys.stderr)
        raise SystemExit(2)

    try:
        if dry_run:
            report = reembed_all_memory_nodes(dry_run=True)
            print(
                f"dry-run: provider={report['provider']} target_dim={report['target_dimension']} "
                f"rows={report['total_rows']}"
            )
            raise SystemExit(0)

        if not yes:
            print(
                "error: reembed is destructive (drops and regenerates every memory embedding "
                f"at dimension {resolve_embedding_column_dimensions()}).\n"
                "Stop traffic, then re-run with --yes to proceed (or --dry-run to preview).",
                file=sys.stderr,
            )
            raise SystemExit(1)

        report = reembed_all_memory_nodes(alter_column=True, drain=not no_drain)
        print(
            f"ok: provider={report['provider']} dim={report['target_dimension']} "
            f"column_altered={report['column_altered']} reembedded={report['reembedded']} "
            f"deferred={report['deferred']} (total {report['total_rows']})"
        )
        if no_drain:
            print("note: --no-drain set; the background embedding sweep will regenerate vectors.")
        raise SystemExit(0)
    except SystemExit:
        raise
    except EmbeddingProviderError as exc:
        print(f"error: provider/dimension mismatch: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:
        exc_str = str(exc)
        if any(k in exc_str for k in ("OperationalError", "could not connect", "Connection refused")):
            print(f"error: could not connect to database.\n  detail: {exc.__cause__ or exc}", file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)


def _run_mcp_server(transport: str, host: str = "0.0.0.0", port: int = 8080) -> NoReturn:
    """Serve AINDY syscalls as an MCP server (ECOGAP-4 / G4b, server-side).

    stdio: a standalone process an MCP client (e.g. Claude Desktop) spawns, acting as the
    single configured AINDY_MCP_SERVER_USER_ID. sse: a remote HTTP server; with
    AINDY_MCP_SERVER_MULTI_TENANT=true each session's bearer/platform-key header resolves to
    a real user and calls dispatch as that identity (MEB-3a). Requires DATABASE_URL.
    """
    from AINDY.config import settings
    if not settings.DATABASE_URL:
        print(
            "error: DATABASE_URL is not set.\n"
            "The MCP server dispatches syscalls that need a database.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    try:
        from AINDY.platform_layer import mcp_server
    except Exception as exc:
        print(
            "error: MCP server support is unavailable. Install the extra:\n"
            f"  pip install 'aindy-runtime[mcp]'\n  detail: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    try:
        if transport == "sse":
            mcp_server.serve_sse(host=host, port=port)  # blocks
        else:
            mcp_server.serve_stdio()  # blocks until the MCP client disconnects
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(0)


def _init(target_dir: str, force: bool) -> NoReturn:
    """Scaffold AINDY/.env, Dockerfile, docker-compose.yml, and docker/init-pgvector.sql."""
    import secrets
    from pathlib import Path

    root = Path(target_dir).resolve()
    created: list[str] = []
    skipped: list[str] = []

    def _write(path: Path, content: str) -> None:
        rel = str(path.relative_to(root))
        if path.exists() and not force:
            skipped.append(rel)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        created.append(rel)

    secret_key = secrets.token_hex(32)

    # ── AINDY/.env ────────────────────────────────────────────────────────────
    _write(root / "AINDY" / ".env", f"""\
# aindy-runtime — generated by `aindy-runtime init`
# Edit values as needed. Do NOT commit this file to version control.

DATABASE_URL=postgresql://aindy:aindy@postgres:5432/aindy
SECRET_KEY={secret_key}
ENV=development
AINDY_BOOT_MODE=runtime-only

# Uncomment and set to your OpenAI key to enable memory embeddings + LLM features.
# OPENAI_API_KEY=

# Optional — grant admin on first boot (register first, then set and restart).
# AINDY_BOOTSTRAP_ADMIN_EMAIL=

# Full reference: https://github.com/Masterplanner25/aindy-runtime/blob/main/AINDY/.env.example
""")

    # ── docker/init-pgvector.sql ──────────────────────────────────────────────
    _write(root / "docker" / "init-pgvector.sql", """\
-- Enable the pgvector extension (required for memory embedding columns).
-- Runs once on first postgres container initialization.
CREATE EXTENSION IF NOT EXISTS vector;
""")

    # ── Dockerfile ────────────────────────────────────────────────────────────
    _write(root / "Dockerfile", f"""\
FROM python:3.11-slim

# Install psycopg2 system deps and the runtime package from PyPI.
RUN apt-get update && apt-get install -y --no-install-recommends \\
        libpq-dev \\
    && rm -rf /var/lib/apt/lists/* \\
    && pip install --no-cache-dir aindy-runtime=={__version__}

WORKDIR /app

# Bind on all interfaces so the compose port mapping reaches the host.
ENV AINDY_HOST=0.0.0.0
# Stable, version-independent path for the env file bind-mount.
ENV AINDY_ENV_FILE=/etc/aindy/.env

CMD ["aindy-runtime", "serve"]
""")

    # ── docker-compose.yml ────────────────────────────────────────────────────
    _write(root / "docker-compose.yml", f"""\
# aindy-runtime {__version__} — generated by `aindy-runtime init`
#
# Quickstart:
#   docker compose build
#   docker compose up -d
#   # Visit http://localhost:8000/platform
#   # Register: POST /auth/register  {{email, password, username}}
#   # Promote admin: docker compose exec api aindy-runtime auth promote-admin <email>
#
# To add Redis + worker (production-shaped, distributed job queue):
#   Set EXECUTION_MODE=distributed in AINDY/.env
#   Then: docker compose --profile full up -d

services:

  postgres:
    image: pgvector/pgvector:pg16
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${{POSTGRES_DB:-aindy}}
      POSTGRES_USER: ${{POSTGRES_USER:-aindy}}
      POSTGRES_PASSWORD: ${{POSTGRES_PASSWORD:-aindy}}
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./docker/init-pgvector.sql:/docker-entrypoint-initdb.d/init-pgvector.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${{POSTGRES_USER:-aindy}} -d ${{POSTGRES_DB:-aindy}}"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s
    ports:
      - "5432:5432"

  api:
    build: .
    image: aindy-runtime:{__version__}
    restart: unless-stopped
    env_file:
      - AINDY/.env
    environment:
      AINDY_ENV_FILE: /etc/aindy/.env
      AINDY_HOST: "0.0.0.0"
    volumes:
      - ./AINDY/.env:/etc/aindy/.env:ro
    ports:
      - "8000:8000"
    depends_on:
      postgres:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "--fail", "--silent", "http://localhost:8000/health"]
      interval: 15s
      timeout: 5s
      retries: 5
      start_period: 40s

  redis:
    image: redis:7-alpine
    profiles: ["full"]
    restart: unless-stopped
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  postgres_data:
""")

    # ── Summary ───────────────────────────────────────────────────────────────
    if created:
        print(f"aindy-runtime init  (v{__version__})")
        print("")
        print("Created:")
        for f in created:
            print(f"  {f}")
    if skipped:
        print("")
        print("Skipped (already exist — use --force to overwrite):")
        for f in skipped:
            print(f"  {f}")

    if not created and not skipped:
        print("Nothing to do.")
        raise SystemExit(0)

    if created:
        print("")
        print("Next steps:")
        print("  1. docker compose build")
        print("  2. docker compose up -d")
        print("  3. curl http://localhost:8000/health")
        print("  4. POST /auth/register  {email, password, username}")
        print("  5. docker compose exec api aindy-runtime auth promote-admin <email>")
        print("  6. Visit http://localhost:8000/platform")
        print("")
        print(f"Your SECRET_KEY has been generated and written to AINDY/.env.")
        print("Edit AINDY/.env to add OPENAI_API_KEY and any other settings before starting.")

    raise SystemExit(0)


def _serve() -> NoReturn:
    """Start the aindy-runtime HTTP API server."""
    from AINDY.platform_layer.deployment_contract import BOOT_MODE_ENV_VAR, RUNTIME_ONLY_BOOT_MODE
    os.environ.setdefault(BOOT_MODE_ENV_VAR, RUNTIME_ONLY_BOOT_MODE)

    from AINDY.config import settings
    if not settings.DATABASE_URL:
        print(
            "error: DATABASE_URL is not set.\n"
            "\n"
            "Docker Compose quickstart (recommended):\n"
            "  cp AINDY/.env.example AINDY/.env   # then edit with real values\n"
            "  docker compose up -d\n"
            "\n"
            "Manual / local dev:\n"
            "  DATABASE_URL=postgresql://user:password@host:5432/aindy aindy-runtime serve\n"
            "\n"
            "For full setup instructions see README.md — Quickstart.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    import uvicorn
    uvicorn.run(
        "AINDY.runtime_only:app",
        host=os.getenv("AINDY_HOST", "127.0.0.1"),
        port=int(os.getenv("AINDY_PORT", "8000")),
    )
    raise SystemExit(0)


def main() -> None:
    import logging as _logging
    # Suppress INFO logs from config.py's Settings() initialization so that
    # every CLI invocation (including --help) stays visually clean.
    # WARNING and above still propagate (e.g. misconfiguration messages).
    _logging.disable(_logging.INFO)

    parser = argparse.ArgumentParser(
        prog="aindy-runtime",
        description="A.I.N.D.Y. runtime - HTTP server and diagnostics.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"aindy-runtime {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command")
    init_parser = subparsers.add_parser(
        "init",
        help="Scaffold AINDY/.env, Dockerfile, docker-compose.yml for a new install.",
        description=(
            "Scaffold all files needed to run aindy-runtime in Docker: "
            "AINDY/.env (with a generated SECRET_KEY), Dockerfile, "
            "docker-compose.yml, and docker/init-pgvector.sql. "
            "Existing files are skipped unless --force is given. "
            "Does not require a running database."
        ),
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Overwrite existing files.",
    )
    init_parser.add_argument(
        "--dir",
        dest="target_dir",
        default=".",
        metavar="PATH",
        help="Directory to scaffold into (default: current directory).",
    )
    subparsers.add_parser(
        "serve",
        help="Start the aindy-runtime HTTP API server.",
        description=(
            "Start the aindy-runtime HTTP API server. "
            "DATABASE_URL must be set to a valid PostgreSQL URI. "
            "AINDY_HOST (default 127.0.0.1) and AINDY_PORT (default 8000) "
            "control the bind address."
        ),
    )
    sandbox_parser = subparsers.add_parser(
        "sandbox",
        help="Report sandbox capabilities and exit.",
        description=(
            "Report sandbox assurance posture and exit. "
            "Prints a human-readable summary by default. "
            "Exit 0 if all requirements are satisfied, 1 if not, 2 on error. "
            "Does not require a running database."
        ),
    )
    sandbox_parser.add_argument(
        "--json",
        dest="output_json",
        action="store_true",
        default=False,
        help="Output raw JSON instead of the human-readable summary.",
    )

    bootstrap_parser = subparsers.add_parser(
        "bootstrap-schema",
        help="Create runtime-owned tables from packaged metadata and stamp the Alembic baseline.",
        description=(
            "Idempotently bootstrap the runtime-owned database surface: build the "
            "runtime's own tables from packaged ORM metadata (never app-owned tables) "
            "and stamp alembic_version_runtime to the runtime head revision. This gives "
            "a create_all-built database a proper Alembic baseline so a later runtime "
            "schema upgrade has a stamped line to migrate from. Safe to run repeatedly. "
            "Requires DATABASE_URL. Intended for a deploy entrypoint that splits schema "
            "ownership: run this for runtime tables + baseline, then have the app build "
            "only its own tables."
        ),
    )
    bootstrap_parser.add_argument(
        "--reconcile",
        action="store_true",
        default=False,
        help="Also apply additive column/index reconciles if the runtime schema is out of date.",
    )

    mcp_server_parser = subparsers.add_parser(
        "mcp-server",
        help="Serve AINDY syscalls as an MCP server for external MCP clients (Claude Desktop, etc.).",
        description=(
            "Expose an allowlist of AINDY syscalls as MCP tools, as a standalone process an "
            "MCP client spawns (stdio) or connects to (SSE). Over stdio, every call runs as the "
            "single configured identity AINDY_MCP_SERVER_USER_ID. Over SSE with "
            "AINDY_MCP_SERVER_MULTI_TENANT=true, each session's Authorization: Bearer / "
            "X-Platform-Key header resolves to a real user and calls dispatch as that identity "
            "(MEB-3a). Read-only tools by default; set AINDY_MCP_SERVER_ALLOW_WRITES=true to "
            "expose writes, or AINDY_MCP_SERVER_TOOLS to override the allowlist. Requires "
            "DATABASE_URL and the [mcp] extra (pip install 'aindy-runtime[mcp]')."
        ),
    )
    mcp_server_parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="MCP transport: stdio (local single-operator) or sse (remote / multi-tenant).",
    )
    mcp_server_parser.add_argument(
        "--host", default="0.0.0.0", help="SSE bind host (default 0.0.0.0). Ignored for stdio.",
    )
    mcp_server_parser.add_argument(
        "--port", type=int, default=8080, help="SSE bind port (default 8080). Ignored for stdio.",
    )

    memory_parser = subparsers.add_parser(
        "memory",
        help="Memory management commands.",
        description="Memory subsystem utilities for the aindy-runtime.",
    )
    memory_sub = memory_parser.add_subparsers(dest="memory_command")
    reembed_parser = memory_sub.add_parser(
        "reembed",
        help="Re-embed all memory nodes with the configured provider (ECOGAP-3 Phase 1).",
        description=(
            "Regenerate every memory embedding with the currently-configured provider and "
            "dimension (AINDY_EMBEDDING_PROVIDER / AINDY_EMBEDDING_DIMENSIONS). Alters the "
            "pgvector column to the target dimension, then re-embeds each node. DESTRUCTIVE: "
            "drops existing embeddings. Intended with traffic stopped. Requires DATABASE_URL "
            "+ PostgreSQL. Use --dry-run to preview; --yes to proceed; --no-drain to alter + "
            "mark pending and let the background sweep regenerate."
        ),
    )
    reembed_parser.add_argument("--yes", action="store_true", default=False, help="Confirm the destructive re-embed.")
    reembed_parser.add_argument("--no-drain", action="store_true", default=False, help="Alter + mark pending only; defer regeneration to the background sweep.")
    reembed_parser.add_argument("--dry-run", action="store_true", default=False, help="Report the plan without mutating anything.")

    prune_parser = memory_sub.add_parser(
        "prune-cascade-debris",
        help="Delete memory nodes created by the RT-MEMTXN-LEAK-1 capture cascade.",
        description=(
            "One-time cleanup for deployments that ran a version before the "
            "RT-MEMTXN-LEAK-1 fix, where the runtime's own embedding jobs had their "
            "lifecycle events captured as memory — each capture spawning another job "
            "and another capture. Scoped by extra.event_payload.task_name (the same "
            "predicate the fixed capture path uses), so no user- or app-authored memory "
            "can match. Deletes in committed batches; child rows go via ON DELETE "
            "CASCADE. Reports without deleting unless --yes. Requires DATABASE_URL."
        ),
    )
    prune_parser.add_argument("--yes", action="store_true", default=False, help="Perform the delete (without this the command only reports).")
    prune_parser.add_argument("--batch-size", type=int, default=500, help="Rows deleted per committed batch (default 500).")

    auth_parser = subparsers.add_parser(
        "auth",
        help="Auth management commands.",
        description="Auth management utilities for the aindy-runtime.",
    )
    auth_sub = auth_parser.add_subparsers(dest="auth_command")
    promote_parser = auth_sub.add_parser(
        "promote-admin",
        help="Grant is_admin=True to an existing user by email (grant-only, never demotes).",
        description=(
            "Grant admin privileges to an existing user. "
            "The user must already be registered via POST /auth/register. "
            "This command is grant-only: running it never removes admin from anyone. "
            "Requires DATABASE_URL to be set."
        ),
    )
    promote_parser.add_argument("email", help="Email address of the user to promote.")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        raise SystemExit(0)

    if args.command == "init":
        _init(target_dir=args.target_dir, force=args.force)
    elif args.command == "serve":
        _serve()
    elif args.command == "sandbox":
        _run_sandbox_check(output_json=getattr(args, "output_json", False))
    elif args.command == "bootstrap-schema":
        _bootstrap_schema(reconcile=getattr(args, "reconcile", False))
    elif args.command == "mcp-server":
        _run_mcp_server(
            transport=getattr(args, "transport", "stdio"),
            host=getattr(args, "host", "0.0.0.0"),
            port=getattr(args, "port", 8080),
        )
    elif args.command == "memory":
        if args.memory_command == "reembed":
            _reembed_memory(
                yes=getattr(args, "yes", False),
                no_drain=getattr(args, "no_drain", False),
                dry_run=getattr(args, "dry_run", False),
            )
        elif args.memory_command == "prune-cascade-debris":
            _prune_cascade_debris(
                yes=getattr(args, "yes", False),
                batch_size=getattr(args, "batch_size", 500),
            )
        else:
            memory_parser.print_help()
            raise SystemExit(0)
    elif args.command == "auth":
        if args.auth_command == "promote-admin":
            _promote_admin(args.email)
        else:
            auth_parser.print_help()
            raise SystemExit(0)


if __name__ == "__main__":
    main()
