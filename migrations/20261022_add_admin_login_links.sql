CREATE TABLE IF NOT EXISTS admin_login_links (
    id BIGSERIAL PRIMARY KEY,
    admin_user_id BIGINT NOT NULL UNIQUE REFERENCES admin_users(id) ON DELETE CASCADE,
    token_hash VARCHAR(64) UNIQUE,
    requested_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ
);
