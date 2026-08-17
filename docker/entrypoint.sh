#!/usr/bin/env sh
# Businext backend container entrypoint.
#
# With RUN_MIGRATIONS=1 (the default) Alembic runs first, overriding the
# hardcoded sqlalchemy.url in alembic.ini with DATABASE_MIGRATION_URI.
# uvicorn defaults to a single worker: main.py's lifespan starts a background
# task that extra workers would duplicate.

set -eu

RUN_MIGRATIONS="${RUN_MIGRATIONS:-1}"
PORT="${PORT:-8000}"
UVICORN_WORKERS="${UVICORN_WORKERS:-1}"

if [ "$RUN_MIGRATIONS" = "1" ]; then
  if [ -z "${DATABASE_MIGRATION_URI:-}" ]; then
    echo "WARN: DATABASE_MIGRATION_URI unset, using the alembic.ini url as-is."
    alembic upgrade head
  else
    echo "Running migrations with DATABASE_MIGRATION_URI..."
    # Escape '/' and '&' for sed, then patch alembic.ini in place (not persisted
    # in the image).
    escaped_url=$(printf '%s' "$DATABASE_MIGRATION_URI" | sed -e 's/[\/&]/\\&/g')
    sed -i "s|^sqlalchemy.url = .*|sqlalchemy.url = ${escaped_url}|" alembic.ini
    alembic upgrade head
  fi
else
  echo "RUN_MIGRATIONS=0, skipping migrations."
fi

echo "Starting uvicorn on port ${PORT} with ${UVICORN_WORKERS} worker(s)..."
exec uvicorn src.main:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --workers "$UVICORN_WORKERS" \
  --proxy-headers
