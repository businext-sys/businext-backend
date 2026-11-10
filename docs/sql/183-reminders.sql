-- Recordatorio por email el dia antes de la cita (issue #183 de businext-sys/businext).
-- Ejecutar a mano en Supabase antes de desplegar: alembic/versions/ no esta
-- versionado (ver README, "Migraciones"). Sin estas columnas fallan las rutas
-- de reservas y de configuracion.

ALTER TABLE reservation ADD COLUMN IF NOT EXISTS reminder_sent_at TIMESTAMP;
ALTER TABLE businessconfiguration ADD COLUMN IF NOT EXISTS reminders_enabled BOOLEAN NOT NULL DEFAULT TRUE;

-- Vuelta atras:
-- ALTER TABLE reservation DROP COLUMN IF EXISTS reminder_sent_at;
-- ALTER TABLE businessconfiguration DROP COLUMN IF EXISTS reminders_enabled;
