"""add_known_file_details_to_torrent_stream

Revision ID: e7e460f99493
Revises: b2c3d4e5f6a7
Create Date: 2025-12-23 13:24:18.067789

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7e460f99493'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add known_file_details column for storing file info from debrid services
    # This allows users to fix metadata later using the stored file details
    op.add_column('torrent_stream', sa.Column('known_file_details', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('torrent_stream', 'known_file_details')
