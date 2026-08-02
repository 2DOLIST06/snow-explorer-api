BEGIN;
CREATE TABLE IF NOT EXISTS admin_users (
    id BIGSERIAL PRIMARY KEY,
    email VARCHAR(320) NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role VARCHAR(50) NOT NULL DEFAULT 'admin',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at TIMESTAMPTZ,
    password_changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT admin_users_email_lowercase CHECK (email = lower(email))
);
CREATE INDEX IF NOT EXISTS admin_users_is_active_idx ON admin_users (is_active);

CREATE TABLE IF NOT EXISTS admin_sessions (
    id BIGSERIAL PRIMARY KEY,
    admin_user_id BIGINT NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    csrf_token_hash VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ,
    ip_address VARCHAR(64),
    user_agent TEXT
);
CREATE INDEX IF NOT EXISTS admin_sessions_user_idx ON admin_sessions (admin_user_id);
CREATE INDEX IF NOT EXISTS admin_sessions_expires_idx ON admin_sessions (expires_at);
CREATE INDEX IF NOT EXISTS admin_sessions_revoked_idx ON admin_sessions (revoked_at);

-- Shared PostgreSQL storage keeps rate limits consistent across Gunicorn workers/instances.
CREATE TABLE IF NOT EXISTS admin_login_attempts (
    id BIGSERIAL PRIMARY KEY,
    ip_address VARCHAR(64) NOT NULL,
    email VARCHAR(320) NOT NULL,
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS admin_login_attempts_lookup_idx
    ON admin_login_attempts (ip_address, email, attempted_at);
CREATE INDEX IF NOT EXISTS admin_login_attempts_time_idx ON admin_login_attempts (attempted_at);
COMMIT;
