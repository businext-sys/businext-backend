-- Tarjeta de fidelidad (sellos) + pase de Apple Wallet (docs/adr/0003-fidelizacion-apple-wallet.md).
-- Ejecutar a mano en Supabase antes de desplegar: alembic/versions/ no esta
-- versionado (ver README, "Migraciones"). Sin estas tablas fallan las rutas
-- /loyalty, /public/loyalty y /wallet/apple, y sin las columnas de `location`
-- fallan todas las rutas que leen locales (reservas publicas incluidas).
--
-- Generado desde los modelos de src/database/models/loyalty_model.py
-- (los modelos no definen __tablename__).
--
-- Idempotente: se puede ejecutar entero otra vez. Los ALTER ... IF NOT EXISTS de
-- abajo completan las tablas creadas con la primera version de este fichero
-- (sin QR, sellos manuales, logo ni enabled_at).

CREATE TABLE IF NOT EXISTS loyaltyprogram (
    id SERIAL PRIMARY KEY,
    business_id VARCHAR NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    stamps_required INTEGER NOT NULL DEFAULT 10,
    reward VARCHAR(40) NOT NULL DEFAULT 'Corte gratis',
    background_color VARCHAR,
    foreground_color VARCHAR,
    label_color VARCHAR,
    enabled_at TIMESTAMP WITHOUT TIME ZONE,
    logo_url VARCHAR,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_loyaltyprogram_business_id ON loyaltyprogram (business_id);

CREATE TABLE IF NOT EXISTS loyaltycard (
    id SERIAL PRIMARY KEY,
    business_id VARCHAR NOT NULL,
    customer_email VARCHAR NOT NULL,
    customer_name VARCHAR NOT NULL,
    serial_number VARCHAR NOT NULL,
    auth_token VARCHAR NOT NULL,
    scan_token VARCHAR NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    UNIQUE (business_id, customer_email)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_loyaltycard_serial_number ON loyaltycard (serial_number);
CREATE INDEX IF NOT EXISTS ix_loyaltycard_business_id ON loyaltycard (business_id);
CREATE INDEX IF NOT EXISTS ix_loyaltycard_updated_at ON loyaltycard (updated_at);

-- reservation_id unico: un sello por reserva completada (sellado idempotente).
-- Sello manual (walk-in escaneado): reservation_id NULL y manual_day, unico por
-- tarjeta y dia (los NULL no chocan entre si).
CREATE TABLE IF NOT EXISTS loyaltystamp (
    id SERIAL PRIMARY KEY,
    card_id INTEGER NOT NULL REFERENCES loyaltycard (id) ON DELETE CASCADE,
    reservation_id INTEGER UNIQUE,
    manual_day DATE,
    visit_date TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    service VARCHAR NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    redeemed_at TIMESTAMP WITHOUT TIME ZONE
);
CREATE INDEX IF NOT EXISTS ix_loyaltystamp_card_id ON loyaltystamp (card_id);

CREATE TABLE IF NOT EXISTS walletregistration (
    id SERIAL PRIMARY KEY,
    device_library_identifier VARCHAR NOT NULL,
    pass_type_identifier VARCHAR NOT NULL,
    serial_number VARCHAR NOT NULL,
    push_token VARCHAR NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    UNIQUE (device_library_identifier, pass_type_identifier, serial_number)
);
CREATE INDEX IF NOT EXISTS ix_walletregistration_serial_number ON walletregistration (serial_number);
CREATE INDEX IF NOT EXISTS ix_walletregistration_device_library_identifier ON walletregistration (device_library_identifier);

-- Tablas creadas con la primera version de este fichero -----------------------

-- Fecha de la primera activacion: las visitas de antes no sellan. Los programas
-- ya activos toman su ultima actualizacion.
ALTER TABLE loyaltyprogram ADD COLUMN IF NOT EXISTS enabled_at TIMESTAMP WITHOUT TIME ZONE;
UPDATE loyaltyprogram SET enabled_at = updated_at WHERE enabled AND enabled_at IS NULL;

-- Logo propio del pase (Supabase Storage, bucket publico loyalty-logos): URL de logo@2x.png.
ALTER TABLE loyaltyprogram ADD COLUMN IF NOT EXISTS logo_url VARCHAR;

-- Token opaco del QR del pase (no es el email ni el auth_token).
ALTER TABLE loyaltycard ADD COLUMN IF NOT EXISTS scan_token VARCHAR;
UPDATE loyaltycard SET scan_token = replace(gen_random_uuid()::text, '-', '') WHERE scan_token IS NULL;
ALTER TABLE loyaltycard ALTER COLUMN scan_token SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ix_loyaltycard_scan_token ON loyaltycard (scan_token);

-- Sellos manuales: sin reserva, uno por tarjeta y dia.
ALTER TABLE loyaltystamp ALTER COLUMN reservation_id DROP NOT NULL;
ALTER TABLE loyaltystamp ADD COLUMN IF NOT EXISTS manual_day DATE;
CREATE UNIQUE INDEX IF NOT EXISTS uq_loyaltystamp_card_manual_day ON loyaltystamp (card_id, manual_day);

-- Coordenadas de cada local, para que iOS sugiera el pase cerca.
ALTER TABLE location ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION;
ALTER TABLE location ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION;

ALTER TABLE loyaltyprogram ENABLE ROW LEVEL SECURITY;
ALTER TABLE loyaltycard ENABLE ROW LEVEL SECURITY;
ALTER TABLE loyaltystamp ENABLE ROW LEVEL SECURITY;
ALTER TABLE walletregistration ENABLE ROW LEVEL SECURITY;

-- Vuelta atras:
-- DROP TABLE IF EXISTS walletregistration;
-- DROP TABLE IF EXISTS loyaltystamp;
-- DROP TABLE IF EXISTS loyaltycard;
-- DROP TABLE IF EXISTS loyaltyprogram;
-- ALTER TABLE location DROP COLUMN IF EXISTS latitude;
-- ALTER TABLE location DROP COLUMN IF EXISTS longitude;
