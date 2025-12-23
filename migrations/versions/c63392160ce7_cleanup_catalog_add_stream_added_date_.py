"""cleanup catalog & add stream added date for sorting metadata

Revision ID: c63392160ce7
Revises: 4829e203ecaf
Create Date: 2024-11-23 14:05:46.061957

"""

from typing import Sequence, Union

from alembic import op
import sqlmodel.sql.sqltypes
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c63392160ce7"
down_revision: Union[str, None] = "4829e203ecaf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add last_stream_added column
    op.add_column(
        "base_metadata",
        sa.Column("last_stream_added", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    # Create composite index for filtering by last_stream_added and type
    op.create_index(
        "idx_last_stream_added",
        "base_metadata",
        ["last_stream_added", "type"],
        unique=False,
    )
    # Create single-column index for sorting by last_stream_added
    op.create_index(
        op.f("ix_base_metadata_last_stream_added"),
        "base_metadata",
        ["last_stream_added"],
        unique=False,
    )
    
    # Recreate media_catalog_link without priority column (preserve data)
    # First, backup existing data
    op.execute("""
        CREATE TABLE media_catalog_link_backup AS 
        SELECT media_id, catalog_id FROM media_catalog_link
    """)
    op.drop_index(
        op.f("ix_media_catalog_link_priority"), table_name="media_catalog_link"
    )
    op.drop_table("media_catalog_link")
    op.create_table(
        "media_catalog_link",
        sa.Column("media_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("catalog_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["catalog_id"], ["catalog.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["media_id"], ["base_metadata.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("media_id", "catalog_id"),
    )
    # Restore data from backup
    op.execute("""
        INSERT INTO media_catalog_link (media_id, catalog_id)
        SELECT media_id, catalog_id FROM media_catalog_link_backup
    """)
    op.execute("DROP TABLE media_catalog_link_backup")


def downgrade() -> None:
    # Recreate media_catalog_link with priority column (preserve data)
    op.execute("""
        CREATE TABLE media_catalog_link_backup AS 
        SELECT media_id, catalog_id FROM media_catalog_link
    """)
    op.drop_table("media_catalog_link")
    op.create_table(
        "media_catalog_link",
        sa.Column("media_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("catalog_id", sa.Integer(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["catalog_id"], ["catalog.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["media_id"], ["base_metadata.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("media_id", "catalog_id"),
    )
    # Restore data with default priority of 0
    op.execute("""
        INSERT INTO media_catalog_link (media_id, catalog_id, priority)
        SELECT media_id, catalog_id, 0 FROM media_catalog_link_backup
    """)
    op.execute("DROP TABLE media_catalog_link_backup")
    op.create_index(
        op.f("ix_media_catalog_link_priority"),
        "media_catalog_link",
        ["priority"],
        unique=False,
    )
    
    # Drop last_stream_added indexes and column
    op.drop_index(
        op.f("ix_base_metadata_last_stream_added"), table_name="base_metadata"
    )
    op.drop_index("idx_last_stream_added", table_name="base_metadata")
    op.drop_column("base_metadata", "last_stream_added")
