"""set user FK ondelete behaviors for account deletion

Revision ID: d7c1a8f4b2e9
Revises: c91d4ab85b2f
Create Date: 2026-02-22 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d7c1a8f4b2e9"
down_revision: Union[str, None] = "c91d4ab85b2f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _recreate_fk(
    *,
    table_name: str,
    constraint_name: str,
    local_cols: list[str],
    remote_table: str = "users",
    remote_cols: list[str] | None = None,
    ondelete: str | None = None,
) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return

    target_local_cols = tuple(local_cols)
    target_remote_cols = tuple(remote_cols or ["id"])

    # Drop by explicit name (if present), then also drop any matching FK on same columns
    # to handle environments where constraint names differ.
    fk_names_to_drop: set[str] = set()
    for fk in inspector.get_foreign_keys(table_name):
        fk_name = fk.get("name")
        if not fk_name:
            continue

        constrained_cols = tuple(fk.get("constrained_columns") or [])
        referred_table = fk.get("referred_table")
        referred_cols = tuple(fk.get("referred_columns") or [])

        if fk_name == constraint_name:
            fk_names_to_drop.add(fk_name)
            continue

        if (
            constrained_cols == target_local_cols
            and referred_table == remote_table
            and referred_cols == target_remote_cols
        ):
            fk_names_to_drop.add(fk_name)

    for fk_name in fk_names_to_drop:
        op.drop_constraint(fk_name, table_name, type_="foreignkey")

    op.create_foreign_key(
        constraint_name,
        table_name,
        remote_table,
        local_cols,
        remote_cols or ["id"],
        ondelete=ondelete,
    )


def upgrade() -> None:
    # Media user references -> SET NULL
    _recreate_fk(
        table_name="media",
        constraint_name="media_created_by_user_id_fkey",
        local_cols=["created_by_user_id"],
        ondelete="SET NULL",
    )
    _recreate_fk(
        table_name="media",
        constraint_name="media_last_refreshed_by_user_id_fkey",
        local_cols=["last_refreshed_by_user_id"],
        ondelete="SET NULL",
    )
    _recreate_fk(
        table_name="media",
        constraint_name="media_migrated_by_user_id_fkey",
        local_cols=["migrated_by_user_id"],
        ondelete="SET NULL",
    )
    _recreate_fk(
        table_name="media",
        constraint_name="media_blocked_by_user_id_fkey",
        local_cols=["blocked_by_user_id"],
        ondelete="SET NULL",
    )
    _recreate_fk(
        table_name="media",
        constraint_name="media_last_scraped_by_user_id_fkey",
        local_cols=["last_scraped_by_user_id"],
        ondelete="SET NULL",
    )

    # Other nullable references -> SET NULL
    _recreate_fk(
        table_name="episode",
        constraint_name="episode_created_by_user_id_fkey",
        local_cols=["created_by_user_id"],
        ondelete="SET NULL",
    )
    _recreate_fk(
        table_name="stream",
        constraint_name="stream_uploader_user_id_fkey",
        local_cols=["uploader_user_id"],
        ondelete="SET NULL",
    )
    _recreate_fk(
        table_name="stream_media_link",
        constraint_name="stream_media_link_linked_by_user_id_fkey",
        local_cols=["linked_by_user_id"],
        ondelete="SET NULL",
    )
    _recreate_fk(
        table_name="media_review",
        constraint_name="media_review_user_id_fkey",
        local_cols=["user_id"],
        ondelete="SET NULL",
    )

    # Non-nullable forward cache entries should be removed with user deletion
    _recreate_fk(
        table_name="telegram_user_forward",
        constraint_name="telegram_user_forward_user_id_fkey",
        local_cols=["user_id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    # Revert to previous NO ACTION behavior
    _recreate_fk(
        table_name="media",
        constraint_name="media_created_by_user_id_fkey",
        local_cols=["created_by_user_id"],
        ondelete=None,
    )
    _recreate_fk(
        table_name="media",
        constraint_name="media_last_refreshed_by_user_id_fkey",
        local_cols=["last_refreshed_by_user_id"],
        ondelete=None,
    )
    _recreate_fk(
        table_name="media",
        constraint_name="media_migrated_by_user_id_fkey",
        local_cols=["migrated_by_user_id"],
        ondelete=None,
    )
    _recreate_fk(
        table_name="media",
        constraint_name="media_blocked_by_user_id_fkey",
        local_cols=["blocked_by_user_id"],
        ondelete=None,
    )
    _recreate_fk(
        table_name="media",
        constraint_name="media_last_scraped_by_user_id_fkey",
        local_cols=["last_scraped_by_user_id"],
        ondelete=None,
    )
    _recreate_fk(
        table_name="episode",
        constraint_name="episode_created_by_user_id_fkey",
        local_cols=["created_by_user_id"],
        ondelete=None,
    )
    _recreate_fk(
        table_name="stream",
        constraint_name="stream_uploader_user_id_fkey",
        local_cols=["uploader_user_id"],
        ondelete=None,
    )
    _recreate_fk(
        table_name="stream_media_link",
        constraint_name="stream_media_link_linked_by_user_id_fkey",
        local_cols=["linked_by_user_id"],
        ondelete=None,
    )
    _recreate_fk(
        table_name="media_review",
        constraint_name="media_review_user_id_fkey",
        local_cols=["user_id"],
        ondelete=None,
    )
    _recreate_fk(
        table_name="telegram_user_forward",
        constraint_name="telegram_user_forward_user_id_fkey",
        local_cols=["user_id"],
        ondelete=None,
    )
