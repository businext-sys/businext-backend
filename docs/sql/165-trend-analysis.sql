-- Analisis del radar de tendencias (businext-sys/businext#165): guion de 3
-- pasos de cada sugerencia y limite de un refresh al dia del analisis.
-- Aplicado en Supabase con el MCP de Supabase (apply_migration
-- «165_trend_analysis»): alembic/versions/ no esta versionado (ver README,
-- "Migraciones"). Las dos tablas ya tienen RLS activado desde #163.

ALTER TABLE contentsuggestion ADD COLUMN IF NOT EXISTS script VARCHAR NOT NULL DEFAULT '[]';
ALTER TABLE trendinsight ADD COLUMN IF NOT EXISTS refreshed_at TIMESTAMP WITHOUT TIME ZONE;

-- Vuelta atras:
-- ALTER TABLE contentsuggestion DROP COLUMN IF EXISTS script;
-- ALTER TABLE trendinsight DROP COLUMN IF EXISTS refreshed_at;
