-- Revert to substring keyword matching (no word boundaries).
-- Word-boundary (\y) matching introduced in migration 0032 was too strict:
-- it missed plurals and conjugations like "whores" for keyword "whore".
-- Substring match means any occurrence in the text triggers the block;
-- use the whitelist for any known false positives (e.g. "cocktail").

-- ── Media trigger ─────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION check_media_keyword_blocked()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    ltitle text := LOWER(NEW.title);
    ldesc  text := LOWER(COALESCE(NEW.description, ''));
    kw_pattern text;
    wl_pattern text;
BEGIN
    SELECT '(' || string_agg(
               regexp_replace(LOWER(keyword), '([.^$*+?()[\]{}|\\])', '\\\1', 'g'),
               '|' ORDER BY keyword
           ) || ')'
    INTO kw_pattern
    FROM keyword_filters WHERE is_active = true AND scope IN ('all', 'media');

    SELECT '(' || string_agg(
               regexp_replace(LOWER(phrase), '([.^$*+?()[\]{}|\\])', '\\\1', 'g'),
               '|' ORDER BY phrase
           ) || ')'
    INTO wl_pattern
    FROM keyword_whitelist;

    NEW.is_keyword_blocked := (
        NEW.adult = true
        OR (
            kw_pattern IS NOT NULL
            AND (ltitle ~ kw_pattern OR ldesc ~ kw_pattern)
            AND (wl_pattern IS NULL OR NOT (ltitle ~ wl_pattern OR ldesc ~ wl_pattern))
        )
    );
    RETURN NEW;
END;
$$;

-- ── Stream trigger ─────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION check_stream_keyword_blocked()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    lname text := LOWER(NEW.name);
    kw_pattern text;
    wl_pattern text;
BEGIN
    SELECT '(' || string_agg(
               regexp_replace(LOWER(keyword), '([.^$*+?()[\]{}|\\])', '\\\1', 'g'),
               '|' ORDER BY keyword
           ) || ')'
    INTO kw_pattern
    FROM keyword_filters WHERE is_active = true AND scope IN ('all', 'stream');

    SELECT '(' || string_agg(
               regexp_replace(LOWER(phrase), '([.^$*+?()[\]{}|\\])', '\\\1', 'g'),
               '|' ORDER BY phrase
           ) || ')'
    INTO wl_pattern
    FROM keyword_whitelist;

    NEW.is_keyword_blocked := (
        kw_pattern IS NOT NULL
        AND (lname ~ kw_pattern)
        AND (wl_pattern IS NULL OR NOT (lname ~ wl_pattern))
    );
    RETURN NEW;
END;
$$;

-- ── PL/pgSQL batch recompute ──────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION recompute_all_keyword_blocked()
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    kw_pattern text;
    wl_pattern text;
BEGIN
    SELECT '(' || string_agg(
               regexp_replace(LOWER(keyword), '([.^$*+?()[\]{}|\\])', '\\\1', 'g'),
               '|' ORDER BY keyword
           ) || ')'
    INTO kw_pattern
    FROM keyword_filters WHERE is_active = true AND scope IN ('all', 'media');

    SELECT '(' || string_agg(
               regexp_replace(LOWER(phrase), '([.^$*+?()[\]{}|\\])', '\\\1', 'g'),
               '|' ORDER BY phrase
           ) || ')'
    INTO wl_pattern
    FROM keyword_whitelist;

    WITH computed AS (
        SELECT id,
               adult = true
               OR (
                   kw_pattern IS NOT NULL
                   AND (
                       LOWER(title) ~ kw_pattern
                       OR LOWER(COALESCE(description, '')) ~ kw_pattern
                   )
                   AND (
                       wl_pattern IS NULL
                       OR NOT (
                           LOWER(title) ~ wl_pattern
                           OR LOWER(COALESCE(description, '')) ~ wl_pattern
                       )
                   )
               ) AS new_blocked
        FROM media
    )
    UPDATE media m
    SET is_keyword_blocked = computed.new_blocked
    FROM computed
    WHERE m.id = computed.id
      AND m.is_keyword_blocked IS DISTINCT FROM computed.new_blocked;
END;
$$;

-- Force Rust batch recompute on next startup for both media and streams.
DELETE FROM keyword_sync_state WHERE id IN (
    'keyword-blocked-recompute',
    'stream-keyword-blocked-recompute'
);
