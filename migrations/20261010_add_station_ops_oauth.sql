-- OAuth infrastructure only. Run explicitly later; never run at application startup.
BEGIN;
CREATE TABLE station_ops_oauth_flows (
    id BIGSERIAL PRIMARY KEY,
    handle_hash VARCHAR(64) NOT NULL UNIQUE,
    browser_hash VARCHAR(64) NOT NULL,
    client_id TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    resource TEXT NOT NULL,
    scopes TEXT NOT NULL,
    state TEXT,
    code_challenge VARCHAR(43) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ
);
CREATE INDEX station_ops_oauth_flows_expiry_idx ON station_ops_oauth_flows(expires_at);
CREATE TABLE station_ops_oauth_codes (
    id BIGSERIAL PRIMARY KEY,
    code_hash VARCHAR(64) NOT NULL UNIQUE,
    admin_user_id INTEGER NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    client_id TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    resource TEXT NOT NULL,
    scopes TEXT NOT NULL,
    code_challenge VARCHAR(43) NOT NULL,
    code_challenge_method VARCHAR(4) NOT NULL DEFAULT 'S256' CHECK (code_challenge_method = 'S256'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ
);
CREATE INDEX station_ops_oauth_codes_admin_idx ON station_ops_oauth_codes(admin_user_id);
CREATE INDEX station_ops_oauth_codes_expiry_idx ON station_ops_oauth_codes(expires_at);
CREATE TABLE station_ops_oauth_grants (
    id BIGSERIAL PRIMARY KEY,
    admin_user_id INTEGER NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    client_id TEXT NOT NULL,
    resource TEXT NOT NULL,
    scopes TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ
);
CREATE INDEX station_ops_oauth_grants_admin_idx ON station_ops_oauth_grants(admin_user_id);
CREATE INDEX station_ops_oauth_grants_expiry_idx ON station_ops_oauth_grants(expires_at);
CREATE INDEX station_ops_oauth_grants_revoked_idx ON station_ops_oauth_grants(revoked_at);
CREATE TABLE station_ops_oauth_tokens (
    id BIGSERIAL PRIMARY KEY,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    grant_id BIGINT NOT NULL REFERENCES station_ops_oauth_grants(id) ON DELETE CASCADE,
    kind VARCHAR(7) NOT NULL CHECK (kind IN ('access', 'refresh')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ
);
CREATE INDEX station_ops_oauth_tokens_grant_idx ON station_ops_oauth_tokens(grant_id);
CREATE INDEX station_ops_oauth_tokens_expiry_idx ON station_ops_oauth_tokens(expires_at);
CREATE INDEX station_ops_oauth_tokens_revoked_idx ON station_ops_oauth_tokens(revoked_at);
CREATE TABLE station_ops_oauth_rate_buckets (
    id BIGSERIAL PRIMARY KEY,
    key_hash VARCHAR(64) NOT NULL UNIQUE,
    count INTEGER NOT NULL DEFAULT 0 CHECK (count >= 0),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX station_ops_oauth_rate_expiry_idx ON station_ops_oauth_rate_buckets(expires_at);
COMMIT;
