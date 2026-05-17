DROP TABLE IF EXISTS public.keyword_sync_state;
ALTER TABLE public.keyword_whitelist DROP COLUMN IF EXISTS source;
ALTER TABLE public.keyword_filters   DROP COLUMN IF EXISTS source;
