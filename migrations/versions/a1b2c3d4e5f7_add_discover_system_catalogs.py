"""Add system catalogs for Discover feature pre-warm

Revision ID: a1b2c3d4e5f7
Revises: fc2804ba1ac9
Create Date: 2026-04-28 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f7"
down_revision: Union[str, None] = "fc2804ba1ac9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    for row in [
        ("discover_pinned_movies", "Discover: Trending Movies", "movie", True, 9900),
        ("discover_pinned_series", "Discover: Trending Series", "series", True, 9901),
    ]:
        name, display_name, media_type, is_system, display_order = row
        existing = conn.execute(
            sa.text("SELECT id FROM catalog WHERE name = :name"),
            {"name": name},
        ).first()
        if not existing:
            conn.execute(
                sa.text(
                    "INSERT INTO catalog (name, display_name, media_type, is_system, display_order) "
                    "VALUES (:name, :display_name, :media_type, :is_system, :display_order)"
                ),
                {
                    "name": name,
                    "display_name": display_name,
                    "media_type": media_type,
                    "is_system": is_system,
                    "display_order": display_order,
                },
            )


def downgrade() -> None:
    conn = op.get_bind()
    for name in ("discover_pinned_movies", "discover_pinned_series"):
        conn.execute(sa.text("DELETE FROM catalog WHERE name = :name"), {"name": name})
