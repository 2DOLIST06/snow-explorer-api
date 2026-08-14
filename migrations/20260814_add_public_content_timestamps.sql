-- Reliable sitemap timestamps.
--
-- Historical rows intentionally remain NULL: the database contains no evidence
-- from which an exact creation/modification instant could be reconstructed.
-- Only new inserts and real changes made after this migration are dated.

ALTER TABLE resort
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

ALTER TABLE station_widgets
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

ALTER TABLE regions
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

CREATE OR REPLACE FUNCTION set_resort_content_timestamps()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.created_at := COALESCE(NEW.created_at, CURRENT_TIMESTAMP);
        NEW.updated_at := COALESCE(NEW.updated_at, NEW.created_at);
    ELSIF (to_jsonb(NEW) - ARRAY['created_at', 'updated_at'])
          IS DISTINCT FROM
          (to_jsonb(OLD) - ARRAY['created_at', 'updated_at']) THEN
        NEW.created_at := OLD.created_at;
        NEW.updated_at := CURRENT_TIMESTAMP;
    ELSE
        NEW.created_at := OLD.created_at;
        NEW.updated_at := OLD.updated_at;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS resort_content_timestamps ON resort;
CREATE TRIGGER resort_content_timestamps
BEFORE INSERT OR UPDATE ON resort
FOR EACH ROW EXECUTE FUNCTION set_resort_content_timestamps();

CREATE OR REPLACE FUNCTION set_widget_content_timestamp()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.updated_at := COALESCE(NEW.updated_at, CURRENT_TIMESTAMP);
    ELSIF NEW.config IS DISTINCT FROM OLD.config
       OR NEW.station_slug IS DISTINCT FROM OLD.station_slug THEN
        NEW.updated_at := CURRENT_TIMESTAMP;
    ELSE
        NEW.updated_at := OLD.updated_at;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS station_widgets_content_timestamp ON station_widgets;
CREATE TRIGGER station_widgets_content_timestamp
BEFORE INSERT OR UPDATE ON station_widgets
FOR EACH ROW EXECUTE FUNCTION set_widget_content_timestamp();

CREATE OR REPLACE FUNCTION set_region_content_timestamps()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.created_at := COALESCE(NEW.created_at, CURRENT_TIMESTAMP);
        NEW.updated_at := COALESCE(NEW.updated_at, NEW.created_at);
    ELSIF (to_jsonb(NEW) - ARRAY['created_at', 'updated_at'])
          IS DISTINCT FROM
          (to_jsonb(OLD) - ARRAY['created_at', 'updated_at']) THEN
        NEW.created_at := OLD.created_at;
        NEW.updated_at := CURRENT_TIMESTAMP;
    ELSE
        NEW.created_at := OLD.created_at;
        NEW.updated_at := OLD.updated_at;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS region_content_timestamps ON regions;
CREATE TRIGGER region_content_timestamps
BEFORE INSERT OR UPDATE ON regions
FOR EACH ROW EXECUTE FUNCTION set_region_content_timestamps();
