-- Full region contract and resort integrity. Safe to run repeatedly.
CREATE TABLE IF NOT EXISTS regions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    country_code VARCHAR(2) NOT NULL DEFAULT 'FR',
    seo_text TEXT,
    meta_title VARCHAR(70),
    meta_description VARCHAR(170),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE regions ADD COLUMN IF NOT EXISTS slug TEXT;
ALTER TABLE regions ADD COLUMN IF NOT EXISTS seo_text TEXT;
ALTER TABLE regions ADD COLUMN IF NOT EXISTS meta_title VARCHAR(70);
ALTER TABLE regions ADD COLUMN IF NOT EXISTS meta_description VARCHAR(170);
ALTER TABLE regions ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE regions ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
UPDATE regions SET slug = trim(both '-' FROM regexp_replace(
    lower(translate(name, 'ÀÁÂÃÄÅàáâãäåÇçÈÉÊËèéêëÌÍÎÏìíîïÑñÒÓÔÕÖòóôõöÙÚÛÜùúûüÝŸýÿŒœ',
                          'AAAAAAaaaaaaCcEEEEeeeeIIIIiiiiNnOOOOOoooooUUUUuuuuYYyyOeoe')),
    '[^a-z0-9]+', '-', 'g')) WHERE slug IS NULL OR slug = '';
ALTER TABLE regions ALTER COLUMN slug SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS regions_slug_unique ON regions(slug);

-- Materialize legacy resort region ids before adding the foreign key. The id
-- is retained and region_name is used when available; SEO fields are untouched.
INSERT INTO regions (id, name, slug, country_code)
SELECT DISTINCT r.region_id,
       COALESCE(NULLIF(r.region_name, ''), r.region_id),
       trim(both '-' FROM regexp_replace(lower(COALESCE(NULLIF(r.region_name, ''), r.region_id)),
                                         '[^a-z0-9]+', '-', 'g')),
       COALESCE(NULLIF(upper(r.country_code), ''), 'FR')
FROM resort r
WHERE r.region_id IS NOT NULL AND r.region_id <> ''
ON CONFLICT (id) DO NOTHING;

DO $$ BEGIN
    ALTER TABLE resort ADD CONSTRAINT resort_region_id_fk
        FOREIGN KEY (region_id) REFERENCES regions(id) ON DELETE RESTRICT;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
