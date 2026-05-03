-- up
ALTER TABLE resort
  ADD COLUMN IF NOT EXISTS is_active boolean;

UPDATE resort
SET is_active = true
WHERE is_active IS NULL;

ALTER TABLE resort
  ALTER COLUMN is_active SET DEFAULT true,
  ALTER COLUMN is_active SET NOT NULL;

-- down
ALTER TABLE resort
  DROP COLUMN IF EXISTS is_active;
