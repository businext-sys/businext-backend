# syntax=docker/dockerfile:1.7
# ---------- Base ----------
# Imagen ligera con Python 3.12 (misma version que CI).
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# `psycopg2-binary` no requiere build tools ni libpq-dev.
# `tini` para manejo correcto de senales (evita procesos zombie).
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instalar dependencias de forma cacheable (antes de copiar el codigo).
COPY requirements.txt requirements-dev.txt ./

# ---------- Prod ----------
FROM base AS prod

RUN pip install -r requirements.txt

# Copiar codigo. .dockerignore excluye .env, .venv, __pycache__, tests, etc.
COPY . .

# Puerto configurable (Render/Railway suelen inyectar $PORT).
ENV PORT=8000
EXPOSE 8000

# Healthcheck contra el endpoint /health que ya existe en src/main.py.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS "http://localhost:${PORT}/health" || exit 1

# tini como PID 1, luego el entrypoint que orquesta migraciones + uvicorn.
ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker/entrypoint.sh"]

# ---------- Dev / Test ----------
# Imagen adicional que incluye deps de desarrollo (pytest, ruff).
# Usada por docker-compose.test.yml del qa-runner.
FROM base AS test

RUN pip install -r requirements.txt -r requirements-dev.txt

COPY . .

ENV PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=5 \
  CMD curl -fsS "http://localhost:${PORT}/health" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker/entrypoint.sh"]
