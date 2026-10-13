-- Optional manual overrides. Names always come from the official GeoJSON.
CREATE TABLE IF NOT EXISTS meteo_france_massif_overrides (
    id SERIAL PRIMARY KEY,
    resort_id VARCHAR NOT NULL UNIQUE REFERENCES resort(id) ON DELETE CASCADE,
    massif_id INTEGER NOT NULL CHECK (massif_id > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
