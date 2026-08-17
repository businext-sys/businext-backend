# syntax=docker/dockerfile:1.7
# ---------- Base ----------
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# tini reaps zombie processes and forwards signals.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copied before the source so dependency installs stay cached.
COPY requirements.txt requirements-dev.txt ./

# ---------- Prod ----------
FROM base AS prod

RUN pip install -r requirements.txt

COPY . .

ENV PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS "http://localhost:${PORT}/health" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker/entrypoint.sh"]

# ---------- Dev / Test ----------
# Adds dev dependencies; used by the qa-runner's docker-compose.test.yml.
FROM base AS test

RUN pip install -r requirements.txt -r requirements-dev.txt

COPY . .

ENV PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=5 \
  CMD curl -fsS "http://localhost:${PORT}/health" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker/entrypoint.sh"]
