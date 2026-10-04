-- Prépare les fiches V2 sans activer aucune station existante et sans recopier
-- les contenus historiques. Les colonnes éditoriales restent NULL par défaut.
ALTER TABLE resort
    ADD COLUMN IF NOT EXISTS page_layout_version VARCHAR(16);

UPDATE resort
SET page_layout_version = 'legacy'
WHERE page_layout_version IS NULL;

ALTER TABLE resort
    ALTER COLUMN page_layout_version SET DEFAULT 'legacy',
    ALTER COLUMN page_layout_version SET NOT NULL;

ALTER TABLE resort
    ADD COLUMN IF NOT EXISTS v2_overview_html TEXT,
    ADD COLUMN IF NOT EXISTS v2_weather_snow_html TEXT,
    ADD COLUMN IF NOT EXISTS v2_ski_pass_html TEXT,
    ADD COLUMN IF NOT EXISTS v2_piste_map_html TEXT,
    ADD COLUMN IF NOT EXISTS v2_webcam_html TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'resort_page_layout_version_check'
          AND conrelid = 'resort'::regclass
    ) THEN
        ALTER TABLE resort ADD CONSTRAINT resort_page_layout_version_check
            CHECK (page_layout_version IN ('legacy', 'v2'));
    END IF;
END $$;
