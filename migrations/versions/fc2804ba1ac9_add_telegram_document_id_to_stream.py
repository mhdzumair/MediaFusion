"""add telegram document id to stream

Revision ID: fc2804ba1ac9
Revises: a4b5c6d7e8f9
Create Date: 2026-03-06 01:19:55.020355

"""

import base64
import struct
from io import BytesIO
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "fc2804ba1ac9"
down_revision: Union[str, None] = "a4b5c6d7e8f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TYPE_ID_FILE_REFERENCE_FLAG = 1 << 25


def _decode_telegram_base64(value: str) -> bytes:
    normalized = value.replace("-", "+").replace("_", "/")
    padding = 4 - (len(normalized) % 4)
    if padding != 4:
        normalized += "=" * padding
    return base64.b64decode(normalized)


def _rle_decode(data: bytes) -> bytes:
    result = bytearray()
    idx = 0
    while idx < len(data):
        if data[idx] == 0 and idx + 1 < len(data):
            result.extend(bytes(data[idx + 1]))
            idx += 2
        else:
            result.append(data[idx])
            idx += 1
    return bytes(result)


def _extract_document_id_from_file_id(file_id: str | None) -> int | None:
    if not file_id:
        return None
    try:
        decoded = _decode_telegram_base64(file_id)
        data = _rle_decode(decoded)
        if len(data) < 20:
            return None

        buf = BytesIO(data)
        type_id_raw = struct.unpack("<i", buf.read(4))[0]
        has_reference = bool(type_id_raw & TYPE_ID_FILE_REFERENCE_FLAG)
        buf.read(4)  # skip dc_id

        if has_reference:
            ref_len_byte_raw = buf.read(1)
            if not ref_len_byte_raw:
                return None
            ref_len_byte = ref_len_byte_raw[0]
            if ref_len_byte == 254:
                ref_len = struct.unpack("<I", buf.read(3) + b"\x00")[0]
            else:
                ref_len = ref_len_byte
            buf.read(ref_len)
            total_len = 1 + (3 if ref_len_byte == 254 else 0) + ref_len
            padding = total_len % 4
            if padding:
                buf.read(4 - padding)

        remaining = buf.read()
        if len(remaining) < 16:
            return None

        return int(struct.unpack("<q", remaining[0:8])[0])
    except Exception:
        return None


def upgrade() -> None:
    op.add_column(
        "telegram_stream",
        sa.Column("document_id", sa.BigInteger(), nullable=True),
    )
    op.create_index("idx_telegram_document_id", "telegram_stream", ["document_id"], unique=False)

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, file_id FROM telegram_stream WHERE file_id IS NOT NULL")).fetchall()
    for row in rows:
        stream_id = row[0]
        file_id = row[1]
        document_id = _extract_document_id_from_file_id(file_id)
        if document_id is None:
            continue
        bind.execute(
            sa.text("UPDATE telegram_stream SET document_id = :document_id WHERE id = :stream_id"),
            {"document_id": document_id, "stream_id": stream_id},
        )


def downgrade() -> None:
    op.drop_index("idx_telegram_document_id", table_name="telegram_stream")
    op.drop_column("telegram_stream", "document_id")
