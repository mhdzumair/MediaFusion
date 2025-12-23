"""add_web_seed_to_torrenttype_enum

Revision ID: 32962aacd8dc
Revises: 64ab9417af09
Create Date: 2025-12-21 11:27:30.264244

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '32962aacd8dc'
down_revision: Union[str, None] = '64ab9417af09'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add WEB_SEED to torrenttype enum
    # PostgreSQL requires using ALTER TYPE to add enum values
    op.execute("ALTER TYPE torrenttype ADD VALUE IF NOT EXISTS 'WEB_SEED'")


def downgrade() -> None:
    # Note: PostgreSQL doesn't support removing enum values directly
    # This would require recreating the enum type
    pass
