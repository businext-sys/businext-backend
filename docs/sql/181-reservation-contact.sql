-- Telefono y email del cliente en cada reserva (issue #181 de businext-sys/businext).
-- Ejecutar a mano en Supabase antes de desplegar: alembic/versions/ no esta
-- versionado (ver README, "Migraciones"). Sin estas columnas fallan todas las
-- rutas de reservas.

ALTER TABLE reservation ADD COLUMN IF NOT EXISTS customer_phone VARCHAR;
ALTER TABLE reservation ADD COLUMN IF NOT EXISTS customer_email VARCHAR;

-- Vuelta atras:
-- ALTER TABLE reservation DROP COLUMN IF EXISTS customer_phone;
-- ALTER TABLE reservation DROP COLUMN IF EXISTS customer_email;
