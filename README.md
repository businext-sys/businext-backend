# businext-backend

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

`src/main.py` permite solo `http://localhost:3000` y el dominio de produccion.
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
- Cualquier tabla nueva hay que crearla a mano en Supabase. Casos conocidos:
  `push_token` (issue #031 del monorepo) y las del historial del asistente
  (`docs/sql/024-assistant-conversations.sql`, `docs/sql/025-assistant-usage.sql`).
- `alembic/env.py` si tiene `target_metadata = SQLModel.metadata`, asi que la
  autogeneracion funcionaria; lo que falta es versionar el resultado.

Salir de esto (deuda tecnica pendiente) es: quitar `alembic/versions/` del
`.gitignore`, generar un baseline con `alembic revision --autogenerate`,
sellarlo con `alembic stamp <rev>` para que coincida con lo que ya hay en la
base, y commitearlo.

### De donde sale la URL

`alembic.ini` esta commiteado y **el repo es publico**, asi que no contiene
ninguna credencial. `alembic/env.py` resuelve la URL en tiempo de ejecucion:

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

Despliegue actual: Render, en <https://businext-backend.onrender.com>.

## Asistente conversacional

`POST /assistant/chat` (`src/routers/assistant.py`) recibe
`{ conversation_id?, message }` y responde en SSE con los eventos del contrato
de `businext/packages/shared-core` (`message_start`, `text_delta`,
`tool_status`, `block`, `message_end`, `error`). La logica vive en
`src/services/assistant/`: `llm.py` (OpenAI `gpt-4o-mini` en streaming),
`tools.py` (registro de herramientas: cada una devuelve datos para el modelo
y, opcionalmente, un bloque para la app) y `orchestrator.py` (bucle de un
turno, maximo `MAX_STEPS` llamadas al modelo). Sin `OPENAI_API_KEY` responde
503. Los tests mockean el LLM sobreescribiendo `get_assistant_llm`.

Herramientas de lectura en `src/routers/assistant_tools.py`: reservas de un
dia o rango, detalle de reserva, caja (hoy/semana/mes con el periodo
anterior), solicitudes, servicios y precios, resenas, resumen semanal y
horario. Llaman a las mismas funciones que los endpoints, asi que aplican sus
reglas de acceso. «Hoy» se calcula en `Europe/Madrid`.

Herramientas de escritura en `src/routers/assistant_actions.py` (crear,
mover, cancelar, completar y revertir reservas; aceptar o rechazar
solicitudes; registrar ingresos o gastos). No escriben: guardan una accion
pendiente (`src/services/assistant/actions.py`, en memoria, caduca a los 15
min) y devuelven un bloque `confirmation`. Solo
`POST /assistant/actions/{action_id}/confirm` con `decision: "confirm"` la
ejecuta, una vez, llamando a la funcion del endpoint correspondiente; el
resultado o el error de negocio vuelve en SSE. Un reinicio del proceso
invalida las acciones pendientes.

Historial (`src/services/assistant/history.py`): cada turno se guarda en
`assistantconversation`/`assistantmessage`, aislado por negocio y usuario, y
al reanudar se envian al modelo los ultimos 20 mensajes. Endpoints:
`GET /assistant/conversations`, `GET` y `DELETE /assistant/conversations/{id}`.
**Las tablas se crean a mano** con
[`docs/sql/024-assistant-conversations.sql`](docs/sql/024-assistant-conversations.sql)
(ver [Migraciones](#migraciones)); mientras no existan, el chat funciona sin
historial y esos endpoints responden 503.

Limites y observabilidad: 30 mensajes por usuario y hora y 300 por negocio y
dia (`src/services/assistant/limits.py`, en memoria; al superarlos el chat
responde con un mensaje, no con un error), 30 s por llamada a OpenAI y 60 s
por turno. Cada turno deja un log `assistant_turn` en JSON (herramientas,
latencia, tokens, coste y error; nunca el texto de los mensajes) y suma su
uso en `assistantusage`, que el owner consulta en `GET /assistant/usage?days=30`.
**La tabla se crea a mano** con
[`docs/sql/025-assistant-usage.sql`](docs/sql/025-assistant-usage.sql); sin
ella, el uso solo queda en los logs.

Permisos por rol (`Tool.roles`): el modelo solo recibe las herramientas del
rol del usuario y el orquestador lo vuelve a comprobar al ejecutar. Un
employee solo ve y gestiona sus reservas (las de su `Profile.display_name`).

| Herramienta | owner | manager | employee |
|---|---|---|---|
| reservas: consultar, crear, mover, cancelar, completar | ✅ | ✅ | solo las suyas |
| revertir reserva completada | ✅ | ❌ | ❌ |
| solicitudes: consultar, aceptar, rechazar | ✅ | ✅ | ✅ |
| servicios y precios, horario | ✅ | ✅ | ✅ |
| caja, resumen semanal, resenas | ✅ | ✅ | ❌ |
| registrar ingreso o gasto | ✅ | ✅ | ❌ |

## Decisiones de arquitectura

En [`docs/adr/`](docs/adr/). La primera,
[0001 — asistente conversacional](docs/adr/0001-asistente-llm.md): proveedor
LLM (OpenAI `gpt-4o-mini`), orquestacion y contrato del chat de la app movil.

## Generacion de tipos para el frontend

`packages/shared-core` del monorepo genera sus tipos desde el OpenAPI que
expone FastAPI en `/openapi.json` (`pnpm generate:types`, o el workflow
`generate-types.yml`). Un cambio de contrato aqui obliga a regenerarlos alli —
nunca se editan a mano.

> `src/export_schema.py` no tiene nada que ver con esto: es un script muerto de
> una epoca en la que el proyecto usaba SQLite (abre un `database.db` que ya no
> existe). Nadie lo importa; se puede borrar.
