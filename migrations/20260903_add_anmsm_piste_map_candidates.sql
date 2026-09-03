CREATE TABLE IF NOT EXISTS station_piste_map_candidates (
 id BIGSERIAL PRIMARY KEY, station_id VARCHAR(255) NOT NULL REFERENCES resort(id) ON DELETE CASCADE,
 external_station_id VARCHAR(255) NOT NULL, anmsm_media_id VARCHAR(255) NOT NULL, anmsm_title TEXT,
 anmsm_credit TEXT, plan_type VARCHAR(255), source_url TEXT NOT NULL, source_checksum VARCHAR(64) NOT NULL CHECK (source_checksum ~ '^[0-9a-f]{64}$'),
 source_format VARCHAR(16) NOT NULL CHECK (source_format IN ('jpeg','png','webp','pdf')), source_width INTEGER CHECK(source_width > 0),
 source_height INTEGER CHECK(source_height > 0), source_size_bytes INTEGER NOT NULL CHECK(source_size_bytes > 0), original_s3_key TEXT NOT NULL,
 display_s3_key TEXT, display_width INTEGER CHECK(display_width > 0), display_height INTEGER CHECK(display_height > 0),
 display_size_bytes INTEGER CHECK(display_size_bytes > 0), status VARCHAR(16) NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','ignored','updated','error')),
 warnings TEXT NOT NULL DEFAULT '[]', previous_plan_url TEXT, previous_plan_s3_key TEXT, detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 approved_at TIMESTAMPTZ, approved_by BIGINT REFERENCES admin_users(id) ON DELETE SET NULL, ignored_at TIMESTAMPTZ,
 ignored_by BIGINT REFERENCES admin_users(id) ON DELETE SET NULL, error_code VARCHAR(64), error_message TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 CONSTRAINT station_piste_map_candidates_media_checksum_uq UNIQUE(station_id, anmsm_media_id, source_checksum));
CREATE INDEX IF NOT EXISTS station_piste_map_candidates_status_idx ON station_piste_map_candidates(status);
CREATE INDEX IF NOT EXISTS station_piste_map_candidates_external_idx ON station_piste_map_candidates(external_station_id, anmsm_media_id);
