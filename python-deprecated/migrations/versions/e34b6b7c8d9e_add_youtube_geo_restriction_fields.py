"""add youtube geo restriction fields

Revision ID: e34b6b7c8d9e
Revises: d7c1a8f4b2e9
Create Date: 2026-02-26 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e34b6b7c8d9e"
down_revision: Union[str, None] = "d7c1a8f4b2e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("youtube_stream", sa.Column("geo_restriction_type", sa.String(), nullable=True))
    op.add_column(
        "youtube_stream",
        sa.Column(
            "geo_restriction_countries",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("youtube_stream", "geo_restriction_countries")
    op.drop_column("youtube_stream", "geo_restriction_type")
