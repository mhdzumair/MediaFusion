"""Drop nzb_content column from usenet_stream table

NZB files are now stored externally (local disk or S3/R2) and
referenced via nzb_url. The nzb_content LargeBinary column is
no longer needed and can cause significant DB bloat (10-100MB per NZB).

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-02-18 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, None] = "a2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("usenet_stream", "nzb_content")


def downgrade() -> None:
    op.add_column(
        "usenet_stream",
        sa.Column("nzb_content", sa.LargeBinary(), nullable=True),
    )
