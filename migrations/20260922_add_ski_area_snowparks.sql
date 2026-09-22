-- Ajoute les informations de snowpark aux domaines skiables existants.
-- Migration additive et réexécutable, sans modification des données existantes.
BEGIN;

ALTER TABLE ski_areas
  ADD COLUMN IF NOT EXISTS snowpark_name TEXT,
  ADD COLUMN IF NOT EXISTS snowparks_count INTEGER;

ALTER TABLE ski_areas
  DROP CONSTRAINT IF EXISTS ski_areas_snowparks_count_non_negative,
  ADD CONSTRAINT ski_areas_snowparks_count_non_negative
    CHECK (snowparks_count >= 0);

COMMIT;
