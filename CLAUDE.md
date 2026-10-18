# businext-backend

API de Businext: FastAPI + SQLModel sobre Postgres de Supabase, desplegada en Render. Setup,
variables y comandos en `README.md`.

## Proceso de contribución

Este repo sigue el proceso de `businext-sys/businext-apm` (`docs/contributing-standard.md`),
instalado con `apm install` en `.claude/` (no se commitea: cada persona lo instala): `/story` → `/implement <n>` → `/verify <n>`, con las
stories en el tablero Businext System Build y la etiqueta `repo:businext-backend`.

- Ramas `<tipo>/<NNN>-<slug>` (`feat/059-agenda-semanal`); commits y PRs con
  `Refs businext-sys/businext#<n>`.
- Validación antes del PR: `ruff check src/` y `pytest tests/` (lo mismo que `Backend CI`).
- Migraciones: `alembic upgrade head` no funciona (las versiones no están en git y la BD está en
  `014`). Las tablas nuevas se crean a mano en Supabase, con el SQL en el PR.
- Un endpoint nuevo o cambiado obliga a regenerar los tipos de `businext/packages/shared-core`.
- Actualizar el toolkit: `apm update --yes` y commitear `apm.lock.yaml`.
