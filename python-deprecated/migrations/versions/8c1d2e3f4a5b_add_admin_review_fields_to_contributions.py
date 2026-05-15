"""add admin review fields to contributions

Revision ID: 8c1d2e3f4a5b
Revises: 7ab8c9d0e1f2
Create Date: 2026-03-04 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8c1d2e3f4a5b"
down_revision: Union[str, None] = "7ab8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "contributions",
        sa.Column("admin_review_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "contributions",
        sa.Column("admin_review_requested_by", sa.String(), nullable=True),
    )
    op.add_column(
        "contributions",
        sa.Column("admin_review_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "contributions",
        sa.Column("admin_review_reason", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_contribution_admin_review_requested",
        "contributions",
        ["admin_review_requested"],
    )
    op.alter_column("contributions", "admin_review_requested", server_default=None)


def downgrade() -> None:
    op.drop_index("idx_contribution_admin_review_requested", table_name="contributions")
    op.drop_column("contributions", "admin_review_reason")
    op.drop_column("contributions", "admin_review_requested_at")
    op.drop_column("contributions", "admin_review_requested_by")
    op.drop_column("contributions", "admin_review_requested")
