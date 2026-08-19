-- Publication is season-specific. Existing normalized grids remain private
-- until an administrator explicitly publishes one.
ALTER TABLE ski_pass_seasons
  ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT FALSE;

-- At most one normalized season can be public for a station.
CREATE UNIQUE INDEX IF NOT EXISTS ski_pass_one_active_season_per_resort
  ON ski_pass_seasons (resort_id)
  WHERE is_active = TRUE;
