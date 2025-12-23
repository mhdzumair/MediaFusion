"""fix_contribution_catalog_names

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2025-12-22 16:30:00.000000

This migration fixes the contribution catalog naming from 'contribution_stream'
to 'contribution_movies' and 'contribution_series' based on the media type.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create new catalogs if they don't exist
    op.execute("""
        INSERT INTO catalog (name) VALUES ('contribution_movies')
        ON CONFLICT (name) DO NOTHING;
    """)
    op.execute("""
        INSERT INTO catalog (name) VALUES ('contribution_series')
        ON CONFLICT (name) DO NOTHING;
    """)
    
    # Migrate media_catalog_link entries from contribution_stream to correct catalog
    # For movies (using enum value 'MOVIE')
    op.execute("""
        INSERT INTO media_catalog_link (media_id, catalog_id)
        SELECT mcl.media_id, (SELECT id FROM catalog WHERE name = 'contribution_movies')
        FROM media_catalog_link mcl
        JOIN catalog c ON c.id = mcl.catalog_id
        JOIN base_metadata bm ON bm.id = mcl.media_id
        WHERE c.name = 'contribution_stream' AND bm.type = 'MOVIE'
        ON CONFLICT (media_id, catalog_id) DO NOTHING;
    """)
    
    # For series (using enum value 'SERIES')
    op.execute("""
        INSERT INTO media_catalog_link (media_id, catalog_id)
        SELECT mcl.media_id, (SELECT id FROM catalog WHERE name = 'contribution_series')
        FROM media_catalog_link mcl
        JOIN catalog c ON c.id = mcl.catalog_id
        JOIN base_metadata bm ON bm.id = mcl.media_id
        WHERE c.name = 'contribution_stream' AND bm.type = 'SERIES'
        ON CONFLICT (media_id, catalog_id) DO NOTHING;
    """)
    
    # Migrate catalog_stream_stats entries
    # For movies
    op.execute("""
        INSERT INTO catalog_stream_stats (media_id, catalog_id, total_streams, last_stream_added)
        SELECT css.media_id, 
               (SELECT id FROM catalog WHERE name = 'contribution_movies'),
               css.total_streams,
               css.last_stream_added
        FROM catalog_stream_stats css
        JOIN catalog c ON c.id = css.catalog_id
        JOIN base_metadata bm ON bm.id = css.media_id
        WHERE c.name = 'contribution_stream' AND bm.type = 'MOVIE'
        ON CONFLICT (media_id, catalog_id) DO UPDATE SET
            total_streams = EXCLUDED.total_streams,
            last_stream_added = EXCLUDED.last_stream_added;
    """)
    
    # For series
    op.execute("""
        INSERT INTO catalog_stream_stats (media_id, catalog_id, total_streams, last_stream_added)
        SELECT css.media_id, 
               (SELECT id FROM catalog WHERE name = 'contribution_series'),
               css.total_streams,
               css.last_stream_added
        FROM catalog_stream_stats css
        JOIN catalog c ON c.id = css.catalog_id
        JOIN base_metadata bm ON bm.id = css.media_id
        WHERE c.name = 'contribution_stream' AND bm.type = 'SERIES'
        ON CONFLICT (media_id, catalog_id) DO UPDATE SET
            total_streams = EXCLUDED.total_streams,
            last_stream_added = EXCLUDED.last_stream_added;
    """)
    
    # Delete old contribution_stream entries from media_catalog_link
    op.execute("""
        DELETE FROM media_catalog_link
        WHERE catalog_id = (SELECT id FROM catalog WHERE name = 'contribution_stream');
    """)
    
    # Delete old contribution_stream entries from catalog_stream_stats
    op.execute("""
        DELETE FROM catalog_stream_stats
        WHERE catalog_id = (SELECT id FROM catalog WHERE name = 'contribution_stream');
    """)
    
    # Optionally delete the old catalog (commented out in case it's needed)
    # op.execute("DELETE FROM catalog WHERE name = 'contribution_stream';")


def downgrade() -> None:
    # Reverse: merge contribution_movies and contribution_series back to contribution_stream
    op.execute("""
        INSERT INTO catalog (name) VALUES ('contribution_stream')
        ON CONFLICT (name) DO NOTHING;
    """)
    
    # Move entries back
    op.execute("""
        INSERT INTO media_catalog_link (media_id, catalog_id)
        SELECT mcl.media_id, (SELECT id FROM catalog WHERE name = 'contribution_stream')
        FROM media_catalog_link mcl
        JOIN catalog c ON c.id = mcl.catalog_id
        WHERE c.name IN ('contribution_movies', 'contribution_series')
        ON CONFLICT (media_id, catalog_id) DO NOTHING;
    """)
    
    # Delete the new catalog entries
    op.execute("""
        DELETE FROM media_catalog_link
        WHERE catalog_id IN (SELECT id FROM catalog WHERE name IN ('contribution_movies', 'contribution_series'));
    """)

