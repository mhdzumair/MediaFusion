CREATE TABLE public.user_telegram_sessions (
    user_id integer NOT NULL,
    encrypted_session character varying NOT NULL,
    telegram_account_id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    last_used_at timestamp with time zone,
    CONSTRAINT user_telegram_sessions_pkey PRIMARY KEY (user_id),
    CONSTRAINT user_telegram_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE
);

CREATE INDEX idx_user_telegram_sessions_account ON public.user_telegram_sessions USING btree (telegram_account_id);
