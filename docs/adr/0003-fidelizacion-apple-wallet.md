# ADR 0003 — Tarjeta de fidelidad con pase de Apple Wallet

- **Estado:** aceptado (backend en `feat/loyalty-wallet`; decisiones del 2026-11-26)
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
     ellos, los de `DESIGN.md`), `enabled_at` (primera activación) y
     `logo_url` (logo propio, opcional). Sin fila = desactivado.
   - `loyaltycard`: una por negocio y **email en minúsculas**. Guarda el
     `serial_number` y el `auth_token` del pase, el `scan_token` del QR y
     `updated_at`, la marca que usan `passesUpdatedSince` y `Last-Modified`.
   - `loyaltystamp`: un sello por visita con fecha, servicio y `redeemed_at`.
     De reserva: `reservation_id` único. Manual (walk-in escaneado):
     `reservation_id` nulo y `manual_day`, único por tarjeta. Sellos
     actuales = sin canjear.
   - `walletregistration`: dispositivo + pass type + serial + push token
     (protocolo de PassKit).
   - Y `location.latitude/longitude` opcionales para las `locations` del pase.
2. **Cliente = email.** Es lo que pide la reserva pública y lo que permite
   entregar el pase. Una reserva sin email (walk-in) usa, por este orden:
   el email de la reserva más reciente del negocio con el **mismo teléfono**
   (normalizado: solo dígitos, los 9 últimos, así `+34 600 00 00 01` y
   `600-000-001` coinciden; `customer_service.email_by_phone`) y, si no hay,
   el de la más reciente con el mismo nombre (`known_contact`). Sin ninguno,
   no sella (salvo que el barbero escanee su QR).
3. **Sellado en un único punto, idempotente.** `loyalty_service.sync_reservation`
   se llama tras crear, cambiar, borrar o revertir una reserva (routers de
   reservas, aceptar solicitud y agente de voz; el asistente pasa por el
   router de reservas). Si la reserva está `COMPLETED` y el programa activo,
   crea la tarjeta si falta y añade el sello (no hace nada si ya existe); si
   deja de estarlo (revertir), quita el sello salvo que ya se canjeara. En
   todo caso marca la tarjeta como cambiada (la próxima cita puede haber
   cambiado) y avisa a sus dispositivos. Nunca rompe la petición.
   - **Las visitas de antes de activar no cuentan:** solo sellan las reservas
     desde el inicio (hora de Madrid) del día de `enabled_at`, que se fija la
     primera vez que el owner activa la tarjeta y no cambia al desactivar y
     reactivar.
   - **Sello manual (walk-in):** el barbero escanea el QR del pase y
     `POST /loyalty/scan/{token}/stamp` suma un sello sin reserva. Uno al día
     como mucho (día de Madrid): si la tarjeta ya tiene un sello de hoy, de
     reserva o manual, no suma. Si después se completa una reserva de ese
     mismo día, es la misma visita: el sello manual pasa a ser el de la
     reserva; revertirla lo devuelve a manual en vez de borrarlo.
4. **Canje explícito, cualquier rol.** `POST /loyalty/cards/{serial}/redeem`
   gasta los N sellos más antiguos. Los sellos de más se conservan. Los sellos
   no caducan.
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
   | `POST /loyalty/cards/{serial}/send-pass` | cualquier rol | (re)envía el enlace del pase al email del cliente |
   | `GET /loyalty/scan/{token}` | cualquier rol | tarjeta del QR (404 si es de otro negocio) + `stamped_today` |
   | `POST /loyalty/scan/{token}/stamp` | cualquier rol | sello manual de un walk-in, uno al día |
   | `PUT /loyalty/program/logo` | owner | sube el logo del pase (multipart `file`) |
   | `DELETE /loyalty/program/logo` | owner | vuelve al logo de Businext |
   | `GET /public/loyalty/{business_id}` | — | si la página de reservas enseña la tarjeta y el botón |
   | `GET /public/loyalty/passes/{serial}.pkpass?token=` | token del pase | descarga del pase |
   | `POST/DELETE /wallet/apple/v1/devices/{device}/registrations/{passType}/{serial}` | `ApplePass <token>` | alta/baja del dispositivo |
   | `GET /wallet/apple/v1/devices/{device}/registrations/{passType}` | — (lo define Apple) | seriales cambiados |
   | `GET /wallet/apple/v1/passes/{passType}/{serial}` | `ApplePass <token>` | último `.pkpass` (304 con `If-Modified-Since`) |
   | `POST /wallet/apple/v1/log` | — | errores que reporta Wallet, al log |

   **El enlace del pase solo sale por email** (prueba de que el email es del
   cliente): `POST /public/book/{id}/request` **no** lo devuelve, porque
   cualquiera puede pedir cita con el email de otro. Va como botón «Añadir a
   Apple Wallet» (texto oficial de la insignia de Apple en español) en el
   email de solicitud recibida y en el de reserva confirmada, si el negocio
   tiene la tarjeta activa y Wallet está configurado. El barbero puede
   reenviarlo con `send-pass`; las respuestas autenticadas (`/loyalty/cards`,
   `/loyalty/scan`) sí incluyen `pass_url`, para enseñarlo en el local.
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
- `barcodes`: un QR (`PKBarcodeFormatQR`) cuyo mensaje es el `scan_token` de
  la tarjeta: opaco, sin el email y distinto del `auth_token` (que da acceso
  al pase). Sin `altText`.
- Imágenes: el logo del negocio si lo subió; si no, o si no se puede
  descargar, el de Businext (`src/assets/wallet/`). Sin `strip` de sellos
  todavía.

## Logo por negocio

- El owner sube un PNG, JPG o WebP (≤ 2 MB) con `PUT /loyalty/program/logo`.
  Pillow le quita el margen transparente y genera los tamaños de Apple:
  `logo.png` (cabe en 160×50) y `logo@2x.png` (320×100), sin deformar; e
  `icon.png`, `@2x` y `@3x` (29, 58 y 87 px), cuadrados con el logo centrado
  sobre transparente.
- Se suben a **Supabase Storage** por la API REST con la service role key
  (como las invitaciones de empleados en `employee_service`), al bucket
  público `LOYALTY_LOGO_BUCKET` (`loyalty-logos` por defecto) en
  `{business_id}/{uuid}/`: una carpeta nueva por subida, así las URLs no
  cambian de contenido y se cachean. `logo_url` guarda la de `logo@2x.png`
  (sirve de vista previa en la app). La carpeta anterior se borra (mejor
  esfuerzo) y todos los pases del negocio se regeneran.
- Al generar un pase se descargan los cinco PNG (timeout de 5 s) con una
  caché en memoria; si falla, el pase lleva el logo de Businext.
- Bucket a crear a mano (ver README). Público porque solo contiene logos.

## Consecuencias

- Hay que crear las tablas y columnas antes de desplegar; sin las columnas de
  `location`, fallan las rutas que leen locales.
- Cambiar el nombre del negocio o sus locales no regenera los pases hasta el
  siguiente cambio de la tarjeta.
- El enlace del pase lleva el mismo token que autentica el web service: quien
  tenga el enlace ve la tarjeta (nombre, sellos, próxima cita) y puede
  registrar un dispositivo para recibir sus actualizaciones. No permite
  modificar nada. Por eso solo se entrega por email.
- El QR solo identifica la tarjeta ante un miembro autenticado del mismo
  negocio; a cualquier otro le da 404. Una foto del QR permite a otro cliente
  hacerse pasar por el titular en el local (como una tarjeta de cartón).
- La tarjeta se crea al pedir cita (para poder mandar el enlace en el email).
  Si la solicitud se rechaza o caduca, o se borra la reserva, y la tarjeta
  quedó vacía, se borra (`loyalty_service.discard_empty_card`): sin ningún
  sello (tampoco canjeado), sin dispositivos en `walletregistration` y sin
  otra solicitud o reserva pendiente con ese email (su enlace sigue
  sirviendo). El enlace que ya recibió el cliente da entonces 404; si vuelve,
  se le crea una tarjeta nueva.
- Con logo propio el pase no lleva `logoText` (el logo ya identifica al
  negocio y suele incluir el nombre). Sin logo, o si no se pudo descargar y
  sale el de Businext, `logoText` es el nombre del negocio.

## Decisiones del 2026-11-26

1. **Entrega del enlace:** solo en el email de confirmación, nunca en la
   respuesta HTTP de una reserva pública. Hecho.
2. **Visitas anteriores a activar la tarjeta:** no cuentan. Hecho
   (`enabled_at`).
3. **Logo:** cada barbería sube el suyo si quiere; si no, el de Businext.
   Hecho.
4. **Canje:** cualquier rol. Los sellos no caducan.
5. **QR en el pase:** sí, con un token opaco. Hecho, con sello manual para
   walk-ins.
6. **Tarjetas vacías:** se borran al rechazar, caducar o borrar la solicitud
   o reserva (ver Consecuencias). Hecho.
7. **`logoText`:** solo sin logo propio. Hecho.
8. **Identidad:** el email sigue siendo la clave; sin email, primero por
   teléfono y luego por nombre. Hecho.

## Preguntas abiertas

1. **Pass Type ID único de Businext** (`pass.<dominio>.loyalty`, todas las
   barberías firmadas por la cuenta de Businext) — es lo asumido aquí.
