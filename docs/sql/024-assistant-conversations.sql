-- Historial del asistente (businext-backend#24). Ejecutar a mano en Supabase:
-- alembic/versions/ no esta versionado (ver README, "Migraciones").

CREATE TABLE IF NOT EXISTS assistantconversation (
    id VARCHAR PRIMARY KEY,
    business_id VARCHAR NOT NULL,
    user_id VARCHAR NOT NULL,
    title VARCHAR NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_assistantconversation_business_id ON assistantconversation (business_id);
CREATE INDEX IF NOT EXISTS ix_assistantconversation_user_id ON assistantconversation (user_id);
CREATE INDEX IF NOT EXISTS ix_assistantconversation_updated_at ON assistantconversation (updated_at);

CREATE TABLE IF NOT EXISTS assistantmessage (
    id VARCHAR PRIMARY KEY,
    conversation_id VARCHAR NOT NULL REFERENCES assistantconversation (id) ON DELETE CASCADE,
    role VARCHAR NOT NULL,
    blocks VARCHAR NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_assistantmessage_conversation_id ON assistantmessage (conversation_id);
CREATE INDEX IF NOT EXISTS ix_assistantmessage_created_at ON assistantmessage (created_at);

-- Vuelta atras:
-- DROP TABLE IF EXISTS assistantmessage;
-- DROP TABLE IF EXISTS assistantconversation;
