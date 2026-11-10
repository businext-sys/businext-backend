-- Mensajes del negocio a clientes en riesgo (issue #185 de businext-sys/businext).
-- Ejecutar a mano en Supabase antes de desplegar: alembic/versions/ no esta
-- versionado (ver README, "Migraciones"). Sin esta tabla fallan las rutas
-- /customer-messages.
--
-- El modelo CustomerMessage no define __tablename__: la tabla es
-- "customermessage".

CREATE TABLE IF NOT EXISTS customermessage (
    id SERIAL PRIMARY KEY,
    business_id VARCHAR NOT NULL,
    customer_email VARCHAR NOT NULL,
    message VARCHAR NOT NULL,
    sent_by VARCHAR NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_customermessage_business_id ON customermessage (business_id);
CREATE INDEX IF NOT EXISTS ix_customermessage_customer_email ON customermessage (customer_email);
CREATE INDEX IF NOT EXISTS ix_customermessage_created_at ON customermessage (created_at);
ALTER TABLE customermessage ENABLE ROW LEVEL SECURITY;

-- Vuelta atras:
-- DROP TABLE IF EXISTS customermessage;
