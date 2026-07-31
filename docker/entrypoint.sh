#!/usr/bin/env sh
# Entrypoint del contenedor de Businext backend.
#
# Comportamiento:
# - Si RUN_MIGRATIONS=1 (por defecto), aplica migraciones Alembic antes de arrancar.
#   Se resuelve el hardcode de sqlalchemy.url en alembic.ini sobreescribiendolo
#   con DATABASE_MIGRATION_URI en tiempo de ejecucion.
# - Arranca uvicorn con un solo worker (main.py usa lifespan con background task,
#   correr multiples workers duplicaria ese task).

set -eu

RUN_MIGRATIONS="${RUN_MIGRATIONS:-1}"
PORT="${PORT:-8000}"
UVICORN_WORKERS="${UVICORN_WORKERS:-1}"

if [ "$RUN_MIGRATIONS" = "1" ]; then
  if [ -z "${DATABASE_MIGRATION_URI:-}" ]; then
    echo "WARN: DATABASE_MIGRATION_URI no definida, se usa la url del alembic.ini tal cual."
    alembic upgrade head
  else
    echo "Aplicando migraciones con DATABASE_MIGRATION_URI (override de alembic.ini)..."
    # Escapar '/' y '&' para sed.
    escaped_url=$(printf '%s' "$DATABASE_MIGRATION_URI" | sed -e 's/[\/&]/\\&/g')
    # Reescribir sqlalchemy.url en alembic.ini in-place, sin persistir en la imagen.
    sed -i "s|^sqlalchemy.url = .*|sqlalchemy.url = ${escaped_url}|" alembic.ini
    alembic upgrade head
  fi
else
  echo "RUN_MIGRATIONS=0, se saltan las migraciones."
fi

echo "Arrancando uvicorn en puerto ${PORT} con ${UVICORN_WORKERS} worker(s)..."
exec uvicorn src.main:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --workers "$UVICORN_WORKERS" \
  --proxy-headers
