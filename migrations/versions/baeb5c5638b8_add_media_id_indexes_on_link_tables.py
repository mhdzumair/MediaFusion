"""add media_id indexes on link tables

Revision ID: baeb5c5638b8
Revises: a1b2c3d4e5f6
Create Date: 2026-05-09 06:27:15.559812

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "baeb5c5638b8"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("idx_media_genre_link_media_id", "media_genre_link", ["media_id"], unique=False)
    op.create_index("idx_media_catalog_link_media_id", "media_catalog_link", ["media_id"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_media_genre_link_media_id", table_name="media_genre_link")
    op.drop_index("idx_media_catalog_link_media_id", table_name="media_catalog_link")
