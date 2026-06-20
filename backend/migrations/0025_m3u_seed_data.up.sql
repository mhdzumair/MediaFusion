-- Seed the 'm3u' metadata provider used to attribute tvg-logo artwork imported
-- from M3U playlists, and the 'radio' catalog for audio-only IPTV streams.

INSERT INTO metadata_provider (name, display_name, is_external, is_active, priority, default_priority, created_at)
VALUES ('m3u', 'M3U', false, true, 0, 0, now())
ON CONFLICT (name) DO NOTHING;

INSERT INTO catalog (name, display_name, is_system, display_order)
VALUES ('radio', 'Radio', true, 0)
ON CONFLICT (name) DO NOTHING;
