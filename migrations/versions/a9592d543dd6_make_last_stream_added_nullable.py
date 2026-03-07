"""make_last_stream_added_nullable

Revision ID: a9592d543dd6
Revises: 3cccb30fad51
Create Date: 2026-03-08 00:16:58.773424

"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a9592d543dd6"
down_revision: Union[str, None] = "3cccb30fad51"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "media",
        "last_stream_added",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "media",
        "last_stream_added",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        nullable=False,
    )
