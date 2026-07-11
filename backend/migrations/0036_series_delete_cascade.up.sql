-- Deleting a media row should fully cascade through series -> season -> episode.
-- These FKs were created without ON DELETE CASCADE, so deleting series_metadata
-- (which itself cascades from media) fails with a foreign key violation because
-- season/episode/episode_image rows still reference the row being deleted.

-- Remove season/episode/episode_image rows that were already orphaned by past
-- deletes that only partially succeeded (series_metadata deleted but season left
-- behind is not possible today since the delete fails atomically, but episode
-- rows can be orphaned relative to a season whose series was deleted directly).
DELETE FROM episode_image WHERE episode_id NOT IN (SELECT id FROM episode);
DELETE FROM episode WHERE season_id NOT IN (SELECT id FROM season);
DELETE FROM season WHERE series_id NOT IN (SELECT id FROM series_metadata);

ALTER TABLE public.season DROP CONSTRAINT season_series_id_fkey;
ALTER TABLE public.season
    ADD CONSTRAINT season_series_id_fkey FOREIGN KEY (series_id) REFERENCES public.series_metadata(id) ON DELETE CASCADE;

ALTER TABLE public.episode DROP CONSTRAINT episode_season_id_fkey;
ALTER TABLE public.episode
    ADD CONSTRAINT episode_season_id_fkey FOREIGN KEY (season_id) REFERENCES public.season(id) ON DELETE CASCADE;

ALTER TABLE public.episode_image DROP CONSTRAINT episode_image_episode_id_fkey;
ALTER TABLE public.episode_image
    ADD CONSTRAINT episode_image_episode_id_fkey FOREIGN KEY (episode_id) REFERENCES public.episode(id) ON DELETE CASCADE;
