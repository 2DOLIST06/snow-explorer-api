-- Apply BEFORE deploying the API that reads these columns. Safe to run again.
BEGIN;
ALTER TABLE newsletter_station_preferences ADD COLUMN IF NOT EXISTS snowfall_enabled BOOLEAN;
ALTER TABLE newsletter_station_preferences ADD COLUMN IF NOT EXISTS articles_enabled BOOLEAN;
ALTER TABLE newsletter_station_preferences ADD COLUMN IF NOT EXISTS newsletter_frequency VARCHAR(16);

-- Only missing new values inherit the subscriber's previous general choices.
UPDATE newsletter_station_preferences AS station
SET snowfall_enabled = COALESCE(station.snowfall_enabled, general.snowfall, TRUE),
    articles_enabled = COALESCE(station.articles_enabled, general.articles, TRUE),
    newsletter_frequency = COALESCE(station.newsletter_frequency, general.newsletter_frequency, 'weekly')
FROM snow_newsletter_preferences AS general
WHERE station.subscriber_id = general.subscriber_id
  AND (station.snowfall_enabled IS NULL OR station.articles_enabled IS NULL OR station.newsletter_frequency IS NULL);
UPDATE newsletter_station_preferences
SET snowfall_enabled = COALESCE(snowfall_enabled, TRUE),
    articles_enabled = COALESCE(articles_enabled, TRUE),
    newsletter_frequency = COALESCE(newsletter_frequency, 'weekly')
WHERE snowfall_enabled IS NULL OR articles_enabled IS NULL OR newsletter_frequency IS NULL;

ALTER TABLE newsletter_station_preferences ALTER COLUMN snowfall_enabled SET DEFAULT TRUE;
ALTER TABLE newsletter_station_preferences ALTER COLUMN snowfall_enabled SET NOT NULL;
ALTER TABLE newsletter_station_preferences ALTER COLUMN articles_enabled SET DEFAULT TRUE;
ALTER TABLE newsletter_station_preferences ALTER COLUMN articles_enabled SET NOT NULL;
ALTER TABLE newsletter_station_preferences ALTER COLUMN newsletter_frequency SET DEFAULT 'weekly';
ALTER TABLE newsletter_station_preferences ALTER COLUMN newsletter_frequency SET NOT NULL;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'newsletter_station_preferences'::regclass AND conname = 'newsletter_station_frequency_check') THEN
        ALTER TABLE newsletter_station_preferences ADD CONSTRAINT newsletter_station_frequency_check
            CHECK (newsletter_frequency IN ('immediate', 'weekly', 'monthly'));
    END IF;
END $$;
COMMIT;
