"""Utilities for decoding Telegram Bot API file_id values."""

import base64
import struct
from io import BytesIO

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


def extract_document_id_from_file_id(file_id: str | None) -> int | None:
    """Extract Telegram document_id from a Bot API file_id.

    Returns None when the input is empty or cannot be decoded.
    """
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

        # Skip dc_id
        buf.read(4)

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

        document_id = struct.unpack("<q", remaining[0:8])[0]
        return int(document_id)
    except Exception:
        return None
