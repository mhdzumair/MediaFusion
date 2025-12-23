"""Add RSSFeed, CatalogStreamStats tables and new fields

Revision ID: d1a2b3c4d5e6
Revises: 7f6e3631b327
Create Date: 2024-12-21 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d1a2b3c4d5e6"
down_revision: Union[str, None] = "7f6e3631b327"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create TorrentType enum if it doesn't exist
    torrent_type_enum = postgresql.ENUM(
        "PUBLIC", "SEMI_PRIVATE", "PRIVATE", name="torrenttype", create_type=False
    )

    conn = op.get_bind()
    # Check if enum exists
    result = conn.execute(
        sa.text("SELECT 1 FROM pg_type WHERE typname = 'torrenttype'")
    )
    if not result.scalar():
        torrent_type_enum.create(conn)

    # ### New Tables ###

    # Create rss_feed table
    op.create_table(
        "rss_feed",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("url", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, default=True),
        sa.Column("last_scraped", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column(
            "torrent_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False, default="public"
        ),
        sa.Column("auto_detect_catalog", sa.Boolean(), nullable=False, default=False),
        sa.Column("parsing_patterns", sa.JSON(), nullable=True),
        sa.Column("filters", sa.JSON(), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
    )
    op.create_index(op.f("ix_rss_feed_name"), "rss_feed", ["name"], unique=False)
    op.create_index(op.f("ix_rss_feed_active"), "rss_feed", ["active"], unique=False)
    op.create_index(op.f("ix_rss_feed_source"), "rss_feed", ["source"], unique=False)
    op.create_index(
        op.f("ix_rss_feed_updated_at"), "rss_feed", ["updated_at"], unique=False
    )

    # Create rss_feed_catalog_pattern table
    op.create_table(
        "rss_feed_catalog_pattern",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rss_feed_id", sa.Integer(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("regex", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, default=True),
        sa.Column("case_sensitive", sa.Boolean(), nullable=False, default=False),
        sa.Column("target_catalogs", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["rss_feed_id"], ["rss_feed.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_rss_feed_catalog_pattern_rss_feed_id"),
        "rss_feed_catalog_pattern",
        ["rss_feed_id"],
        unique=False,
    )

    # Create catalog_stream_stats table
    op.create_table(
        "catalog_stream_stats",
        sa.Column("media_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("catalog_id", sa.Integer(), nullable=False),
        sa.Column("total_streams", sa.Integer(), nullable=False, default=0),
        sa.Column("last_stream_added", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["media_id"], ["base_metadata.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["catalog_id"], ["catalog.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("media_id", "catalog_id"),
    )
    op.create_index(
        "idx_catalog_stats_lookup",
        "catalog_stream_stats",
        ["catalog_id", "total_streams", "last_stream_added"],
        unique=False,
    )

    # ### New Columns on Existing Tables ###

    # Add total_streams to base_metadata
    op.add_column(
        "base_metadata",
        sa.Column("total_streams", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        op.f("ix_base_metadata_total_streams"),
        "base_metadata",
        ["total_streams"],
        unique=False,
    )

    # Add tmdb_rating to movie_metadata
    op.add_column(
        "movie_metadata",
        sa.Column("tmdb_rating", sa.Float(), nullable=True),
    )
    op.create_index(
        op.f("ix_movie_metadata_tmdb_rating"),
        "movie_metadata",
        ["tmdb_rating"],
        unique=False,
    )

    # Add tmdb_rating to series_metadata
    op.add_column(
        "series_metadata",
        sa.Column("tmdb_rating", sa.Float(), nullable=True),
    )
    op.create_index(
        op.f("ix_series_metadata_tmdb_rating"),
        "series_metadata",
        ["tmdb_rating"],
        unique=False,
    )

    # Add new columns to torrent_stream
    op.add_column(
        "torrent_stream",
        sa.Column("uploader", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.add_column(
        "torrent_stream",
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "torrent_stream",
        sa.Column("hdr", sa.JSON(), nullable=True),
    )
    op.add_column(
        "torrent_stream",
        sa.Column("torrent_file", sa.LargeBinary(), nullable=True),
    )

    # Rename indexer_flag to torrent_type (if column exists with old name)
    # First check if the old column exists
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col["name"] for col in inspector.get_columns("torrent_stream")]

    if "indexer_flag" in columns and "torrent_type" not in columns:
        # Add new column with correct enum type
        op.add_column(
            "torrent_stream",
            sa.Column(
                "torrent_type",
                torrent_type_enum,
                nullable=False,
                server_default="PUBLIC",
            ),
        )
        # Migrate data from old column to new column
        op.execute(
            """
            UPDATE torrent_stream 
            SET torrent_type = 
                CASE indexer_flag::text
                    WHEN 'FREELEACH' THEN 'PUBLIC'::torrenttype
                    WHEN 'SEMI_PRIVATE' THEN 'SEMI_PRIVATE'::torrenttype
                    WHEN 'PRIVATE' THEN 'PRIVATE'::torrenttype
                    ELSE 'PUBLIC'::torrenttype
                END
            """
        )
        # Drop old column
        op.drop_column("torrent_stream", "indexer_flag")
    elif "torrent_type" not in columns:
        # Fresh install - just add the column
        op.add_column(
            "torrent_stream",
            sa.Column(
                "torrent_type",
                torrent_type_enum,
                nullable=False,
                server_default="PUBLIC",
            ),
        )

    # Create index for episode_file lookups
    op.create_index(
        "idx_episode_file_lookup",
        "episode_file",
        ["torrent_stream_id", "season_number", "episode_number"],
        unique=False,
    )


def downgrade() -> None:
    # Drop new indexes
    op.drop_index("idx_episode_file_lookup", table_name="episode_file")

    # Drop new columns from torrent_stream
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col["name"] for col in inspector.get_columns("torrent_stream")]

    if "torrent_type" in columns:
        # Recreate indexer_flag enum if needed
        indexer_type_enum = postgresql.ENUM(
            "FREELEACH", "SEMI_PRIVATE", "PRIVATE", name="indexertype", create_type=False
        )
        result = conn.execute(
            sa.text("SELECT 1 FROM pg_type WHERE typname = 'indexertype'")
        )
        if not result.scalar():
            indexer_type_enum.create(conn)

        op.add_column(
            "torrent_stream",
            sa.Column(
                "indexer_flag",
                indexer_type_enum,
                nullable=False,
                server_default="FREELEACH",
            ),
        )
        op.execute(
            """
            UPDATE torrent_stream 
            SET indexer_flag = 
                CASE torrent_type::text
                    WHEN 'PUBLIC' THEN 'FREELEACH'::indexertype
                    WHEN 'SEMI_PRIVATE' THEN 'SEMI_PRIVATE'::indexertype
                    WHEN 'PRIVATE' THEN 'PRIVATE'::indexertype
                    ELSE 'FREELEACH'::indexertype
                END
            """
        )
        op.drop_column("torrent_stream", "torrent_type")

    if "torrent_file" in columns:
        op.drop_column("torrent_stream", "torrent_file")
    if "hdr" in columns:
        op.drop_column("torrent_stream", "hdr")
    if "uploaded_at" in columns:
        op.drop_column("torrent_stream", "uploaded_at")
    if "uploader" in columns:
        op.drop_column("torrent_stream", "uploader")

    # Drop tmdb_rating indexes and columns
    op.drop_index(op.f("ix_series_metadata_tmdb_rating"), table_name="series_metadata")
    op.drop_column("series_metadata", "tmdb_rating")
    op.drop_index(op.f("ix_movie_metadata_tmdb_rating"), table_name="movie_metadata")
    op.drop_column("movie_metadata", "tmdb_rating")

    # Drop total_streams from base_metadata
    op.drop_index(op.f("ix_base_metadata_total_streams"), table_name="base_metadata")
    op.drop_column("base_metadata", "total_streams")

    # Drop catalog_stream_stats table
    op.drop_index("idx_catalog_stats_lookup", table_name="catalog_stream_stats")
    op.drop_table("catalog_stream_stats")

    # Drop rss_feed_catalog_pattern table
    op.drop_index(
        op.f("ix_rss_feed_catalog_pattern_rss_feed_id"),
        table_name="rss_feed_catalog_pattern",
    )
    op.drop_table("rss_feed_catalog_pattern")

    # Drop rss_feed table
    op.drop_index(op.f("ix_rss_feed_updated_at"), table_name="rss_feed")
    op.drop_index(op.f("ix_rss_feed_source"), table_name="rss_feed")
    op.drop_index(op.f("ix_rss_feed_active"), table_name="rss_feed")
    op.drop_index(op.f("ix_rss_feed_name"), table_name="rss_feed")
    op.drop_table("rss_feed")

    # Drop torrenttype enum
    op.execute("DROP TYPE IF EXISTS torrenttype;")

