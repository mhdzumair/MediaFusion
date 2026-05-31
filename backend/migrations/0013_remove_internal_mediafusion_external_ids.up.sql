-- Drop internal MediaFusion identifiers from media_external_id.
--
-- media.id is the sole internal primary key. Stremio-facing aliases like mf:123
-- are computed at read time and must not be persisted as external provider IDs.
--
-- Preserves real provider rows (imdb, tmdb, tvdb, kitsu, …) and user-scoped
-- identifiers (mf:user:…) used for IPTV/M3U imports.

DELETE FROM media_external_id
WHERE provider = 'mediafusion';

DELETE FROM media_external_id
WHERE external_id LIKE 'mfm\_%' ESCAPE '\'
   OR external_id LIKE 'mfs\_%' ESCAPE '\'
   OR external_id LIKE 'mf_tv\_%' ESCAPE '\'
   OR external_id ~ '^mf:[0-9]+$'
   OR external_id ~ '^mf[0-9]+$';
