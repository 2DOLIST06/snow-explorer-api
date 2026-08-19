-- Dates éditoriales destinées notamment aux balises <lastmod> du sitemap.
ALTER TABLE resort
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE regions
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- Couvre également les écritures SQL réalisées en dehors de l'API. Les routes
-- applicatives renseignent explicitement la date afin que les tests SQLite et
-- les réponses renvoyées juste après un PATCH voient la nouvelle valeur.
CREATE OR REPLACE FUNCTION set_public_page_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS resort_set_updated_at ON resort;
CREATE TRIGGER resort_set_updated_at
BEFORE UPDATE ON resort
FOR EACH ROW EXECUTE FUNCTION set_public_page_updated_at();

DROP TRIGGER IF EXISTS regions_set_updated_at ON regions;
CREATE TRIGGER regions_set_updated_at
BEFORE UPDATE ON regions
FOR EACH ROW EXECUTE FUNCTION set_public_page_updated_at();
