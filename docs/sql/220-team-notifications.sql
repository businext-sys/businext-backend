-- Avisos push al equipo, estado «no vino» y duracion por servicio (businext-sys/businext#220).
-- Ejecutar a mano en Supabase antes de desplegar: alembic/versions/ no esta
-- versionado (ver README, "Migraciones").

CREATE TABLE IF NOT EXISTS notificationpreferences (
    user_id VARCHAR PRIMARY KEY,
    upcoming BOOLEAN NOT NULL DEFAULT TRUE,
    upcoming_minutes INTEGER NOT NULL DEFAULT 15,
    daily_summary BOOLEAN NOT NULL DEFAULT TRUE,
    daily_summary_hour INTEGER NOT NULL DEFAULT 9,
    reservation_changes BOOLEAN NOT NULL DEFAULT TRUE,
    day_close BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS notificationlog (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR NOT NULL,
    kind VARCHAR NOT NULL,
    key VARCHAR NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
    UNIQUE (user_id, kind, key)
);

ALTER TABLE notificationpreferences ENABLE ROW LEVEL SECURITY;
ALTER TABLE notificationlog ENABLE ROW LEVEL SECURITY;

-- Duracion de cada servicio; las filas existentes quedan en 30 min, como hasta ahora.
ALTER TABLE product ADD COLUMN IF NOT EXISTS duration_minutes INTEGER DEFAULT 30;
UPDATE product SET duration_minutes = 30 WHERE duration_minutes IS NULL;

-- El estado «no vino» (reservation.status = 'NO_SHOW') no necesita SQL: status es VARCHAR.

-- Vuelta atras:
-- ALTER TABLE product DROP COLUMN IF EXISTS duration_minutes;
-- DROP TABLE IF EXISTS notificationlog;
-- DROP TABLE IF EXISTS notificationpreferences;
