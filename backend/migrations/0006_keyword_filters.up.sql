CREATE TABLE public.keyword_filters (
    id serial PRIMARY KEY,
    keyword character varying NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamp with time zone NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX idx_keyword_filters_keyword ON public.keyword_filters (LOWER(keyword));

CREATE TABLE public.keyword_whitelist (
    id serial PRIMARY KEY,
    phrase character varying NOT NULL,
    reason character varying,
    created_at timestamp with time zone NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX idx_keyword_whitelist_phrase ON public.keyword_whitelist (LOWER(phrase));
