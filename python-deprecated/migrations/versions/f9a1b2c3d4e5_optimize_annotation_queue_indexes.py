"""optimize annotation queue indexes

Revision ID: f9a1b2c3d4e5
Revises: e34b6b7c8d9e
Create Date: 2026-02-28 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f9a1b2c3d4e5"
down_revision: Union[str, None] = "e34b6b7c8d9e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Speeds up stream_file -> file_media_link join on (file_id, media_id)
    # while still supporting episode_number NULL checks.
    op.create_index(
        "idx_file_media_link_file_media_episode",
        "file_media_link",
        ["file_id", "media_id", "episode_number"],
        unique=False,
    )

    # Speeds up stream -> stream_media_link filtering by stream+media pair.
    op.create_index(
        "idx_stream_media_stream_media",
        "stream_media_link",
        ["stream_id", "media_id"],
        unique=False,
    )

    # Helps active/unblocked dashboard queries sorted by newest stream first.
    op.create_index(
        "idx_stream_active_blocked_created",
        "stream",
        ["is_active", "is_blocked", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_stream_active_blocked_created", table_name="stream")
    op.drop_index("idx_stream_media_stream_media", table_name="stream_media_link")
    op.drop_index("idx_file_media_link_file_media_episode", table_name="file_media_link")
