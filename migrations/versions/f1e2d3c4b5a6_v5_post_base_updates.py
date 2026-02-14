"""MediaFusion v5.0 Post-Base Updates (Consolidated)

This migration consolidates all schema changes after the v5.0 base schema.
It combines the following migrations into a single file:
- a1b2c3d4e5f6: Unified watch history with integration sync state
- 17334c3ffa57: Add profile_integration table
- b6974f19eb9a: Fix watchaction enum type
- 641f2ff525e4: Fix watchaction enum values uppercase
- 302b751510cd: Add history source field
- a675cdb2e88a: Add broken_report_threshold to contribution_settings
- c7a8b9d0e1f2: Add episode_suggestions table
- 53827f147dc2: Add AceStream support and HTTP extractor_name
- d8e9f0a1b2c3: Add Telegram backup fields
- 1baf610e47f4: Add Telegram fields to user
- 95f08ba65a00: Add contribute_anonymously to user

Revision ID: f1e2d3c4b5a6
Revises: 050497c15ecc
Create Date: 2026-02-14 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f1e2d3c4b5a6"
down_revision: Union[str, None] = "050497c15ecc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ==========================================================
    # 1. Create new enum types
    # ==========================================================

    # watchaction enum with final uppercase values
    op.execute("CREATE TYPE watchaction AS ENUM ('WATCHED', 'DOWNLOADED', 'QUEUED')")

    # historysource enum
    op.execute("CREATE TYPE historysource AS ENUM ('MEDIAFUSION', 'TRAKT', 'SIMKL', 'MANUAL')")

    # integrationtype enum
    integrationtype = postgresql.ENUM(
        "TRAKT",
        "SIMKL",
        "MAL",
        "LETTERBOXD",
        "ANILIST",
        "TVTIME",
        name="integrationtype",
        create_type=False,
    )
    integrationtype.create(op.get_bind(), checkfirst=True)

    # Add ACESTREAM to streamtype enum
    op.execute("ALTER TYPE streamtype ADD VALUE IF NOT EXISTS 'ACESTREAM'")

    # ==========================================================
    # 2. Modify watch_history table
    # ==========================================================

    # Add action column with watchaction enum type (directly with final type)
    op.execute("ALTER TABLE watch_history ADD COLUMN action watchaction NOT NULL DEFAULT 'WATCHED'::watchaction")

    # Add stream_info column
    op.add_column(
        "watch_history",
        sa.Column("stream_info", sa.JSON(), nullable=False, server_default="{}"),
    )

    # Add source column with historysource enum type
    op.execute(
        "ALTER TABLE watch_history ADD COLUMN source historysource NOT NULL DEFAULT 'MEDIAFUSION'::historysource"
    )

    # Create indexes on new watch_history columns
    op.create_index(op.f("ix_watch_history_action"), "watch_history", ["action"], unique=False)
    op.create_index(op.f("ix_watch_history_source"), "watch_history", ["source"], unique=False)

    # ==========================================================
    # 3. Migrate download_history data and drop the table
    # ==========================================================

    # Move download_history entries into watch_history with action='DOWNLOADED'
    op.execute(
        """
        INSERT INTO watch_history (
            user_id, profile_id, media_id, title, media_type,
            season, episode, progress, duration, action, stream_info, watched_at
        )
        SELECT
            user_id, profile_id, media_id, title, media_type,
            season, episode, 0, NULL, 'DOWNLOADED'::watchaction,
            COALESCE(stream_info, '{}'), downloaded_at
        FROM download_history
        ON CONFLICT DO NOTHING
    """
    )

    # Drop download_history table (no longer needed)
    op.drop_table("download_history")

    # ==========================================================
    # 4. Create profile_integration table
    # ==========================================================

    op.create_table(
        "profile_integration",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column(
            "platform",
            postgresql.ENUM(
                "TRAKT",
                "SIMKL",
                "MAL",
                "LETTERBOXD",
                "ANILIST",
                "TVTIME",
                name="integrationtype",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("encrypted_credentials", sa.String(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("sync_direction", sa.String(), nullable=False, server_default="two_way"),
        sa.Column("scrobble_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("settings", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_status", sa.String(), nullable=True),
        sa.Column("last_sync_error", sa.String(), nullable=True),
        sa.Column("sync_cursor", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("last_sync_stats", sa.JSON(), nullable=False, server_default="{}"),
        sa.ForeignKeyConstraint(["profile_id"], ["user_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_profile_integration_is_enabled"),
        "profile_integration",
        ["is_enabled"],
        unique=False,
    )
    op.create_index(
        op.f("ix_profile_integration_platform"),
        "profile_integration",
        ["platform"],
        unique=False,
    )
    op.create_index(
        op.f("ix_profile_integration_profile_id"),
        "profile_integration",
        ["profile_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_profile_integration_updated_at"),
        "profile_integration",
        ["updated_at"],
        unique=False,
    )
    op.create_index(
        "idx_integration_profile_platform",
        "profile_integration",
        ["profile_id", "platform"],
        unique=True,
    )

    # ==========================================================
    # 5. Add broken_report_threshold to contribution_settings
    # ==========================================================

    op.add_column(
        "contribution_settings",
        sa.Column(
            "broken_report_threshold",
            sa.Integer(),
            nullable=False,
            server_default="3",
        ),
    )

    # ==========================================================
    # 6. Create episode_suggestions table
    # ==========================================================

    op.create_table(
        "episode_suggestions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("episode_id", sa.Integer(), nullable=False),
        sa.Column("field_name", sa.String(), nullable=False),
        sa.Column("current_value", sa.String(), nullable=True),
        sa.Column("suggested_value", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("reviewed_by", sa.String(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_notes", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["episode_id"], ["episode.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_episode_suggestions_episode_id"),
        "episode_suggestions",
        ["episode_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_episode_suggestions_user_id"),
        "episode_suggestions",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_episode_suggestions_status"),
        "episode_suggestions",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_episode_suggestions_updated_at"),
        "episode_suggestions",
        ["updated_at"],
        unique=False,
    )

    # ==========================================================
    # 7. Add AceStream support
    # ==========================================================

    # Add extractor_name column to http_stream table
    op.add_column(
        "http_stream",
        sa.Column("extractor_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )

    # Create acestream_stream table
    op.create_table(
        "acestream_stream",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stream_id", sa.Integer(), nullable=False),
        sa.Column("content_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("info_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.ForeignKeyConstraint(["stream_id"], ["stream.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stream_id"),
    )
    op.create_index(
        op.f("ix_acestream_stream_stream_id"),
        "acestream_stream",
        ["stream_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_acestream_stream_content_id"),
        "acestream_stream",
        ["content_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_acestream_stream_info_hash"),
        "acestream_stream",
        ["info_hash"],
        unique=False,
    )

    # ==========================================================
    # 8. Add Telegram backup fields to telegram_stream
    # ==========================================================

    op.add_column(
        "telegram_stream",
        sa.Column("file_unique_id", sa.String(), nullable=True),
    )
    op.add_column(
        "telegram_stream",
        sa.Column("backup_chat_id", sa.String(), nullable=True),
    )
    op.add_column(
        "telegram_stream",
        sa.Column("backup_message_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "idx_telegram_file_unique_id",
        "telegram_stream",
        ["file_unique_id"],
        unique=False,
    )

    # ==========================================================
    # 9. Create telegram_user_forward table
    # ==========================================================

    op.create_table(
        "telegram_user_forward",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_stream_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("telegram_user_id", sa.Integer(), nullable=False),
        sa.Column(
            "forwarded_chat_id",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=False,
        ),
        sa.Column("forwarded_message_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["telegram_stream_id"], ["telegram_stream.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "telegram_stream_id",
            "user_id",
            name="uq_tg_forward_stream_user",
        ),
    )
    op.create_index(
        "idx_tg_forward_user_stream",
        "telegram_user_forward",
        ["user_id", "telegram_stream_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_telegram_user_forward_telegram_stream_id"),
        "telegram_user_forward",
        ["telegram_stream_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_telegram_user_forward_user_id"),
        "telegram_user_forward",
        ["user_id"],
        unique=False,
    )

    # ==========================================================
    # 10. Add fields to users table
    # ==========================================================

    # Telegram fields
    op.add_column(
        "users",
        sa.Column(
            "telegram_user_id",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column("telegram_linked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_user_telegram_user_id", "users", ["telegram_user_id"], unique=False)
    op.create_index(
        op.f("ix_users_telegram_user_id"),
        "users",
        ["telegram_user_id"],
        unique=True,
    )

    # contribute_anonymously: add as nullable, backfill, then set NOT NULL
    op.add_column("users", sa.Column("contribute_anonymously", sa.Boolean(), nullable=True))
    op.execute("UPDATE users SET contribute_anonymously = false WHERE contribute_anonymously IS NULL")
    op.alter_column("users", "contribute_anonymously", nullable=False)


def downgrade() -> None:
    # ==========================================================
    # Reverse all changes in reverse order
    # ==========================================================

    # 10. Remove user fields
    op.drop_column("users", "contribute_anonymously")
    op.drop_index(op.f("ix_users_telegram_user_id"), table_name="users")
    op.drop_index("idx_user_telegram_user_id", table_name="users")
    op.drop_column("users", "telegram_linked_at")
    op.drop_column("users", "telegram_user_id")

    # 9. Drop telegram_user_forward table
    op.drop_index(
        op.f("ix_telegram_user_forward_user_id"),
        table_name="telegram_user_forward",
    )
    op.drop_index(
        op.f("ix_telegram_user_forward_telegram_stream_id"),
        table_name="telegram_user_forward",
    )
    op.drop_index("idx_tg_forward_user_stream", table_name="telegram_user_forward")
    op.drop_table("telegram_user_forward")

    # 8. Remove Telegram backup fields
    op.drop_index("idx_telegram_file_unique_id", table_name="telegram_stream")
    op.drop_column("telegram_stream", "backup_message_id")
    op.drop_column("telegram_stream", "backup_chat_id")
    op.drop_column("telegram_stream", "file_unique_id")

    # 7. Remove AceStream support
    op.drop_index(op.f("ix_acestream_stream_info_hash"), table_name="acestream_stream")
    op.drop_index(op.f("ix_acestream_stream_content_id"), table_name="acestream_stream")
    op.drop_index(op.f("ix_acestream_stream_stream_id"), table_name="acestream_stream")
    op.drop_table("acestream_stream")
    op.drop_column("http_stream", "extractor_name")
    # Note: Cannot remove ACESTREAM from streamtype enum in PostgreSQL

    # 6. Drop episode_suggestions table
    op.drop_index(
        op.f("ix_episode_suggestions_updated_at"),
        table_name="episode_suggestions",
    )
    op.drop_index(op.f("ix_episode_suggestions_status"), table_name="episode_suggestions")
    op.drop_index(
        op.f("ix_episode_suggestions_user_id"),
        table_name="episode_suggestions",
    )
    op.drop_index(
        op.f("ix_episode_suggestions_episode_id"),
        table_name="episode_suggestions",
    )
    op.drop_table("episode_suggestions")

    # 5. Remove broken_report_threshold
    op.drop_column("contribution_settings", "broken_report_threshold")

    # 4. Drop profile_integration table
    op.drop_index("idx_integration_profile_platform", table_name="profile_integration")
    op.drop_index(
        op.f("ix_profile_integration_updated_at"),
        table_name="profile_integration",
    )
    op.drop_index(
        op.f("ix_profile_integration_profile_id"),
        table_name="profile_integration",
    )
    op.drop_index(
        op.f("ix_profile_integration_platform"),
        table_name="profile_integration",
    )
    op.drop_index(
        op.f("ix_profile_integration_is_enabled"),
        table_name="profile_integration",
    )
    op.drop_table("profile_integration")

    # 3. Recreate download_history and migrate data back
    op.create_table(
        "download_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.Column("stream_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(), nullable=False, server_default=""),
        sa.Column("media_type", sa.String(), nullable=False, server_default="movie"),
        sa.Column("season", sa.Integer(), nullable=True),
        sa.Column("episode", sa.Integer(), nullable=True),
        sa.Column("stream_info", sa.JSON(), nullable=True, server_default="{}"),
        sa.Column("status", sa.String(), nullable=False, server_default="completed"),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["user_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"]),
        sa.ForeignKeyConstraint(["stream_id"], ["stream.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_download_user", "download_history", ["user_id"], unique=False)
    op.create_index("idx_download_profile", "download_history", ["profile_id"], unique=False)
    op.create_index("idx_download_at", "download_history", ["downloaded_at"], unique=False)

    # Migrate downloaded entries back
    op.execute(
        """
        INSERT INTO download_history (
            user_id, profile_id, media_id, title, media_type,
            season, episode, stream_info, status, downloaded_at
        )
        SELECT
            user_id, profile_id, media_id, title, media_type,
            season, episode, stream_info, 'completed', watched_at
        FROM watch_history
        WHERE action = 'DOWNLOADED'::watchaction
    """
    )

    # Remove downloaded entries from watch_history
    op.execute("DELETE FROM watch_history WHERE action = 'DOWNLOADED'::watchaction")

    # 2. Remove watch_history new columns
    op.drop_index(op.f("ix_watch_history_source"), table_name="watch_history")
    op.drop_index(op.f("ix_watch_history_action"), table_name="watch_history")
    op.drop_column("watch_history", "source")
    op.drop_column("watch_history", "stream_info")
    op.drop_column("watch_history", "action")

    # 1. Drop enum types
    op.execute("DROP TYPE IF EXISTS integrationtype")
    op.execute("DROP TYPE IF EXISTS historysource")
    op.execute("DROP TYPE IF EXISTS watchaction")
