-- Public visibility only: no season, product, period or price data is removed.
ALTER TABLE ski_pass_seasons
  ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT FALSE;

-- The public normalized contract displays at most one season per resort.
CREATE UNIQUE INDEX IF NOT EXISTS ski_pass_seasons_one_active_per_resort
  ON ski_pass_seasons (resort_id)
  WHERE is_active = TRUE;
