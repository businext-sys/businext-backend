# ADR 0001 — Proveedor LLM, orquestación y contrato del asistente

- **Estado:** aceptado
- **Fecha:** 2026-10-23
- **Story:** businext-sys/businext-backend#19 (Fase 7, app móvil conversacional)

## Contexto

La Fase 7 convierte la app móvil en un chat: el usuario pregunta («¿qué tengo
hoy?», «cancélame la de las 5») y el asistente consulta o modifica datos del
negocio con herramientas, respondiendo en streaming con texto y tarjetas.

Necesitamos un modelo con *tool calling* fiable en español coloquial, streaming
de texto y de llamadas a herramientas, latencia baja (es la home de la app) y
coste por conversación asumible para negocios pequeños. El backend ya usa
OpenAI `gpt-4o-mini` en `src/services/ai_service.py` (respuestas a reseñas,
resúmenes), con `openai==1.82.0` y `OPENAI_API_KEY` configurada en Render.

## Decisión

1. **Proveedor y modelo: OpenAI `gpt-4o-mini`**, vía Chat Completions con
   `tools` y `stream=True`. El nombre del modelo va en una constante del
   servicio del asistente para poder cambiarlo sin tocar la orquestación.
2. **Orquestación propia**, sin framework de agentes, en
   `src/services/assistant/`:
   - Registro de herramientas: cada una declara nombre, descripción, esquema de
     parámetros, roles permitidos (#23) y si escribe (#22). Las de lectura
     llaman a los servicios/queries existentes y devuelven datos + el bloque
     rico del contrato.
   - Bucle por turno: mensajes → modelo en streaming → si pide herramientas, se
     ejecutan (emitiendo `tool_status`) y se devuelve su resultado al modelo →
     hasta que responde con texto. Límite de iteraciones por turno.
   - Las herramientas de escritura nunca se ejecutan en el bucle: crean una
     acción pendiente y emiten un bloque `confirmation`; solo
     `POST /assistant/actions/{action_id}/confirm` las ejecuta
     (businext-sys/businext#99).
   - El router `src/routers/assistant.py` traduce el bucle a eventos SSE.
3. **Contrato:** el implementado en la app, en
   `businext/packages/shared-core/src/schemas/assistant.ts` y documentado en
   `businext/packages/shared-core/README.md` («Contrato del asistente
   conversacional»). Resumen:
   - Mensaje `{ id, conversation_id, role: user|assistant, blocks, created_at }`
     (snake_case).
   - Bloques: `text`, `reservation_list`, `cash_summary`, `booking_request`,
     `confirmation`, `quick_replies`; todos admiten `fallback_text`, que la app
     muestra si no conoce el tipo.
   - Eventos SSE: `message_start`, `text_delta`, `tool_status`, `block`,
     `message_end`, `error`.
   - Endpoints: `POST /assistant/chat`, `POST
     /assistant/actions/{action_id}/confirm`, `GET /assistant/conversations`,
     `GET`/`DELETE /assistant/conversations/{id}`.

   Un cambio de contrato se hace primero en shared-core y luego aquí; los tipos
   del OpenAPI se regeneran en `businext` con `pnpm generate:types`.

## Alternativas descartadas

- **Claude (Anthropic).** Buen *tool use* en español, pero supone un segundo
  proveedor, otra clave y otro SDK para el equipo, y más coste por token que
  `gpt-4o-mini`. Se puede reevaluar con los evals (#26) si la precisión de
  herramientas no llega.
- **Un modelo mayor de OpenAI (`gpt-4o`, `gpt-4.1`).** Más caro y más lento
  para consultas que son, en su mayoría, una herramienta y una frase. Queda
  como opción si los evals lo justifican: el modelo es una constante.
- **Frameworks de agentes (LangChain, OpenAI Assistants/Agents).** Añaden
  dependencias y estado fuera de nuestra base de datos para un bucle que son
  pocas decenas de líneas, y dificultan aplicar los permisos por rol y la
  confirmación de escrituras exactamente donde queremos.

## Coste estimado

Precios de `gpt-4o-mini` a la fecha de este ADR: **0,15 $ / 1M tokens de
entrada** y **0,60 $ / 1M de salida** (revisar en <https://openai.com/api/pricing>
antes de lanzar).

Conversación típica: 4 turnos del usuario, cada uno con ~1 llamada a
herramienta (2 llamadas al modelo por turno).

| | Tokens por llamada | Llamadas | Total |
|---|---|---|---|
| Entrada (sistema + herramientas ~1.500, historial y resultados ~1.500) | ~3.000 | 8 | ~24.000 |
| Salida | ~150 | 8 | ~1.200 |

Coste ≈ 24.000 × 0,15/1M + 1.200 × 0,60/1M ≈ **0,004 $ por conversación**.
Un negocio con 20 conversaciones al día gasta ~0,08 $/día (~2,5 $/mes). El
recorte de historial (#24) y los límites de uso (#25) acotan los casos
extremos.

## Consecuencias

- Un único proveedor y clave (`OPENAI_API_KEY`) para toda la IA del backend.
- El streaming SSE debe atravesar Render sin buffering (cabeceras
  `Cache-Control: no-cache` y `X-Accel-Buffering: no`), comprobado en #20.
- Los tests no llaman a OpenAI: el cliente se inyecta y se mockea; el modo
  mock determinista para E2E es businext-sys/businext#101.
