-- Socle relationnel du centre d'abonnement Snow Explorer. Aucun envoi n'est
-- réalisé par cette migration ou par les modèles associés.
BEGIN;

CREATE TABLE newsletter_subscribers (
  id BIGSERIAL PRIMARY KEY,
  email VARCHAR(320) NOT NULL UNIQUE CHECK (email = lower(email)),
  status VARCHAR(16) NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'active', 'unsubscribed', 'bounced', 'complained')),
  language VARCHAR(2) NOT NULL CHECK (language IN ('fr', 'en')),
  source VARCHAR(100) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  confirmed_at TIMESTAMPTZ,
  unsubscribed_at TIMESTAMPTZ,
  consent_at TIMESTAMPTZ NOT NULL,
  consent_text_version VARCHAR(50) NOT NULL,
  consent_source VARCHAR(100) NOT NULL,
  confirmation_token VARCHAR(128) UNIQUE,
  preferences_token VARCHAR(128) NOT NULL UNIQUE
);
CREATE INDEX newsletter_subscribers_status_language_idx
  ON newsletter_subscribers(status, language);

CREATE TABLE snow_newsletter_preferences (
  id BIGSERIAL PRIMARY KEY,
  subscriber_id BIGINT NOT NULL UNIQUE REFERENCES newsletter_subscribers(id) ON DELETE CASCADE,
  snow_conditions BOOLEAN NOT NULL DEFAULT TRUE,
  snowfall BOOLEAN NOT NULL DEFAULT TRUE,
  weather BOOLEAN NOT NULL DEFAULT TRUE,
  resort_updates BOOLEAN NOT NULL DEFAULT TRUE,
  opening_closing BOOLEAN NOT NULL DEFAULT TRUE,
  lift_updates BOOLEAN NOT NULL DEFAULT TRUE,
  ski_pass_updates BOOLEAN NOT NULL DEFAULT TRUE,
  articles BOOLEAN NOT NULL DEFAULT TRUE,
  newsletter_frequency VARCHAR(16) NOT NULL DEFAULT 'weekly'
    CHECK (newsletter_frequency IN ('immediate', 'weekly', 'monthly')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX snow_newsletter_preferences_frequency_idx
  ON snow_newsletter_preferences(newsletter_frequency);

CREATE TABLE newsletter_subscriber_stations (
  id BIGSERIAL PRIMARY KEY,
  subscriber_id BIGINT NOT NULL REFERENCES newsletter_subscribers(id) ON DELETE CASCADE,
  station_id VARCHAR(255) NOT NULL REFERENCES resort(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (subscriber_id, station_id)
);
CREATE INDEX newsletter_subscriber_stations_station_idx
  ON newsletter_subscriber_stations(station_id);

CREATE TABLE newsletter_station_preferences (
  id BIGSERIAL PRIMARY KEY,
  subscriber_id BIGINT NOT NULL REFERENCES newsletter_subscribers(id) ON DELETE CASCADE,
  station_id VARCHAR(255) NOT NULL REFERENCES resort(id) ON DELETE CASCADE,
  weather_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  snow_conditions_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  resort_updates_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  weather_frequency VARCHAR(16) NOT NULL DEFAULT 'weekly'
    CHECK (weather_frequency IN ('daily', 'friday', 'weekly', 'disabled')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (subscriber_id, station_id),
  FOREIGN KEY (subscriber_id, station_id)
    REFERENCES newsletter_subscriber_stations(subscriber_id, station_id) ON DELETE CASCADE
);
CREATE INDEX newsletter_station_preferences_station_frequency_idx
  ON newsletter_station_preferences(station_id, weather_frequency);

CREATE TABLE snow_alerts (
  id BIGSERIAL PRIMARY KEY,
  subscriber_id BIGINT NOT NULL REFERENCES newsletter_subscribers(id) ON DELETE CASCADE,
  station_id VARCHAR(255) NOT NULL REFERENCES resort(id) ON DELETE CASCADE,
  alert_type VARCHAR(16) NOT NULL DEFAULT 'snowfall' CHECK (alert_type = 'snowfall'),
  threshold_cm INTEGER NOT NULL CHECK (threshold_cm BETWEEN 1 AND 500),
  forecast_period_hours INTEGER NOT NULL CHECK (forecast_period_hours IN (24, 48, 72)),
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  FOREIGN KEY (subscriber_id, station_id)
    REFERENCES newsletter_subscriber_stations(subscriber_id, station_id) ON DELETE CASCADE
);
CREATE INDEX snow_alerts_automation_idx
  ON snow_alerts(station_id, alert_type, forecast_period_hours, is_active);

COMMIT;
