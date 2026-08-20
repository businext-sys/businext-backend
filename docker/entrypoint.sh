#!/usr/bin/env sh
# Entrypoint del contenedor de Businext backend.
#
# Comportamiento:
# - Si RUN_MIGRATIONS=1 (por defecto), aplica migraciones Alembic antes de arrancar.
#   La URL la resuelve alembic/env.py desde el entorno (DATABASE_MIGRATION_URI, o
#   DATABASE_URI como fallback) y aborta con un mensaje claro si no hay ninguna;
#   alembic.ini ya no contiene ninguna credencial.
# - Arranca uvicorn con un solo worker (main.py usa lifespan con background task,
#   correr multiples workers duplicaria ese task).

set -eu

RUN_MIGRATIONS="${RUN_MIGRATIONS:-1}"
PORT="${PORT:-8000}"
UVICORN_WORKERS="${UVICORN_WORKERS:-1}"

if [ "$RUN_MIGRATIONS" = "1" ]; then
  if [ -n "${DATABASE_MIGRATION_URI:-}" ]; then
    echo "Aplicando migraciones con DATABASE_MIGRATION_URI..."
  else
    echo "Aplicando migraciones con DATABASE_URI (DATABASE_MIGRATION_URI no definida)..."
  fi
  alembic upgrade head
else
  echo "RUN_MIGRATIONS=0, se saltan las migraciones."
fi

echo "Arrancando uvicorn en puerto ${PORT} con ${UVICORN_WORKERS} worker(s)..."
exec uvicorn src.main:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --workers "$UVICORN_WORKERS" \
  --proxy-headers
