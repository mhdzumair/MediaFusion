-- Genre management: add per-(genre, media-type) pairing table with is_hidden flag.
-- Backfills from existing media-genre links; seeds adult genres as hidden.

CREATE TABLE genre_media_type (
    genre_id   integer     NOT NULL REFERENCES genre(id) ON DELETE CASCADE,
    media_type varchar     NOT NULL,  -- wire form: 'movie' | 'series' | 'tv' | 'events'
    is_hidden  boolean     NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT NOW(),
    PRIMARY KEY (genre_id, media_type)
);

-- Supports fast "all visible genres for type X" lookups used by manifest + catalog.
CREATE INDEX ix_genre_media_type_type_hidden ON genre_media_type(media_type, is_hidden);

-- Backfill: derive (genre_id, media_type) from existing media-genre links.
-- lower(m.type::text) converts the 'MOVIE'/'SERIES'/'TV' postgres enum to wire format.
INSERT INTO genre_media_type (genre_id, media_type)
SELECT DISTINCT mgl.genre_id, lower(m.type::text)
FROM   media_genre_link mgl
JOIN   media m ON m.id = mgl.media_id
ON CONFLICT DO NOTHING;

-- Seed adult genres as hidden (replaces the hardcoded ADULT_GENRE_NAMES Rust filter).
UPDATE genre_media_type
SET    is_hidden = true
WHERE  genre_id IN (SELECT id FROM genre WHERE lower(name) IN ('adult', '18+'));
