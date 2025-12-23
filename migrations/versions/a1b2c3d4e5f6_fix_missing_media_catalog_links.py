"""fix_missing_media_catalog_links

Revision ID: a1b2c3d4e5f6
Revises: 5633887f53ad
Create Date: 2025-12-22 16:00:00.000000

This migration fixes missing MediaCatalogLink entries that should have been
created when torrent streams were added. The CatalogStreamStats table was
being populated, but MediaCatalogLink was not, causing catalog queries to
return empty results.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '5633887f53ad'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Fix missing MediaCatalogLink entries from CatalogStreamStats
    # The CatalogStreamStats table has catalog associations that should also
    # exist in MediaCatalogLink for the catalog query to work correctly
    op.execute("""
        INSERT INTO media_catalog_link (media_id, catalog_id)
        SELECT DISTINCT css.media_id, css.catalog_id 
        FROM catalog_stream_stats css
        WHERE NOT EXISTS (
            SELECT 1 FROM media_catalog_link mcl 
            WHERE mcl.media_id = css.media_id AND mcl.catalog_id = css.catalog_id
        )
        ON CONFLICT (media_id, catalog_id) DO NOTHING;
    """)


def downgrade() -> None:
    # We don't want to remove catalog links on downgrade as they may have been
    # created legitimately through other means. This is a data fix, not a schema change.
    pass


