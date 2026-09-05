-- Safe complement for installations that already applied the initial snapshot
-- migration before piste-map catalogue persistence was connected.
ALTER TABLE anmsm_station_snapshots
 ADD COLUMN IF NOT EXISTS piste_map_observation_complete BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE anmsm_station_snapshots
 ADD COLUMN IF NOT EXISTS station_catalog_seen_at TIMESTAMPTZ;
