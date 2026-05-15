"""add annotation request dismissal table

Revision ID: 9d2e3f4a5b6c
Revises: 8c1d2e3f4a5b
Create Date: 2026-03-04 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9d2e3f4a5b6c"
down_revision: Union[str, None] = "8c1d2e3f4a5b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "annotation_request_dismissal",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stream_id", sa.Integer(), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.Column("dismissed_by", sa.String(), nullable=False),
        sa.Column("dismiss_reason", sa.Text(), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stream_id"], ["stream.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stream_id", "media_id"),
    )
    op.create_index(
        "idx_annotation_dismissal_stream",
        "annotation_request_dismissal",
        ["stream_id"],
    )
    op.create_index(
        "idx_annotation_dismissal_media",
        "annotation_request_dismissal",
        ["media_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_annotation_dismissal_media", table_name="annotation_request_dismissal")
    op.drop_index("idx_annotation_dismissal_stream", table_name="annotation_request_dismissal")
    op.drop_table("annotation_request_dismissal")
