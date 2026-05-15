"""add_stream_name_trgm_index

Adds a pg_trgm GIN index on stream.name so that ILIKE '%query%' searches
(e.g. in the Torznab API at db/crud/torznab.py) are index-supported instead
of forcing a sequential scan on the entire stream table.

The pg_trgm extension is already enabled via deployment init scripts.

Production note: on a large stream table, build this index concurrently before
running the migration to avoid a long table lock:

    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_stream_name_trgm
        ON stream USING gin (name gin_trgm_ops);

The migration will then skip creation because of IF NOT EXISTS.

Revision ID: a1b2c3d4e5f6
Revises: d826df80371b
Create Date: 2026-05-03 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "d826df80371b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # GIN trigram index on stream.name enables ILIKE '%x%' queries to use the index
    # instead of seq-scanning the full stream table (used by Torznab search).
    op.create_index(
        "idx_stream_name_trgm",
        "stream",
        ["name"],
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("idx_stream_name_trgm", table_name="stream", if_exists=True)
