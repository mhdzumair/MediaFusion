-- Remove the Adult genre → adult flag propagation trigger.
-- Does NOT revert the adult flag on media rows (safer than unblocking content).
DROP TRIGGER IF EXISTS trg_adult_genre_link ON media_genre_link;
DROP FUNCTION IF EXISTS propagate_adult_genre();
