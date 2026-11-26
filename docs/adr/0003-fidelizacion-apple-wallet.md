# ADR 0003 — Tarjeta de fidelidad con pase de Apple Wallet

- **Estado:** propuesto (MVP del backend en `feat/loyalty-wallet`)
- **Fecha:** 2026-11-26
- **Story:** pendiente de issue

## Contexto

El barbero quiere una tarjeta de sellos: N sellos (10 por defecto) dan un
premio («corte gratis»), y cada reserva **completada** del cliente suma uno.
El cliente la lleva en Apple Wallet (Google Wallet vendrá después): un
`storeCard` con el nombre y los colores de la barbería, los sellos, la
próxima cita, sus últimas visitas y enlaces a «Reservar cita» y «Cómo
llegar», y la ubicación del local para que iOS lo sugiera cerca.

No hay tabla de clientes: la app los deduce de las reservas por nombre
(`customer_service`), y el estado fiel / en riesgo / nuevo se calcula en el
cliente (`shared-core/intelligence/clientScoring.ts`) sin guardarse. No hay
nada que reutilizar para los sellos más allá de esa forma de identificar al
cliente.

## Decisión

1. **Los datos viven en Businext; el pase es una vista.** Cuatro tablas
   (`docs/sql/loyalty-wallet.sql`, a mano como el resto: `alembic/versions/`
   no está versionado):
   - `loyaltyprogram`: una fila por negocio — `enabled`, `stamps_required`
     (1–50), `reward` (≤ 40), colores `#RRGGBB` opcionales del pase (sin
     ellos, los de `DESIGN.md`). Sin fila = desactivado.
   - `loyaltycard`: una por negocio y **email en minúsculas**. Guarda el
     `serial_number` y el `auth_token` del pase y `updated_at`, la marca que
     usan `passesUpdatedSince` y `Last-Modified`.
   - `loyaltystamp`: un sello por reserva (`reservation_id` único) con fecha,
     servicio y `redeemed_at`. Sellos actuales = sin canjear.
   - `walletregistration`: dispositivo + pass type + serial + push token
     (protocolo de PassKit).
   - Y `location.latitude/longitude` opcionales para las `locations` del pase.
2. **Cliente = email.** Es lo que pide la reserva pública y lo que permite
   entregar el pase. Una reserva sin email (walk-in) usa el email más
   reciente de las reservas con el mismo nombre (`known_contact`); sin
   ninguno, no sella.
3. **Sellado en un único punto, idempotente.** `loyalty_service.sync_reservation`
   se llama tras crear, cambiar, borrar o revertir una reserva (routers de
   reservas, aceptar solicitud y agente de voz; el asistente pasa por el
   router de reservas). Si la reserva está `COMPLETED` y el programa activo,
   crea la tarjeta si falta y añade el sello (no hace nada si ya existe); si
   deja de estarlo (revertir), quita el sello salvo que ya se canjeara. En
   todo caso marca la tarjeta como cambiada (la próxima cita puede haber
   cambiado) y avisa a sus dispositivos. Nunca rompe la petición.
4. **Canje explícito.** `POST /loyalty/cards/{serial}/redeem` gasta los N
   sellos más antiguos. Los sellos de más se conservan.
5. **Firma en el backend, sin dependencias de terceros para PassKit.**
   `cryptography` (PKCS#7 separada, SHA-256, con el WWDR incluido) y
   `zipfile`. El certificado del Pass Type ID, su clave y el WWDR vienen de
   variables de entorno (PEM en línea o ruta a fichero). Si falta algo, las
   rutas de Wallet responden **503** con los nombres de las variables que
   faltan y el resto de la app sigue igual.
6. **Actualización:** cambio en la tarjeta → `updated_at` avanza al menos un
   segundo entero → push vacío por APNs (HTTP/2 con el certificado del pase,
   `apns-topic` = Pass Type ID, en un hilo) a cada push token registrado →
   Wallet pide `GET .../registrations/...?passesUpdatedSince=` y luego
   `GET .../passes/...` y recibe el `.pkpass` regenerado. Los tokens que APNs
   da por muertos (410) se borran. Cambiar la regla o los colores regenera
   todos los pases del negocio.
7. **Rutas** (`src/routers/loyalty.py`):

   | Ruta | Auth | Qué hace |
   |------|------|----------|
   | `GET /loyalty/program` | cualquier rol | regla + `apple_wallet_available` |
   | `PUT /loyalty/program` | owner | activa y fija regla y colores |
   | `GET /loyalty/cards?email=\|name=` | cualquier rol | tarjeta de un cliente + `pass_url` |
   | `POST /loyalty/cards` | cualquier rol | crea/devuelve la tarjeta (enlace para el cliente en el local) |
   | `POST /loyalty/cards/{serial}/redeem` | cualquier rol | entrega el premio |
   | `GET /public/loyalty/{business_id}` | — | si la página de reservas enseña la tarjeta y el botón |
   | `GET /public/loyalty/passes/{serial}.pkpass?token=` | token del pase | descarga del pase |
   | `POST/DELETE /wallet/apple/v1/devices/{device}/registrations/{passType}/{serial}` | `ApplePass <token>` | alta/baja del dispositivo |
   | `GET /wallet/apple/v1/devices/{device}/registrations/{passType}` | — (lo define Apple) | seriales cambiados |
   | `GET /wallet/apple/v1/passes/{passType}/{serial}` | `ApplePass <token>` | último `.pkpass` (304 con `If-Modified-Since`) |
   | `POST /wallet/apple/v1/log` | — | errores que reporta Wallet, al log |

   `POST /public/book/{id}/request` devuelve además `wallet_pass_url` (o
   `null`) para el botón «Añadir a Apple Wallet» de la confirmación.
8. **Sitio para Google Wallet.** Nada del modelo es de Apple salvo
   `walletregistration` y `auth_token`. Google usa la API de objetos
   (`loyaltyObject` actualizado por REST, sin registros de dispositivos), así
   que irá en `src/services/google_wallet.py` + `/wallet/google/...`, llamado
   desde el mismo `notify_devices`.

## Contenido del pase

- Frontal: `SELLOS 3/10` (primario), `PRÓXIMA CITA` (fecha con zona de
  Madrid, o «Sin cita») y `CLIENTE` (secundarios), `PREMIO` («Corte gratis:
  te faltan 7» / «¡Corte gratis disponible!»). `relevantDate` = próxima cita.
- Dorso: últimas 5 visitas selladas, «Reservar cita» (`APP_URL/book/{id}`),
  «Cómo llegar» (`maps_link` del primer local, o Apple Maps con sus
  coordenadas o su dirección) y la regla.
- `locations`: hasta 10 locales activos con coordenadas.
- Imágenes: `src/assets/wallet/` (icono de Businext) para todos los negocios.
  Sin `strip` de sellos todavía.

## Consecuencias

- Hay que crear las tablas y columnas antes de desplegar; sin las columnas de
  `location`, fallan las rutas que leen locales.
- Cambiar el nombre del negocio o sus locales no regenera los pases hasta el
  siguiente cambio de la tarjeta.
- El enlace del pase lleva el mismo token que autentica el web service: quien
  tenga el enlace ve la tarjeta (nombre, sellos, próxima cita) y puede
  registrar un dispositivo para recibir sus actualizaciones. No permite
  modificar nada.

## Preguntas abiertas

1. **Entrega del enlace.** Hoy la respuesta de la solicitud pública trae
   `wallet_pass_url`, así que quien haga una solicitud con el email de otro
   cliente obtiene su tarjeta. ¿Basta, o el enlace solo debe ir en el email de
   confirmación (prueba de que el email es suyo)?
2. **¿El email es la identidad correcta?** Los walk-ins sin email no suman
   salvo que su nombre coincida con reservas que sí lo tienen. ¿Pedir el
   email al completar, o identificar también por teléfono?
3. **¿Se sellan las visitas anteriores a activar la tarjeta?** Ahora no (solo
   completadas después). Se podría ofrecer un «importar visitas» único.
4. **Colores y logo por negocio.** No hay colores ni logo del negocio en la
   base de datos; el barbero elige colores en la tarjeta y el logo es el de
   Businext. ¿Subir un logo por negocio (Supabase Storage) para el pase?
5. **Canje:** ¿lo hace cualquier miembro del equipo o solo owner/manager?
   ¿Caduca el premio o los sellos?
6. **¿Código QR/barcode en el pase** para identificar al cliente en el local?
7. **Pass Type ID único de Businext** (`pass.<dominio>.loyalty`, todas las
   barberías firmadas por la cuenta de Businext) — es lo asumido aquí.
