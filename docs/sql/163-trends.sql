-- Radar de tendencias: tendencias, analisis, sugerencias y preferencias de
-- contenido (businext-sys/businext#163). Aplicado en Supabase con el MCP de
-- Supabase (apply_migration «163_trends»): alembic/versions/ no esta
-- versionado (ver README, "Migraciones").

CREATE TABLE IF NOT EXISTS trendsnapshot (
    id SERIAL PRIMARY KEY,
    niche VARCHAR NOT NULL,
    language VARCHAR NOT NULL,
    country VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    day DATE NOT NULL,
    captured_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    payload VARCHAR NOT NULL,
    summary VARCHAR,
    CONSTRAINT uq_trend_snapshot_day UNIQUE (niche, language, country, source, day)
);

CREATE TABLE IF NOT EXISTS trendinsight (
    id SERIAL PRIMARY KEY,
    business_id VARCHAR NOT NULL,
    day DATE NOT NULL,
    trends_used VARCHAR NOT NULL,
    analysis VARCHAR NOT NULL,
    matched_services VARCHAR NOT NULL,
    model VARCHAR NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    CONSTRAINT uq_trend_insight_business_day UNIQUE (business_id, day)
);
CREATE INDEX IF NOT EXISTS ix_trendinsight_business_id ON trendinsight (business_id);

CREATE TABLE IF NOT EXISTS contentsuggestion (
    id SERIAL PRIMARY KEY,
    business_id VARCHAR NOT NULL,
    insight_id INTEGER NOT NULL,
    network VARCHAR NOT NULL,
    format VARCHAR NOT NULL,
    idea VARCHAR NOT NULL,
    caption_hint VARCHAR NOT NULL,
    audio_hint VARCHAR,
    publish_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    source_trend_ids VARCHAR NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'pending',
    sent_at TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_contentsuggestion_business_id ON contentsuggestion (business_id);
CREATE INDEX IF NOT EXISTS ix_contentsuggestion_insight_id ON contentsuggestion (insight_id);
CREATE INDEX IF NOT EXISTS ix_contentsuggestion_publish_at ON contentsuggestion (publish_at);
CREATE INDEX IF NOT EXISTS ix_contentsuggestion_status ON contentsuggestion (status);

CREATE TABLE IF NOT EXISTS contentpreferences (
    business_id VARCHAR PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    networks VARCHAR NOT NULL DEFAULT '["instagram", "tiktok"]',
    max_per_day INTEGER NOT NULL DEFAULT 1,
    quiet_from TIME WITHOUT TIME ZONE NOT NULL DEFAULT '21:00',
    quiet_to TIME WITHOUT TIME ZONE NOT NULL DEFAULT '09:00',
    timezone VARCHAR NOT NULL DEFAULT 'Europe/Madrid',
    niche VARCHAR NOT NULL DEFAULT 'barberia',
    language VARCHAR NOT NULL DEFAULT 'es',
    country VARCHAR NOT NULL DEFAULT 'ES',
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
);

-- Solo el backend accede a estas tablas: sin politicas, RLS cierra la API de Supabase.
ALTER TABLE trendsnapshot ENABLE ROW LEVEL SECURITY;
ALTER TABLE trendinsight ENABLE ROW LEVEL SECURITY;
ALTER TABLE contentsuggestion ENABLE ROW LEVEL SECURITY;
ALTER TABLE contentpreferences ENABLE ROW LEVEL SECURITY;

-- Vuelta atras:
-- DROP TABLE IF EXISTS contentpreferences, contentsuggestion, trendinsight, trendsnapshot;
