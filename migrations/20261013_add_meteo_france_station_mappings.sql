BEGIN;

CREATE TABLE IF NOT EXISTS meteo_france_station_mappings (
    id SERIAL PRIMARY KEY,
    resort_id VARCHAR(255) NOT NULL UNIQUE REFERENCES resort(id) ON DELETE CASCADE,
    meteo_france_station_id VARCHAR(8) NOT NULL
        CHECK (meteo_france_station_id ~ '^[0-9]{8}$'),
    station_name VARCHAR(255) NOT NULL,
    latitude DOUBLE PRECISION NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude DOUBLE PRECISION NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    altitude_m INTEGER NOT NULL,
    distance_km DOUBLE PRECISION CHECK (distance_km >= 0),
    is_manual_override BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Only existing Resorts are mapped. Preserve any operator override on reruns.
INSERT INTO meteo_france_station_mappings
    (resort_id, meteo_france_station_id, station_name, latitude, longitude,
     altitude_m, distance_km, is_manual_override)
SELECT r.id, v.station_id, v.station_name, v.latitude, v.longitude,
       v.altitude_m, v.distance_km, TRUE
FROM resort r
JOIN (VALUES
    ('la-colmiane', '06153400', 'MILLEFONTS-NIVOSE', 44.118333, 7.192000, 2430, 5.587),
    ('luz-ardiden', '65413400', 'LAC D''ARDIDEN-NIVOSE', 42.848167, -0.066000, 2445, 4.147),
    ('val-thorens', '73257005', 'VAL THORENS_SAPC', 45.294000, 6.581000, 2277, 0.472)
) AS v(slug, station_id, station_name, latitude, longitude, altitude_m, distance_km)
ON r.slug = v.slug
ON CONFLICT (resort_id) DO NOTHING;

COMMIT;
