"""add_missing_series_episode_columns

Revision ID: 55db6bd4aab2
Revises: 8d55e5e54b6a
Create Date: 2025-12-21 07:46:21.753721

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '55db6bd4aab2'
down_revision: Union[str, None] = '8d55e5e54b6a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new columns
    op.add_column('series_episode', sa.Column('overview', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column('series_episode', sa.Column('released', sa.DateTime(timezone=True), nullable=True))
    op.add_column('series_episode', sa.Column('thumbnail', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    
    # Migrate data from old columns to new columns before dropping
    op.execute("UPDATE series_episode SET overview = plot WHERE plot IS NOT NULL")
    op.execute("UPDATE series_episode SET released = air_date WHERE air_date IS NOT NULL")
    op.execute("UPDATE series_episode SET thumbnail = poster WHERE poster IS NOT NULL")
    
    # Drop old columns (data has been migrated or will be repopulated from external sources)
    op.drop_column('series_episode', 'is_poster_working')
    op.drop_column('series_episode', 'plot')
    op.drop_column('series_episode', 'poster')
    op.drop_column('series_episode', 'air_date')
    op.drop_column('series_episode', 'runtime')


def downgrade() -> None:
    # Add old columns back
    op.add_column('series_episode', sa.Column('runtime', sa.INTEGER(), autoincrement=False, nullable=True))
    op.add_column('series_episode', sa.Column('air_date', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True))
    op.add_column('series_episode', sa.Column('poster', sa.VARCHAR(), autoincrement=False, nullable=True))
    op.add_column('series_episode', sa.Column('plot', sa.VARCHAR(), autoincrement=False, nullable=True))
    
    # Add is_poster_working as nullable first
    op.add_column('series_episode', sa.Column('is_poster_working', sa.BOOLEAN(), autoincrement=False, nullable=True))
    # Set default value for existing rows
    op.execute("UPDATE series_episode SET is_poster_working = FALSE WHERE is_poster_working IS NULL")
    # Alter to NOT NULL
    op.alter_column('series_episode', 'is_poster_working', nullable=False)
    
    # Migrate data back from new columns to old columns
    op.execute("UPDATE series_episode SET plot = overview WHERE overview IS NOT NULL")
    op.execute("UPDATE series_episode SET air_date = released WHERE released IS NOT NULL")
    op.execute("UPDATE series_episode SET poster = thumbnail WHERE thumbnail IS NOT NULL")
    
    # Drop new columns
    op.drop_column('series_episode', 'thumbnail')
    op.drop_column('series_episode', 'released')
    op.drop_column('series_episode', 'overview')
