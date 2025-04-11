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
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column(
        "base_metadata",
        sa.Column("last_stream_added", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_base_meta_last_stream_added",
        "base_metadata",
        ["last_stream_added"],
        unique=False,
    )
    op.create_index(
        "idx_last_stream_added",
        "base_metadata",
        ["last_stream_added", "type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_base_metadata_last_stream_added"),
        "base_metadata",
        ["last_stream_added"],
        unique=False,
    )
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
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table("media_catalog_link")
    op.create_table(
        "media_catalog_link",
        sa.Column("media_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("catalog_id", sa.Integer(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["catalog_id"], ["catalog.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["media_id"], ["base_metadata.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("media_id", "catalog_id"),
        postgresql_partition_by="LIST (catalog_id)",
    )
    op.create_index(
        op.f("ix_media_catalog_link_priority"),
        "media_catalog_link",
        ["priority"],
        unique=False,
    )
    op.drop_index(
        op.f("ix_base_metadata_last_stream_added"), table_name="base_metadata"
    )
    op.drop_index("idx_last_stream_added", table_name="base_metadata")
    op.drop_index("idx_base_meta_last_stream_added", table_name="base_metadata")
    op.drop_column("base_metadata", "last_stream_added")
    # ### end Alembic commands ###
