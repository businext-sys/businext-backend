# ADR 0002 — Agente de voz Retell multitenant

- **Estado:** aceptado
- **Fecha:** 2026-10-24
- **Story:** businext-sys/businext#117 (Fase 8, businext-sys/businext#116)

## Contexto

Cada barbería tendrá un número de teléfono atendido por un agente de voz que
informa de servicios, locales y disponibilidad y reserva citas con los mismos
datos que la página pública de reservas. La fase #116 ya decidió el proveedor
(Retell), un único agente plantilla parametrizable y funciones HTTP sobre la
API existente en lugar de un servidor MCP.

## Decisión

1. **Número → negocio en el backend.** Tabla `voicephonenumber` (la rellena
   Businext; el barbero no puede cambiarla). Los números de Retell no tienen
   agente fijo, solo `inbound_webhook_url` → `POST /retell/inbound`, que
   devuelve `override_agent_id` (`RETELL_AGENT_ID`) y los datos del negocio
   como `dynamic_variables`, o `reject: true` si el número no es de ningún
   negocio o su agente está desactivado. Si el webhook falla tres veces,
   Retell cuelga: nunca se atiende una llamada sin negocio.
2. **El modelo nunca elige el negocio.** Las funciones
   (`POST /retell/functions/{name}`) toman el negocio de
   `call.retell_llm_dynamic_variables.business_id`, que fijó el webhook, y
   comprueban que el `to_number` de la llamada es de ese negocio y que su
   agente sigue activo. Los argumentos del modelo no llevan `business_id`.
3. **Firma obligatoria.** Webhook y funciones verifican `X-Retell-Signature`
   (HMAC-SHA256 del cuerpo crudo + timestamp con `RETELL_API_KEY`, máximo
   5 min de antigüedad). Sin `RETELL_API_KEY` todo responde 401.
4. **Modo de reserva por negocio** (`voiceagentconfig.booking_mode`):
   - `request` (por defecto): llama a `create_booking_request` de la reserva
     pública, con sus validaciones, push al owner y emails.
   - `confirm`: crea la `Reservation` directamente, igual que la crea aceptar
     una solicitud (`PENDING`, 30 min), y envía al cliente el email de reserva
     confirmada. No pasa por `BookingRequest`.

   En los dos modos la hora tiene que estar entre los huecos libres de
   `get_available_slots`: sin un humano que revise, el modelo no puede
   reservar una hora inventada ni ocupada. Si al cliente le da igual el
   profesional, la cita se asigna al del primer hueco libre, para que ocupe su
   agenda.
5. **Agente plantilla versionado** en `scripts/retell/agent_template.json`
   (prompt, primer mensaje y herramientas); `scripts/retell/sync.py` lo crea o
   actualiza en Retell y conecta números al webhook.

## Consecuencias

- El email del cliente es obligatorio para reservar, como en la web: el
  agente pide que lo deletree. Si en la práctica falla mucho por voz, habría
  que permitir solicitudes sin email (la tabla y el control de duplicados
  dependen de él).
- En modo `confirm` el owner no recibe push: la cita aparece en la agenda.
- No se comprueba la suscripción del negocio al recibir la llamada; si el
  agente se cobra aparte, esa regla irá en el webhook.
- Las tablas se crean a mano (`docs/sql/117-voice-agent.sql`).
