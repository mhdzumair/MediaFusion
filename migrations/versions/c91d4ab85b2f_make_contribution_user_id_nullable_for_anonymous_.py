"""make contribution user_id nullable for anonymous submissions

Revision ID: c91d4ab85b2f
Revises: b0ddd67b439e
Create Date: 2026-02-21 14:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c91d4ab85b2f"
down_revision: Union[str, None] = "b0ddd67b439e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("contributions", "user_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    # Rows created as truly anonymous have no user_id and cannot be restored.
    op.execute(sa.text("DELETE FROM contributions WHERE user_id IS NULL"))
    op.alter_column("contributions", "user_id", existing_type=sa.Integer(), nullable=False)
