"""fix_unique_constraint_title_year

Revision ID: 8d55e5e54b6a
Revises: d1a2b3c4d5e6
Create Date: 2025-12-21 03:19:14.834512

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8d55e5e54b6a'
down_revision: Union[str, None] = 'd1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the overly restrictive unique constraint on (title, year)
    # Multiple movies can have the same title and year with different IDs
    op.drop_constraint('base_metadata_title_year_key', 'base_metadata', type_='unique')
    
    # Create a partial unique index for custom IDs (mf* prefix) only
    # This matches the MongoDB behavior where uniqueness is only enforced for is_custom=True
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_base_metadata_custom_title_year_type 
        ON base_metadata (title, year, type) 
        WHERE id LIKE 'mf%'
    """)


def downgrade() -> None:
    # Drop the partial index
    op.execute("DROP INDEX IF EXISTS ix_base_metadata_custom_title_year_type")
    
    # Recreate the unique constraint (may fail if duplicates exist)
    op.create_unique_constraint('base_metadata_title_year_key', 'base_metadata', ['title', 'year'])
