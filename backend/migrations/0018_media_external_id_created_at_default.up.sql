-- Many INSERT statements omit created_at. Add a server-side DEFAULT so they
-- no longer violate the NOT NULL constraint.
ALTER TABLE media_external_id ALTER COLUMN created_at SET DEFAULT now();
