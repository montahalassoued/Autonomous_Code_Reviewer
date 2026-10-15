# File purpose: Multi-stage container image for the FastAPI review service.
# Inputs: Repository source code (filtered by .dockerignore) and requirements.txt.
# Outputs: A non-root runtime image that serves the app on $PORT (default 8000).
#          Secrets are NOT in the image; pass them as env vars at runtime.
# Build requires BuildKit (default in current Docker): cache mounts are used.

ARG PYTHON_VERSION=3.11

# ---------------------------------------------------------------------------
# Stage 1: builder - compilers live here only and are discarded afterwards.
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Dependency layer: only re-runs when requirements.txt changes.
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# ---------------------------------------------------------------------------
# Stage 2: runtime - slim Python + git only.
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000

# git is required by rag/ingestion.py (GitPython clones TARGET_REPO_URL).
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# Fixed uid so volume/file-share ownership is predictable across hosts.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app

WORKDIR /app

# Writable runtime dir: the cloned target repo the agents' tools read.
RUN mkdir -p /app/target_repo \
    && chown -R app:app /app

COPY --from=builder /opt/venv /opt/venv

USER app

# Source last (changes most often). .dockerignore keeps .env, tests, docs out.
COPY --chown=app:app . .

EXPOSE 8000

# The API starts instantly (no ingestion; the worker does that), so a short
# start-period is enough. Reads PORT in Python (exec form has no shell expansion).
# The worker has no HTTP server: compose disables this check for it.
# Note: Azure Container Apps ignores this and uses its own probes.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'), timeout=4)"]

# sh -c + exec: expands $PORT, then uvicorn replaces the shell and becomes
# PID 1, so SIGTERM from the orchestrator reaches it for graceful shutdown.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
