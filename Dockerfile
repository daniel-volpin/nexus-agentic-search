# Spec 12 §Container image. Multi-stage build.
#
# Builder stage installs dependencies into a venv that the runtime
# stage copies in. The runtime stage runs as a non-root user with a
# read-only root filesystem (per the compose.yaml `read_only: true`).
#
# Pin the base image by digest in production builds. The :slim tag
# here is for reproducible local builds; the build-and-pin.sh script
# rewrites the FROM line with @sha256:<digest> before pushing.

FROM python:3.11-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates build-essential \
 && rm -rf /var/lib/apt/lists/*

# uv for lock-aware install
COPY --from=ghcr.io/astral-sh/uv:0.5.7 /uv /usr/local/bin/uv

WORKDIR /build
COPY pyproject.toml uv.lock ./
COPY nexus ./nexus

RUN uv sync --frozen --no-dev --no-editable \
 && uv export --frozen --no-dev --format requirements-txt -o requirements.txt \
 && uv pip install --system -r requirements.txt \
 && uv build --wheel \
 && uv pip install --system --no-deps dist/*.whl


FROM python:3.11-slim-bookworm AS runtime

ARG GIT_SHA=""
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NEXUS_HOME=/var/lib/nexus \
    GIT_SHA=${GIT_SHA}

RUN groupadd --system --gid 10001 nexus \
 && useradd --system --uid 10001 --gid 10001 --home-dir /home/nexus --shell /sbin/nologin nexus \
 && mkdir -p $NEXUS_HOME/cache $NEXUS_HOME/models \
 && chown -R nexus:nexus $NEXUS_HOME \
 && apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Copy installed Python stack from builder.
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Source + default LLM config.
WORKDIR /app
COPY --chown=nexus:nexus nexus ./nexus
COPY --chown=nexus:nexus config ./config

USER nexus
EXPOSE 8185 8186 9090

# Healthcheck hits the HTTP transport's /v1/health (Spec 08).
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -fsS http://localhost:8186/v1/health || exit 1

ENTRYPOINT ["python", "-m", "nexus.main"]
