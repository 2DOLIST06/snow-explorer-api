-- Initialise le total des snowparks de tous les domaines depuis leurs stations
-- réellement rattachées. Réexécuter ce backfill écrase les corrections manuelles.
BEGIN;

-- Les configurations de widgets historiques sont du texte et peuvent contenir
-- du JSON mal formé. Cette fonction locale rend également ces lignes égales à 0.
CREATE OR REPLACE FUNCTION pg_temp.station_snowparks_count(config_text TEXT)
RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
  value JSONB;
  count_value NUMERIC;
BEGIN
  value := config_text::JSONB;
  IF jsonb_typeof(value #> '{snowparks,count}') <> 'number' THEN
    RETURN 0;
  END IF;
  count_value := (value #>> '{snowparks,count}')::NUMERIC;
  IF count_value < 0 OR count_value <> trunc(count_value) OR count_value > 2147483647 THEN
    RETURN 0;
  END IF;
  RETURN count_value::INTEGER;
EXCEPTION WHEN OTHERS THEN
  RETURN 0;
END;
$$;

UPDATE ski_areas AS area
SET snowparks_count = totals.snowparks_count
FROM (
  SELECT area_to_update.id,
         COALESCE(SUM(pg_temp.station_snowparks_count(widgets.config)), 0)::INTEGER
           AS snowparks_count
  FROM ski_areas AS area_to_update
  LEFT JOIN ski_area_resorts AS membership
    ON membership.ski_area_id = area_to_update.id
  LEFT JOIN resort AS station
    ON station.id = membership.resort_id
  LEFT JOIN station_widgets AS widgets
    ON widgets.station_slug = station.slug
  GROUP BY area_to_update.id
) AS totals
WHERE area.id = totals.id;

COMMIT;
