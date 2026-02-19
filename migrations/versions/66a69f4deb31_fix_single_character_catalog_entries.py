"""fix single character catalog entries

A bug in the scrapy pipelines iterated over a catalog *string* instead of a
list, storing each character as a separate catalog (e.g. "football" became
individual rows for 'f', 'o', 't', 'b', 'a', 'l').

This migration rebuilds the correct catalog links by fingerprinting: for every
known catalog name we pre-compute the set of unique characters it contains,
then match each affected media's character-catalog set against those
fingerprints to recover the intended name.

Revision ID: 66a69f4deb31
Revises: b3c4d5e6f7a8
Create Date: 2026-02-19 11:21:25.260764

"""

from collections import defaultdict
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "66a69f4deb31"
down_revision: Union[str, None] = "b3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# All known valid catalog names (snapshot from utils/const.py CATALOG_DATA).
# Hardcoded so the migration is self-contained and won't break if app code changes.
VALID_CATALOG_NAMES = [
    "anime_movies",
    "anime_series",
    "american_football",
    "arabic_movies",
    "arabic_series",
    "baseball",
    "basketball",
    "bangla_movies",
    "bangla_series",
    "english_hdrip",
    "english_series",
    "english_tcrip",
    "football",
    "formula_racing",
    "hindi_dubbed",
    "hindi_hdrip",
    "hindi_old",
    "hindi_series",
    "hindi_tcrip",
    "hockey",
    "jackett_movies",
    "jackett_series",
    "kannada_dubbed",
    "kannada_hdrip",
    "kannada_old",
    "kannada_series",
    "kannada_tcrip",
    "live_sport_events",
    "live_tv",
    "malayalam_dubbed",
    "malayalam_hdrip",
    "malayalam_old",
    "malayalam_series",
    "malayalam_tcrip",
    "mediafusion_search_movies",
    "mediafusion_search_series",
    "mediafusion_search_tv",
    "motogp_racing",
    "other_sports",
    "prowlarr_movies",
    "prowlarr_series",
    "punjabi_movies",
    "punjabi_series",
    "rugby",
    "motor_sports",
    "tamil_dubbed",
    "tamil_hdrip",
    "tamil_old",
    "tamil_series",
    "tamil_tcrip",
    "telugu_dubbed",
    "telugu_hdrip",
    "telugu_old",
    "telugu_series",
    "telugu_tcrip",
    "fighting",
    "rss_feed_movies",
    "rss_feed_series",
    "tgx_movie",
    "tgx_series",
    "ext_to_movie",
    "ext_to_series",
    "contribution_movies",
    "contribution_series",
]


def _build_fingerprint_map() -> dict[frozenset[str], str]:
    """Map frozenset-of-characters → original catalog name.

    If two catalog names produce the same character set (collision),
    both are dropped since we can't disambiguate.
    """
    fp_map: dict[frozenset[str], str] = {}
    collisions: set[frozenset[str]] = set()

    for name in VALID_CATALOG_NAMES:
        fp = frozenset(name)
        if fp in collisions:
            continue
        if fp in fp_map:
            print(f"  WARNING: fingerprint collision between '{fp_map[fp]}' and '{name}' — skipping both")
            collisions.add(fp)
            del fp_map[fp]
        else:
            fp_map[fp] = name

    return fp_map


def upgrade() -> None:
    conn = op.get_bind()

    fingerprint_map = _build_fingerprint_map()

    # Step 1: Find all single-character catalog rows
    char_rows = conn.execute(sa.text("SELECT id, name FROM catalog WHERE LENGTH(name) = 1")).fetchall()

    if not char_rows:
        print("No single-character catalogs found. Nothing to fix.")
        return

    char_id_to_name = {row[0]: row[1] for row in char_rows}
    char_catalog_ids = list(char_id_to_name.keys())
    print(f"Found {len(char_catalog_ids)} single-character catalog entries: {sorted(char_id_to_name.values())}")

    # Step 2: Find all media linked to these character catalogs
    links = conn.execute(
        sa.text("SELECT media_id, catalog_id FROM media_catalog_link WHERE catalog_id = ANY(:ids)"),
        {"ids": char_catalog_ids},
    ).fetchall()

    if not links:
        print("No media linked to single-character catalogs. Cleaning up orphans.")
        conn.execute(
            sa.text("DELETE FROM catalog WHERE id = ANY(:ids)"),
            {"ids": char_catalog_ids},
        )
        return

    # Group character names by media_id
    media_chars: dict[int, set[str]] = defaultdict(set)
    for row in links:
        media_chars[row[0]].add(char_id_to_name[row[1]])

    print(f"Found {len(media_chars)} media entries linked to character catalogs")

    # Step 3: Match each media's character set to the correct catalog
    fixed = 0
    unmatched_count = 0
    unmatched_examples: list[tuple[int, str]] = []

    for media_id, chars in media_chars.items():
        fp = frozenset(chars)
        correct_name = fingerprint_map.get(fp)

        if not correct_name:
            unmatched_count += 1
            if len(unmatched_examples) < 10:
                unmatched_examples.append((media_id, "".join(sorted(chars))))
            continue

        # Get or create the correct catalog row
        existing = conn.execute(
            sa.text("SELECT id FROM catalog WHERE name = :name"),
            {"name": correct_name},
        ).fetchone()

        if existing:
            correct_catalog_id = existing[0]
        else:
            result = conn.execute(
                sa.text("INSERT INTO catalog (name, display_order) VALUES (:name, 100) RETURNING id"),
                {"name": correct_name},
            ).fetchone()
            correct_catalog_id = result[0]

        # Insert the correct link (skip if already exists)
        conn.execute(
            sa.text("INSERT INTO media_catalog_link (media_id, catalog_id) VALUES (:mid, :cid) ON CONFLICT DO NOTHING"),
            {"mid": media_id, "cid": correct_catalog_id},
        )

        # Remove all single-character links for this media
        conn.execute(
            sa.text("DELETE FROM media_catalog_link WHERE media_id = :mid AND catalog_id = ANY(:ids)"),
            {"mid": media_id, "ids": char_catalog_ids},
        )

        fixed += 1

    # Step 4: Clean up orphaned single-character catalog rows
    deleted = conn.execute(
        sa.text(
            "DELETE FROM catalog WHERE id = ANY(:ids) "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM media_catalog_link WHERE catalog_id = catalog.id"
            ") RETURNING id"
        ),
        {"ids": char_catalog_ids},
    ).fetchall()

    print(f"Fixed {fixed} media entries")
    if unmatched_count:
        print(f"Could not match {unmatched_count} media entries (character sets didn't match any known catalog)")
        for mid, chars in unmatched_examples:
            print(f"  media_id={mid} chars='{chars}'")
    print(f"Cleaned up {len(deleted)} orphaned single-character catalog rows")


def downgrade() -> None:
    # Data-only migration — the broken state cannot be meaningfully restored.
    pass
