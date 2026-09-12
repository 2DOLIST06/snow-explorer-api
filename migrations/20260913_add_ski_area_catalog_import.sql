-- Référentiel durable du catalogue de domaines et décisions de rapprochement.
-- Migration additive : aucune station, relation ou donnée éditoriale existante n'est supprimée.
BEGIN;

CREATE TABLE IF NOT EXISTS ski_area_catalog_imports (
  id VARCHAR(36) PRIMARY KEY, catalog_id VARCHAR(255) NOT NULL,
  batch_id VARCHAR(255) NOT NULL, schema_version VARCHAR(255) NOT NULL,
  file_sha256 VARCHAR(64) NOT NULL, status VARCHAR(16) NOT NULL,
  preview_json TEXT NOT NULL, result_json TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), applied_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS ski_area_catalog_areas (
  id BIGSERIAL PRIMARY KEY, catalog_id VARCHAR(255) NOT NULL,
  catalog_key VARCHAR(255) NOT NULL, ski_area_id BIGINT REFERENCES ski_areas(id) ON DELETE SET NULL,
  name TEXT NOT NULL, proposed_slug VARCHAR(255) NOT NULL, area_kind VARCHAR(64), notes TEXT,
  sources_json TEXT NOT NULL DEFAULT '[]', updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (catalog_id, catalog_key)
);
CREATE TABLE IF NOT EXISTS ski_area_expected_stations (
  id BIGSERIAL PRIMARY KEY, catalog_id VARCHAR(255) NOT NULL,
  station_ref VARCHAR(255) NOT NULL, name TEXT NOT NULL, country_code VARCHAR(8), department VARCHAR(255),
  aliases_json TEXT NOT NULL DEFAULT '[]', origin_resolution VARCHAR(32) NOT NULL,
  covered_by_json TEXT NOT NULL DEFAULT '[]', resort_id VARCHAR(255) REFERENCES resort(id) ON DELETE SET NULL,
  resolution_state VARCHAR(32) NOT NULL DEFAULT 'pending', resolution_note TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), UNIQUE (catalog_id, station_ref)
);
CREATE TABLE IF NOT EXISTS ski_area_expected_memberships (
  id BIGSERIAL PRIMARY KEY,
  catalog_area_id BIGINT NOT NULL REFERENCES ski_area_catalog_areas(id) ON DELETE CASCADE,
  expected_station_id BIGINT NOT NULL REFERENCES ski_area_expected_stations(id) ON DELETE CASCADE,
  evidence_status VARCHAR(32) NOT NULL, relation_kind VARCHAR(64) NOT NULL,
  source_json TEXT NOT NULL DEFAULT '{}', state VARCHAR(24) NOT NULL DEFAULT 'pending',
  decision_origin VARCHAR(24) NOT NULL DEFAULT 'catalog', decision_note TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), UNIQUE (catalog_area_id, expected_station_id)
);
CREATE TABLE IF NOT EXISTS ski_area_catalog_notices (
  id BIGSERIAL PRIMARY KEY, catalog_id VARCHAR(255) NOT NULL, notice_key VARCHAR(64) NOT NULL,
  kind VARCHAR(32) NOT NULL, payload_json TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), UNIQUE (catalog_id, notice_key)
);
CREATE INDEX IF NOT EXISTS expected_stations_resort_idx ON ski_area_expected_stations(resort_id);
CREATE INDEX IF NOT EXISTS expected_memberships_state_idx ON ski_area_expected_memberships(state);
COMMIT;
