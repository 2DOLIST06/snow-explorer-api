CREATE TABLE IF NOT EXISTS resort_import_history (
  id varchar(255) PRIMARY KEY,
  created_at timestamptz NOT NULL,
  user_id varchar(255), file_name text, schema_version varchar(16) NOT NULL,
  import_type varchar(16) NOT NULL, status varchar(16) NOT NULL,
  target_station_id varchar(255), stations_total integer NOT NULL DEFAULT 0,
  stations_updated integer NOT NULL DEFAULT 0, stations_created integer NOT NULL DEFAULT 0,
  stations_ignored integer NOT NULL DEFAULT 0, stations_failed integer NOT NULL DEFAULT 0,
  changes_summary text NOT NULL DEFAULT '[]', errors_summary text NOT NULL DEFAULT '[]',
  checksum varchar(64) NOT NULL
);
CREATE INDEX IF NOT EXISTS resort_import_history_created_at_idx ON resort_import_history (created_at DESC);
