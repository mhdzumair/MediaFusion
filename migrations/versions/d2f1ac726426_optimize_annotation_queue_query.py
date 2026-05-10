"""optimize annotation queue query

Revision ID: d2f1ac726426
Revises: baeb5c5638b8
Create Date: 2026-05-09 22:29:53.523109

stream_media_link already has idx_stream_media_media (media_id) and
idx_stream_media_link_media_stream (media_id, stream_id) — no new index needed there.
The query rewrite in the Rust handler uses those existing indexes.
"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "d2f1ac726426"
down_revision: Union[str, None] = "baeb5c5638b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
