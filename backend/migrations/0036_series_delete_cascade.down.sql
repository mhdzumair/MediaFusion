ALTER TABLE public.episode_image DROP CONSTRAINT episode_image_episode_id_fkey;
ALTER TABLE public.episode_image
    ADD CONSTRAINT episode_image_episode_id_fkey FOREIGN KEY (episode_id) REFERENCES public.episode(id);

ALTER TABLE public.episode DROP CONSTRAINT episode_season_id_fkey;
ALTER TABLE public.episode
    ADD CONSTRAINT episode_season_id_fkey FOREIGN KEY (season_id) REFERENCES public.season(id);

ALTER TABLE public.season DROP CONSTRAINT season_series_id_fkey;
ALTER TABLE public.season
    ADD CONSTRAINT season_series_id_fkey FOREIGN KEY (series_id) REFERENCES public.series_metadata(id);
