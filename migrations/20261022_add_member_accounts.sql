CREATE TABLE IF NOT EXISTS member_users (
 id BIGSERIAL PRIMARY KEY, email VARCHAR(320) NOT NULL UNIQUE,
 password_hash TEXT, is_active BOOLEAN NOT NULL DEFAULT TRUE,
 verified_at TIMESTAMPTZ, password_changed_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS member_sessions (
 id BIGSERIAL PRIMARY KEY, member_id BIGINT NOT NULL REFERENCES member_users(id) ON DELETE CASCADE,
 token_hash VARCHAR(64) NOT NULL UNIQUE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 expires_at TIMESTAMPTZ NOT NULL, revoked_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS member_sessions_member_id ON member_sessions(member_id);
CREATE INDEX IF NOT EXISTS member_sessions_expires_at ON member_sessions(expires_at);
CREATE TABLE IF NOT EXISTS member_login_links (
 id BIGSERIAL PRIMARY KEY, member_id BIGINT NOT NULL UNIQUE REFERENCES member_users(id) ON DELETE CASCADE,
 token_hash VARCHAR(64) UNIQUE, requested_at TIMESTAMPTZ, expires_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS member_login_attempts (
 id BIGSERIAL PRIMARY KEY, key_hash VARCHAR(64) NOT NULL, ip_address VARCHAR(64) NOT NULL, attempted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS member_login_attempts_ip_address ON member_login_attempts(ip_address);
CREATE INDEX IF NOT EXISTS member_login_attempts_attempted_at ON member_login_attempts(attempted_at);
CREATE INDEX IF NOT EXISTS member_login_attempts_key_hash ON member_login_attempts(key_hash);
