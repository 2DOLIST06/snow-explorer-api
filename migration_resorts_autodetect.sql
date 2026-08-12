DO -l
DECLARE
  t text;
  chosen text;
  candidates text[] := ARRAY['resorts','stations','ski_resorts','resort'];
BEGIN
  -- trouver la table existante
  FOREACH t IN ARRAY candidates LOOP
    PERFORM 1
    FROM information_schema.tables
    WHERE table_schema='public' AND table_name=t;
    IF FOUND THEN chosen := t; EXIT; END IF;
  END LOOP;
  IF chosen IS NULL THEN
    RAISE EXCEPTION 'Aucune table trouvée parmi %', candidates;
  END IF;

  -- colonnes à ajouter si manquantes
  EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION', chosen);
  EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION', chosen);
  EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS website_url TEXT', chosen);
  EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS cover_image_url TEXT', chosen);
  EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS description_md TEXT', chosen);
  EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS region_id TEXT', chosen);
  EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS department TEXT', chosen);
  EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS altitude_base_m INTEGER', chosen);
  EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS altitude_top_m INTEGER', chosen);
  EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS altitude_min_m INTEGER', chosen);
  EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS altitude_max_m INTEGER', chosen);
  EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS season_open_date DATE', chosen);
  EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS season_close_date DATE', chosen);
  EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS ski_area_km INTEGER', chosen);
  EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS lifts_count INTEGER', chosen);
  EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS pistes_count INTEGER', chosen);
  EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS amenities TEXT', chosen);
END -l;
