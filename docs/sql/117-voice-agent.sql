-- Agente de voz Retell (businext-sys/businext#117). Ejecutar a mano en
-- Supabase: alembic/versions/ no esta versionado (ver README, "Migraciones").

-- Numero de Retell (E.164) -> negocio. Lo asigna Businext, no el barbero.
CREATE TABLE IF NOT EXISTS voicephonenumber (
    id SERIAL PRIMARY KEY,
    phone_number VARCHAR NOT NULL,
    business_id VARCHAR NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_voicephonenumber_phone_number ON voicephonenumber (phone_number);
CREATE INDEX IF NOT EXISTS ix_voicephonenumber_business_id ON voicephonenumber (business_id);

-- Configuracion del agente por negocio. Sin fila: desactivado, modo solicitud.
CREATE TABLE IF NOT EXISTS voiceagentconfig (
    id SERIAL PRIMARY KEY,
    business_id VARCHAR NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    booking_mode VARCHAR NOT NULL DEFAULT 'request',
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_voiceagentconfig_business_id ON voiceagentconfig (business_id);

-- Asignar un numero a un negocio (business_id = user_id del owner):
-- INSERT INTO voicephonenumber (phone_number, business_id, created_at)
-- VALUES ('+34910000000', '<business_id>', now());

-- Vuelta atras:
-- DROP TABLE IF EXISTS voiceagentconfig;
-- DROP TABLE IF EXISTS voicephonenumber;
