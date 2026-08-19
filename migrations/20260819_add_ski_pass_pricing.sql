-- Normalized, season-aware ski-pass tariffs. Existing station_widgets data is
-- deliberately retained: free-form strings cannot safely be inferred as money,
-- dates, seasons or categories and remain served by the legacy endpoints.
CREATE TABLE IF NOT EXISTS ski_pass_seasons (
  id BIGSERIAL PRIMARY KEY,
  resort_id VARCHAR(255) NOT NULL REFERENCES resort(id) ON DELETE CASCADE,
  season VARCHAR(255) NOT NULL,
  currency VARCHAR(3) NOT NULL DEFAULT 'EUR',
  source_url TEXT,
  is_active BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (resort_id, season)
);

CREATE TABLE IF NOT EXISTS ski_pass_periods (
  id BIGSERIAL PRIMARY KEY,
  season_id BIGINT NOT NULL REFERENCES ski_pass_seasons(id) ON DELETE CASCADE,
  external_id VARCHAR(255) NOT NULL,
  name VARCHAR(255) NOT NULL,
  start_date DATE NOT NULL,
  end_date DATE NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 0,
  CHECK (start_date <= end_date),
  UNIQUE (season_id, external_id)
);

CREATE TABLE IF NOT EXISTS ski_pass_products (
  id BIGSERIAL PRIMARY KEY,
  season_id BIGINT NOT NULL REFERENCES ski_pass_seasons(id) ON DELETE CASCADE,
  external_id VARCHAR(255) NOT NULL,
  name VARCHAR(255) NOT NULL,
  duration_days INTEGER CHECK (duration_days IS NULL OR duration_days > 0),
  duration_label VARCHAR(255) NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 0,
  UNIQUE (season_id, external_id)
);

CREATE TABLE IF NOT EXISTS ski_pass_prices (
  id BIGSERIAL PRIMARY KEY,
  product_id BIGINT NOT NULL REFERENCES ski_pass_products(id) ON DELETE CASCADE,
  period_id BIGINT NOT NULL REFERENCES ski_pass_periods(id) ON DELETE CASCADE,
  category VARCHAR(255) NOT NULL,
  category_label VARCHAR(255) NOT NULL,
  price_type VARCHAR(7) NOT NULL,
  price NUMERIC(12,2), price_min NUMERIC(12,2), price_max NUMERIC(12,2),
  dynamic_label TEXT,
  sort_order INTEGER NOT NULL DEFAULT 0,
  UNIQUE (product_id, period_id, category),
  CHECK (
    (price_type = 'fixed' AND price IS NOT NULL AND price >= 0 AND price_min IS NULL AND price_max IS NULL)
    OR
    (price_type = 'dynamic' AND price IS NULL AND price_min IS NOT NULL AND price_min >= 0
      AND price_max IS NOT NULL AND price_max >= price_min)
  )
);

CREATE UNIQUE INDEX IF NOT EXISTS ski_pass_one_active_season_per_resort
  ON ski_pass_seasons (resort_id)
  WHERE is_active = TRUE;
