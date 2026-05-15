"""add uploads_restricted flag to users

Revision ID: a4b5c6d7e8f9
Revises: 9d2e3f4a5b6c
Create Date: 2026-03-04 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4b5c6d7e8f9"
down_revision: Union[str, None] = "9d2e3f4a5b6c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("uploads_restricted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("idx_user_uploads_restricted", "users", ["uploads_restricted"])
    op.alter_column("users", "uploads_restricted", server_default=None)


def downgrade() -> None:
    op.drop_index("idx_user_uploads_restricted", table_name="users")
    op.drop_column("users", "uploads_restricted")
