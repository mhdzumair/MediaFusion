"""set default false for contribution admin review flag

Revision ID: 3cccb30fad51
Revises: fc2804ba1ac9
Create Date: 2026-03-06 14:07:59.212776

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "3cccb30fad51"
down_revision: Union[str, None] = "fc2804ba1ac9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "contributions",
        "admin_review_requested",
        existing_type=sa.Boolean(),
        server_default=sa.false(),
    )


def downgrade() -> None:
    op.alter_column(
        "contributions",
        "admin_review_requested",
        existing_type=sa.Boolean(),
        server_default=None,
    )
