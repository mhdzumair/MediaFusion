"""add_discover_system_catalogs

Revision ID: d826df80371b
Revises: 12bf74e53c8d
Create Date: 2026-04-29 09:20:23.148329

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d826df80371b"
down_revision: Union[str, None] = "12bf74e53c8d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    for name, display_name, display_order in [
        ("discover_pinned_movies", "Discover: Trending Movies", 9900),
        ("discover_pinned_series", "Discover: Trending Series", 9901),
    ]:
        existing = conn.execute(
            sa.text("SELECT id FROM catalog WHERE name = :name"),
            {"name": name},
        ).first()
        if not existing:
            conn.execute(
                sa.text(
                    "INSERT INTO catalog (name, display_name, is_system, display_order) "
                    "VALUES (:name, :display_name, :is_system, :display_order)"
                ),
                {
                    "name": name,
                    "display_name": display_name,
                    "is_system": True,
                    "display_order": display_order,
                },
            )


def downgrade() -> None:
    conn = op.get_bind()
    for name in ("discover_pinned_movies", "discover_pinned_series"):
        conn.execute(sa.text("DELETE FROM catalog WHERE name = :name"), {"name": name})
