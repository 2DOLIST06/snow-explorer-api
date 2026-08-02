-- Older deployments may have created these columns through Peewee as
-- TIMESTAMP WITHOUT TIME ZONE.  Such legacy values have always represented UTC.
BEGIN;
DO $$
DECLARE
    item RECORD;
BEGIN
    FOR item IN
        SELECT * FROM (VALUES
            ('admin_users', 'created_at'),
            ('admin_users', 'updated_at'),
            ('admin_users', 'last_login_at'),
            ('admin_users', 'password_changed_at'),
            ('admin_sessions', 'created_at'),
            ('admin_sessions', 'expires_at'),
            ('admin_sessions', 'last_seen_at'),
            ('admin_sessions', 'revoked_at'),
            ('admin_login_attempts', 'attempted_at')
        ) AS columns_to_fix(table_name, column_name)
    LOOP
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = item.table_name
              AND column_name = item.column_name
              AND data_type = 'timestamp without time zone'
        ) THEN
            EXECUTE format(
                'ALTER TABLE %I ALTER COLUMN %I TYPE TIMESTAMPTZ USING %I AT TIME ZONE ''UTC''',
                item.table_name, item.column_name, item.column_name
            );
        END IF;
    END LOOP;
END $$;
COMMIT;
