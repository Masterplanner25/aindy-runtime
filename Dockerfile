# syntax=docker/dockerfile:1.6
#
# A.I.N.D.Y. Runtime — Container Image
#
# Multi-stage build:
#   Stage 1 (builder) — builds a wheel from local source and installs it
#                       into a relocatable prefix. Carries compilers and
#                       build headers that do NOT propagate to runtime.
#   Stage 2 (runtime) — copies only the installed package, runs as a
#                       non-root user, ships with libpq for psycopg and
#                       curl for the HEALTHCHECK.
#
# Post-PyPI-publish transition:
#   When aindy-runtime is published to PyPI, replace the wheel-build
#   block in the builder stage with:
#       RUN pip install --prefix=/install "aindy-runtime==1.0.0"
#   and remove the WORKDIR /src + COPY . /src + `python -m build` lines.
#   Everything else stays identical. See TECH_DEBT: PYPI-PUBLISH-1.


# ═══════════════════════════════════════════════════════════════════════════
# Stage 1: builder
# ═══════════════════════════════════════════════════════════════════════════
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# Build-time deps: compilers and headers for any wheels that compile from
# source (psycopg, cryptography). These do NOT propagate to the runtime
# stage — only the installed Python package is copied forward.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy source. .dockerignore controls what is actually included in the
# build context; without it this COPY drags .git/, tempverify-*/, .venv/,
# tests/, and other artifacts into the builder.
WORKDIR /src
COPY . /src

# Build the wheel from local source, then install it into a relocatable
# prefix that we copy into the runtime stage.
#
# `packaging` is a declared dependency (required by limits → slowapi) but pip
# treats it as a bootstrap package and skips it when resolving --prefix installs
# because it is present in the system site-packages of the builder image.
# Forcing it into /install ensures it propagates to the runtime stage.
RUN pip install build \
    && python -m build --wheel --outdir /dist /src \
    && pip install --prefix=/install /dist/*.whl \
    && pip install --prefix=/install --ignore-installed "packaging>=24.0"


# ═══════════════════════════════════════════════════════════════════════════
# Stage 2: runtime
# ═══════════════════════════════════════════════════════════════════════════
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Runtime deps only:
#   libpq5 — psycopg's shared library, required at execution time
#   curl   — HEALTHCHECK probe
# No compilers, no build headers. Keeps the image small and the attack
# surface minimal.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Copy the installed package and console scripts from the builder.
# /install/bin/aindy-runtime  →  /usr/local/bin/aindy-runtime  (on $PATH)
# /install/lib/python3.11/... →  /usr/local/lib/python3.11/... (importable)
COPY --from=builder /install /usr/local

# Non-root user for the runtime process. UID/GID 1000 is the conventional
# first non-system user; mount-point ownership on the host should match
# if operators bind-mount config/data volumes.
RUN groupadd --system --gid 1000 aindy \
    && useradd --system --uid 1000 --gid aindy --create-home aindy

USER aindy
WORKDIR /home/aindy

# Alembic migration files — not part of the installed wheel; required for
# `alembic upgrade head` on container boot. Copied here so the working
# directory (cwd when `alembic upgrade head` runs) contains alembic.ini.
COPY --chown=aindy:aindy alembic.ini ./
COPY --chown=aindy:aindy alembic/ ./alembic/

EXPOSE 8000

# Liveness probe — does NOT gate on database or external services. Docker
# uses this to decide whether to restart the container, so transient DB
# hiccups must not flip it to unhealthy. For traffic gating (load balancer
# probes, compose depends_on condition checks), use /ready instead.
# start-period gives the runtime 30s to complete boot (DB connect, schema
# enforcement, connection pool warmup) before the first probe counts.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl --fail --silent http://localhost:8000/health || exit 1

CMD ["aindy-runtime", "serve"]
