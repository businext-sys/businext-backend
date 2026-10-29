-- Registro de llamadas del agente de voz (pestaña «Agente de voz» de la web).
-- Ejecutar a mano en Supabase: alembic/versions/ no esta versionado (ver
-- README, "Migraciones"). Tras crearla, re-sincronizar el agente para que
-- Retell envie los eventos a /retell/webhook:
--   python scripts/retell/sync.py agent --base-url https://businext-backend.onrender.com

CREATE TABLE IF NOT EXISTS voicecall (
    id SERIAL PRIMARY KEY,
    call_id VARCHAR NOT NULL,
    business_id VARCHAR NOT NULL,
    from_number VARCHAR,
    to_number VARCHAR,
    status VARCHAR NOT NULL DEFAULT 'in_progress',
    started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    ended_at TIMESTAMP WITHOUT TIME ZONE,
    duration_seconds INTEGER,
    disconnection_reason VARCHAR,
    summary VARCHAR,
    sentiment VARCHAR,
    successful BOOLEAN,
    transcript VARCHAR,
    recording_url VARCHAR,
    booking_mode VARCHAR,
    booking_id INTEGER,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_voicecall_call_id ON voicecall (call_id);
CREATE INDEX IF NOT EXISTS ix_voicecall_business_id ON voicecall (business_id);
CREATE INDEX IF NOT EXISTS ix_voicecall_started_at ON voicecall (started_at);

-- Vuelta atras:
-- DROP TABLE IF EXISTS voicecall;
