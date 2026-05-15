"""backfill empty usernames from email prefix

Revision ID: b0ddd67b439e
Revises: 66a69f4deb31
Create Date: 2026-02-20 20:08:14.401148

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b0ddd67b439e"
down_revision: Union[str, None] = "66a69f4deb31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _generate_unique_username(base: str, user_id: int, used_usernames: set[str]) -> str:
    """Generate a unique username within the 100-char limit."""
    normalized_base = (base or "").strip()
    if len(normalized_base) < 3:
        normalized_base = f"user_{user_id}"

    candidate = normalized_base[:100]
    if len(candidate) < 3:
        candidate = f"user_{user_id}"

    if candidate not in used_usernames:
        return candidate

    suffix = f"_{user_id}"
    max_base_len = 100 - len(suffix)
    trimmed_base = normalized_base[: max(0, max_base_len)]
    candidate = f"{trimmed_base}{suffix}" if trimmed_base else f"user{suffix}"[:100]
    if len(candidate) < 3:
        candidate = f"user_{user_id}"

    counter = 1
    while candidate in used_usernames:
        counter_suffix = f"_{user_id}_{counter}"
        max_base_len = 100 - len(counter_suffix)
        trimmed_base = normalized_base[: max(0, max_base_len)]
        candidate = f"{trimmed_base}{counter_suffix}" if trimmed_base else f"user{counter_suffix}"[:100]
        counter += 1

    return candidate


def upgrade() -> None:
    bind = op.get_bind()

    users_to_backfill = bind.execute(
        sa.text(
            """
            SELECT id, email
            FROM users
            WHERE username IS NULL OR btrim(username) = ''
            ORDER BY id
            """
        )
    ).fetchall()

    if not users_to_backfill:
        return

    existing_usernames = bind.execute(
        sa.text(
            """
            SELECT username
            FROM users
            WHERE username IS NOT NULL AND btrim(username) <> ''
            """
        )
    ).fetchall()
    used_usernames: set[str] = {row[0] for row in existing_usernames}

    for row in users_to_backfill:
        user_id = row[0]
        email = row[1] or ""
        email_prefix = email.split("@", 1)[0].strip()
        username = _generate_unique_username(email_prefix, user_id, used_usernames)

        bind.execute(
            sa.text("UPDATE users SET username = :username WHERE id = :user_id"),
            {"username": username, "user_id": user_id},
        )
        used_usernames.add(username)


def downgrade() -> None:
    # Irreversible data migration: cannot reliably determine prior NULL/empty usernames.
    return None
