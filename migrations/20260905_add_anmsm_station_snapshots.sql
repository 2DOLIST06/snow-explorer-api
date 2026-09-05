-- The upstream station catalogue was previously transient.  Persisting its
-- observations is required for a read-only coverage page and for distinguishing
-- a confirmed missing resource from an incomplete/unknown synchronization.
CREATE TABLE IF NOT EXISTS anmsm_station_snapshots (
 external_station_id VARCHAR(255) PRIMARY KEY,
 station_name TEXT NOT NULL,
 station_slug TEXT,
 logo_available BOOLEAN,
 logo_url TEXT,
 logo_seen_at TIMESTAMPTZ,
 piste_map_available BOOLEAN,
 piste_map_url TEXT,
 piste_map_seen_at TIMESTAMPTZ,
 piste_map_observation_complete BOOLEAN NOT NULL DEFAULT FALSE,
 station_catalog_seen_at TIMESTAMPTZ,
 last_seen_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS anmsm_station_snapshots_last_seen_idx
 ON anmsm_station_snapshots(last_seen_at);
