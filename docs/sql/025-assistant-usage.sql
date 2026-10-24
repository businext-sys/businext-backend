-- Uso y coste diario del asistente (businext-backend#25). Ejecutar a mano en
-- Supabase: alembic/versions/ no esta versionado (ver README, "Migraciones").

CREATE TABLE IF NOT EXISTS assistantusage (
    id SERIAL PRIMARY KEY,
    business_id VARCHAR NOT NULL,
    day DATE NOT NULL,
    turns INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
    CONSTRAINT uq_assistant_usage_business_day UNIQUE (business_id, day)
);
CREATE INDEX IF NOT EXISTS ix_assistantusage_business_id ON assistantusage (business_id);
CREATE INDEX IF NOT EXISTS ix_assistantusage_day ON assistantusage (day);

-- Vuelta atras:
-- DROP TABLE IF EXISTS assistantusage;
