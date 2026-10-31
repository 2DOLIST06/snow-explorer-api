BEGIN;

-- Demande non confirmée, conservée avec le lien de connexion à usage unique.
ALTER TABLE member_login_links
    ADD COLUMN IF NOT EXISTS newsletter_intent TEXT;

-- Choix distincts par station. Les abonnements existants sont conservés.
ALTER TABLE newsletter_station_preferences
    ADD COLUMN IF NOT EXISTS opening_closing_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS lift_updates_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS ski_pass_updates_enabled BOOLEAN NOT NULL DEFAULT FALSE;

COMMIT;
