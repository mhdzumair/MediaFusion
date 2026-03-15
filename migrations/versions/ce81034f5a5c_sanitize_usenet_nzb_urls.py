"""sanitize usenet nzb urls

Revision ID: ce81034f5a5c
Revises: 24c9d2e84ae0
Create Date: 2026-03-15 15:53:21.181206

"""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "ce81034f5a5c"
down_revision: Union[str, None] = "24c9d2e84ae0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SENSITIVE_QUERY_KEYS = {
    "apikey",
    "api_key",
    "token",
    "auth",
    "authorization",
    "passkey",
    "password",
    "pwd",
    "username",
    "user",
    "rsskey",
    "key",
    "secret",
}
BATCH_SIZE = 1000


def _sanitize_nzb_url(url: str | None) -> str | None:
    if not url or not isinstance(url, str):
        return None

    raw_url = url.strip()
    if not raw_url:
        return None

    try:
        parts = urlsplit(raw_url)
    except ValueError:
        return raw_url

    netloc = parts.netloc or ""
    sanitized_netloc = netloc.rsplit("@", 1)[1] if "@" in netloc else netloc

    query_items = parse_qsl(parts.query, keep_blank_values=True)
    safe_query_items = [
        (key, value) for key, value in query_items if (key or "").strip().lower() not in SENSITIVE_QUERY_KEYS
    ]
    sanitized_query = urlencode(safe_query_items, doseq=True)

    return urlunsplit((parts.scheme, sanitized_netloc, parts.path, sanitized_query, parts.fragment))


def upgrade() -> None:
    bind = op.get_bind()

    usenet_stream = sa.table(
        "usenet_stream",
        sa.column("id", sa.Integer),
        sa.column("nzb_url", sa.Text),
    )
    lowered_nzb_url = sa.func.lower(usenet_stream.c.nzb_url)
    potential_credential_filter = sa.or_(
        usenet_stream.c.nzb_url.contains("@"),
        *[lowered_nzb_url.contains(f"{key}=") for key in SENSITIVE_QUERY_KEYS],
    )

    select_stmt = (
        sa.select(usenet_stream.c.id, usenet_stream.c.nzb_url)
        .where(usenet_stream.c.id > sa.bindparam("last_id"))
        .where(usenet_stream.c.nzb_url.is_not(None))
        .where(potential_credential_filter)
        .order_by(usenet_stream.c.id)
        .limit(BATCH_SIZE)
    )
    update_stmt = (
        sa.update(usenet_stream)
        .where(usenet_stream.c.id == sa.bindparam("stream_id"))
        .values(nzb_url=sa.bindparam("new_nzb_url"))
    )

    last_id = 0
    while True:
        rows = bind.execute(select_stmt, {"last_id": last_id}).mappings().all()
        if not rows:
            break

        pending_updates: list[dict[str, str | int | None]] = []
        for row in rows:
            row_id = int(row["id"])
            current_url = row["nzb_url"]
            sanitized_url = _sanitize_nzb_url(current_url)

            if sanitized_url != current_url:
                pending_updates.append(
                    {
                        "stream_id": row_id,
                        "new_nzb_url": sanitized_url,
                    }
                )

            last_id = row_id

        if pending_updates:
            bind.execute(update_stmt, pending_updates)


def downgrade() -> None:
    # Irreversible data migration: stripped credentials cannot be recovered.
    return None
