-- Tokens de Expo Push de la app movil (issue #031 de businext-sys/businext).
-- Ejecutar a mano en Supabase: alembic/versions/ no esta versionado (ver
-- README, "Migraciones").
--
-- El modelo PushToken no define __tablename__, asi que SQLModel usa el
-- nombre de la clase en minusculas: la tabla es "pushtoken", no "push_token".

CREATE TABLE IF NOT EXISTS pushtoken (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR NOT NULL,
    token VARCHAR NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_pushtoken_user_id ON pushtoken (user_id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_pushtoken_token ON pushtoken (token);

-- Vuelta atras:
-- DROP TABLE IF EXISTS pushtoken;
