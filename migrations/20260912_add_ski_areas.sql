-- Domaines skiables, volontairement distincts des stations historiques `resort`.
-- Cette migration est additive et ne déduit ni ne copie aucune donnée existante.
BEGIN;

CREATE TABLE IF NOT EXISTS ski_areas (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  slug VARCHAR(255) NOT NULL UNIQUE,
  status VARCHAR(16) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published')),
  description TEXT, cover_image_url TEXT, piste_map_url TEXT,
  altitude_min_m INTEGER CHECK (altitude_min_m >= 0),
  altitude_max_m INTEGER CHECK (altitude_max_m >= 0),
  ski_area_km INTEGER CHECK (ski_area_km >= 0),
  pistes_count INTEGER CHECK (pistes_count >= 0),
  green_pistes_count INTEGER CHECK (green_pistes_count >= 0),
  blue_pistes_count INTEGER CHECK (blue_pistes_count >= 0),
  red_pistes_count INTEGER CHECK (red_pistes_count >= 0),
  black_pistes_count INTEGER CHECK (black_pistes_count >= 0),
  lifts_count INTEGER CHECK (lifts_count >= 0),
  forecast_open_date DATE, forecast_close_date DATE,
  season VARCHAR(32), source TEXT, verified_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK (altitude_min_m IS NULL OR altitude_max_m IS NULL OR altitude_min_m <= altitude_max_m),
  CHECK (forecast_open_date IS NULL OR forecast_close_date IS NULL OR forecast_open_date <= forecast_close_date)
);

CREATE TABLE IF NOT EXISTS ski_area_resorts (
  id BIGSERIAL PRIMARY KEY,
  ski_area_id BIGINT NOT NULL REFERENCES ski_areas(id) ON DELETE CASCADE,
  resort_id VARCHAR(255) NOT NULL REFERENCES resort(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (ski_area_id, resort_id)
);
CREATE INDEX IF NOT EXISTS ski_area_resorts_resort_idx ON ski_area_resorts(resort_id);
COMMIT;
