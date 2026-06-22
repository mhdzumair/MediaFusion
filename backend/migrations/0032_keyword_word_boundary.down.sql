-- Revert to substring (position-based) keyword matching.

CREATE OR REPLACE FUNCTION check_media_keyword_blocked()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    ltitle text := LOWER(NEW.title);
    ldesc  text := LOWER(COALESCE(NEW.description, ''));
BEGIN
    NEW.is_keyword_blocked := (
        NEW.adult = true
        OR (
            EXISTS (
                SELECT 1 FROM keyword_filters kf
                WHERE kf.is_active = true
                  AND kf.scope IN ('all', 'media')
                  AND (
                      position(LOWER(kf.keyword) IN ltitle) > 0
                      OR position(LOWER(kf.keyword) IN ldesc) > 0
                  )
            )
            AND NOT EXISTS (
                SELECT 1 FROM keyword_whitelist kw
                WHERE position(LOWER(kw.phrase) IN ltitle) > 0
                   OR position(LOWER(kw.phrase) IN ldesc) > 0
            )
        )
    );
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION check_stream_keyword_blocked()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    lname text := LOWER(NEW.name);
BEGIN
    NEW.is_keyword_blocked := (
        EXISTS (
            SELECT 1 FROM keyword_filters kf
            WHERE kf.is_active = true
              AND kf.scope IN ('all', 'stream')
              AND position(LOWER(kf.keyword) IN lname) > 0
        )
        AND NOT EXISTS (
            SELECT 1 FROM keyword_whitelist kw
            WHERE position(LOWER(kw.phrase) IN lname) > 0
        )
    );
    RETURN NEW;
END;
$$;

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

DELETE FROM keyword_sync_state WHERE id IN (
    'keyword-blocked-recompute',
    'stream-keyword-blocked-recompute'
);
