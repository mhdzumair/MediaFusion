"""Minimal LZ-String URI-component decompressor.

This module implements compatibility with JavaScript `lz-string`
`decompressFromEncodedURIComponent`, used by DMM hashlist payloads.
"""

from collections.abc import Callable

_URI_SAFE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+-$"
_BASE_REVERSE_DIC: dict[str, dict[str, int]] = {}


def _get_base_value(alphabet: str, character: str) -> int:
    if alphabet not in _BASE_REVERSE_DIC:
        _BASE_REVERSE_DIC[alphabet] = {ch: idx for idx, ch in enumerate(alphabet)}
    return _BASE_REVERSE_DIC[alphabet][character]


def _decompress(length: int, reset_value: int, get_next_value: Callable[[int], int]) -> str:
    dictionary: dict[int, str] = {
        0: "",
        1: "",
        2: "",
    }
    enlarge_in = 4
    dict_size = 4
    num_bits = 3
    data_val = get_next_value(0)
    data_position = reset_value
    data_index = 1

    def read_bits(bit_count: int) -> int:
        nonlocal data_val, data_position, data_index
        bits = 0
        power = 1
        max_power = 1 << bit_count

        while power != max_power:
            resb = data_val & data_position
            data_position >>= 1
            if data_position == 0:
                data_position = reset_value
                data_val = get_next_value(data_index) if data_index < length else 0
                data_index += 1

            if resb > 0:
                bits |= power
            power <<= 1

        return bits

    next_code = read_bits(2)
    if next_code == 0:
        c = chr(read_bits(8))
    elif next_code == 1:
        c = chr(read_bits(16))
    elif next_code == 2:
        return ""
    else:
        raise ValueError("Invalid initial LZ-string code")

    dictionary[3] = c
    w = c
    result = [c]

    while True:
        if data_index > length:
            return ""

        c_code = read_bits(num_bits)

        if c_code == 0:
            dictionary[dict_size] = chr(read_bits(8))
            dict_size += 1
            c_code = dict_size - 1
            enlarge_in -= 1
        elif c_code == 1:
            dictionary[dict_size] = chr(read_bits(16))
            dict_size += 1
            c_code = dict_size - 1
            enlarge_in -= 1
        elif c_code == 2:
            return "".join(result)

        if enlarge_in == 0:
            enlarge_in = 1 << num_bits
            num_bits += 1

        if c_code in dictionary:
            entry = dictionary[c_code]
        elif c_code == dict_size:
            entry = w + w[0]
        else:
            return ""

        result.append(entry)

        dictionary[dict_size] = w + entry[0]
        dict_size += 1
        enlarge_in -= 1
        w = entry

        if enlarge_in == 0:
            enlarge_in = 1 << num_bits
            num_bits += 1


def decompress_from_encoded_uri_component(compressed: str | None) -> str:
    """Decode string compressed with JS lz-string `compressToEncodedURIComponent`."""
    if compressed is None or compressed == "":
        return ""

    normalized = compressed.replace(" ", "+")

    try:
        return _decompress(
            len(normalized),
            32,
            lambda index: _get_base_value(_URI_SAFE_ALPHABET, normalized[index]),
        )
    except Exception as exc:  # pragma: no cover - defensive wrapper
        raise ValueError("Invalid LZ-string URI component payload") from exc
