-- Editable content displayed on /regions/:slug landing pages.
ALTER TABLE regions ADD COLUMN IF NOT EXISTS description_html TEXT;
ALTER TABLE regions ADD COLUMN IF NOT EXISTS meta_title TEXT;
ALTER TABLE regions ADD COLUMN IF NOT EXISTS meta_description TEXT;
