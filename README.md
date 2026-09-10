# businext-backend

<!-- staging: retrigger deploy 2026-09-10 -->

API de Businext: FastAPI + SQLModel sobre Postgres (Supabase), con
autenticacion por JWT de Supabase.

Repos hermanos: [`businext`](https://github.com/businext-sys/businext)
(monorepo web + mobile + shared-core) y
[`businext-qa-runner`](https://github.com/businext-sys/businext-qa-runner)
(tests E2E, API y carga).

## Stack

- **FastAPI 0.116** + **uvicorn** — un solo worker: `main.py` usa un
  `lifespan` con una tarea de fondo (`expire_old_requests`) que se duplicaria
  con varios workers.
- **SQLModel / SQLAlchemy 2** + **psycopg2** — Postgres gestionado por Supabase.
- **PyJWT** — validacion de los JWT de Supabase (`SUPABASE_JWT_SECRET`).
- **Alembic** — configurado, pero sin revisiones commiteadas (ver
  [Migraciones](#migraciones)).
- **OpenAI** (`ai_service`), **Outscraper** (resenas de Google), **Resend** y
  **Gmail SMTP** (emails), **Expo Push API** (notificaciones).

## Requisitos

- **Python 3.12** — la misma que CI (`.github/workflows/ci.yml`) y el
  `Dockerfile`. Las dependencias estan pinneadas (`psycopg2-binary==2.9.11`,
  `pydantic-core`) y no hay garantia de wheels para versiones mas nuevas.
- Acceso al proyecto de Supabase (URL + keys + JWT secret).

La forma mas simple de obtener 3.12 sin instalarla en el sistema es
[uv](https://docs.astral.sh/uv/), que la descarga sola.

## Arranque local

```bash
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -r requirements.txt -r requirements-dev.txt
uvicorn src.main:app --reload --port 8000
```

Comprobacion: `curl http://localhost:8000/health` → `{"status":"ok"}`.
Documentacion interactiva en <http://localhost:8000/docs>.

Para que la app movil llegue desde un telefono fisico, expon el servidor en
todas las interfaces (`--host 0.0.0.0`) y usa tu IP de LAN en
`EXPO_PUBLIC_API_BASE_URL`.

### Variables de entorno

En un `.env` en la raiz del repo (gitignored). Se carga con `load_dotenv()`
desde `src/database/database.py`, `src/api/auth.py` y algunos servicios.

```env
# Requeridas: sin DATABASE_URI el arranque falla con RuntimeError, y sin
# SUPABASE_JWT_SECRET todo endpoint autenticado devuelve 401.
DATABASE_URI=postgresql://postgres:<pass>@db.<proyecto>.supabase.co:5432/postgres
SUPABASE_JWT_SECRET=<JWT secret del proyecto de Supabase>

# Invitaciones de empleados (src/services/supabase_utils.py)
SUPABASE_URL=https://<proyecto>.supabase.co
SUPABASE_ANON_KEY=<anon key>
SUPABASE_SERVICE_ROLE_KEY=<service role key>
APP_URL=http://localhost:3000        # base de los links de invitacion

# Opcionales: la feature correspondiente degrada si faltan
OPENAI_API_KEY=                      # src/services/ai_service.py
OUTSCRAPER_API_KEY=                  # resenas de Google
RESEND_API_KEY=                      # emails transaccionales
RESEND_FROM_EMAIL=noreply@businext.app
GMAIL_BUSINEXT_USER=                 # src/services/employee_service.py
GMAIL_BUSINEXT_PASSWORD=
```

### CORS

`src/main.py` permite `http://localhost:3000`, el dominio de produccion y los
previews de staging en Vercel (ver la lista `origins` y `allow_origin_regex`).
Si sirves la web en otro puerto, hay que anadirlo a la lista `origins`.

## Migraciones

**`alembic/versions/` esta en el `.gitignore` y nunca se commiteo**: el repo no
contiene ninguna revision (`alembic heads` devuelve vacio). La base de datos
real, en cambio, tiene su `alembic_version` sellada en la revision `014`: esas
14 revisiones existieron en la maquina de alguien y no se versionaron.

Consecuencia concreta: **`alembic upgrade head` falla** contra la base real.

```
$ alembic upgrade head
ERROR [alembic.util.messaging] Can't locate revision identified by '014'
FAILED: Can't locate revision identified by '014'
```

Falla al resolver el grafo de revisiones, antes de tocar el esquema, asi que no
es destructivo — pero no funciona. Y contra una base vacia no fallaria, pero no
aplicaria nada, asi que tampoco puede construir el esquema desde cero.

En la practica:

- Apunta `DATABASE_URI` a la base de Supabase ya migrada. No levantes un
  Postgres local vacio esperando que Alembic lo pueble.
- **En Docker, arranca con `RUN_MIGRATIONS=0`.** El entrypoint corre con
  `set -eu`, asi que el fallo de `alembic upgrade head` mata el contenedor
  antes de que uvicorn llegue a arrancar.
- Cualquier tabla nueva hay que crearla a mano en Supabase. Caso conocido:
  `push_token` (issue #031 del monorepo).
- `alembic/env.py` si tiene `target_metadata = SQLModel.metadata`, asi que la
  autogeneracion funcionaria; lo que falta es versionar el resultado.

Salir de esto (deuda tecnica pendiente) es: quitar `alembic/versions/` del
`.gitignore`, generar un baseline con `alembic revision --autogenerate`,
sellarlo con `alembic stamp <rev>` para que coincida con lo que ya hay en la
base, y commitearlo.

### De donde sale la URL

`alembic.ini` esta commiteado; **no contiene ninguna credencial**.
`alembic/env.py` resuelve la URL en tiempo de ejecucion:

1. `DATABASE_MIGRATION_URI`, si esta definida (permite migrar con un rol mas
   privilegiado que el de la app en runtime).
2. `DATABASE_URI` como fallback — la misma que usa la app.
3. Si no hay ninguna, aborta con un `RuntimeError` explicito.

Un `%` literal en la contrasena se escapa solo antes de pasarlo a
`set_main_option` (configparser interpola `%`).

## Estructura

```text
src/
├── main.py                 App FastAPI, CORS, lifespan y registro de routers
├── api/auth.py             Validacion de JWT + matriz de permisos por rol
├── database/
│   ├── database.py         Engine y sesion (SessionDep)
│   └── models/             Modelos SQLModel (tablas)
├── routers/                Endpoints HTTP, uno por dominio
└── services/               Logica de negocio e integraciones externas
alembic/                    Config de migraciones (sin versions/)
docker/entrypoint.sh        Migraciones + uvicorn en el contenedor
tests/                      pytest
```

Los roles son `owner > manager > employee`; la matriz de permisos por recurso
esta documentada en `src/api/auth.py`. Solo los owners necesitan una
suscripcion activa para acceder a los recursos protegidos.

## Tests y lint

```bash
ruff check src/
pytest tests/
```

`pytest` necesita `DATABASE_URI`, `SUPABASE_URL` y `SUPABASE_JWT_SECRET` en el
entorno (en CI vienen de secrets del repo).

## Docker

El `Dockerfile` es multi-stage: `prod` (solo `requirements.txt`) y `test`
(anade `requirements-dev.txt`, usado por el `docker-compose.test.yml` del
qa-runner).

```bash
docker build --target prod -t businext-backend .
docker run --rm -p 8000:8000 --env-file .env -e RUN_MIGRATIONS=0 businext-backend
```

`RUN_MIGRATIONS=0` no es opcional hoy: con el default (`1`) el contenedor
muere al arrancar, porque `alembic upgrade head` falla (ver
[Migraciones](#migraciones)) y el entrypoint corre con `set -eu`.

Variables que lee el entrypoint: `PORT` (default 8000), `UVICORN_WORKERS`
(default 1, no lo subas — ver la nota del `lifespan`), `RUN_MIGRATIONS`
(default 1) y `DATABASE_MIGRATION_URI`.

Despliegue actual: Render, en <https://businext-backend.onrender.com>
(produccion) y <https://businext-backend-staging.onrender.com> (staging).

## Generacion de tipos para el frontend

`packages/shared-core` del monorepo genera sus tipos desde el OpenAPI que
expone FastAPI en `/openapi.json` (`pnpm generate:types`, o el workflow
`generate-types.yml`). Un cambio de contrato aqui obliga a regenerarlos alli —
nunca se editan a mano.

> `src/export_schema.py` no tiene nada que ver con esto: es un script muerto de
> una epoca en la que el proyecto usaba SQLite (abre un `database.db` que ya no
> existe). Nadie lo importa; se puede borrar.
