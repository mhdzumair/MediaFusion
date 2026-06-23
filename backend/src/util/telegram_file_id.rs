//! Decode Telegram Bot API `file_id` values to extract the embedded `document_id`.

const TYPE_ID_FILE_REFERENCE_FLAG: i32 = 1 << 25;

fn decode_telegram_base64(value: &str) -> Option<Vec<u8>> {
    use base64::{Engine as _, engine::general_purpose::STANDARD};

    let normalized = value.replace('-', "+").replace('_', "/");
    let padding = (4 - normalized.len() % 4) % 4;
    let padded = if padding == 0 {
        normalized
    } else {
        format!("{normalized}{}", "=".repeat(padding))
    };
    STANDARD.decode(padded).ok()
}

fn rle_decode(data: &[u8]) -> Vec<u8> {
    let mut result = Vec::with_capacity(data.len());
    let mut idx = 0;
    while idx < data.len() {
        if data[idx] == 0 && idx + 1 < data.len() {
            // Python parity: bytes(count) allocates `count` zero bytes.
            let count = data[idx + 1] as usize;
            result.extend(std::iter::repeat_n(0u8, count));
            idx += 2;
        } else {
            result.push(data[idx]);
            idx += 1;
        }
    }
    result
}

/// Extract Telegram `document_id` from a Bot API `file_id`.
///
/// Returns `None` when the input is empty or cannot be decoded.
pub fn extract_document_id_from_file_id(file_id: Option<&str>) -> Option<i64> {
    let file_id = file_id.filter(|s| !s.is_empty())?;

    let decoded = decode_telegram_base64(file_id)?;
    let data = rle_decode(&decoded);
    if data.len() < 20 {
        return None;
    }

    let mut offset = 0;

    let type_id_raw = i32::from_le_bytes(data[offset..offset + 4].try_into().ok()?);
    offset += 4;
    let has_reference = type_id_raw & TYPE_ID_FILE_REFERENCE_FLAG != 0;

    // Skip dc_id
    offset += 4;

    if has_reference {
        let ref_len_byte = *data.get(offset)?;
        offset += 1;

        let ref_len = if ref_len_byte == 254 {
            if data.len() < offset + 3 {
                return None;
            }
            let mut len_bytes = [0u8; 4];
            len_bytes[..3].copy_from_slice(&data[offset..offset + 3]);
            offset += 3;
            u32::from_le_bytes(len_bytes) as usize
        } else {
            ref_len_byte as usize
        };

        if data.len() < offset + ref_len {
            return None;
        }
        offset += ref_len;

        let marker_len = if ref_len_byte == 254 { 4 } else { 1 };
        let total_len = marker_len + ref_len;
        let padding = total_len % 4;
        if padding != 0 {
            offset += 4 - padding;
        }
    }

    let remaining = &data[offset..];
    if remaining.len() < 16 {
        return None;
    }

    Some(i64::from_le_bytes(remaining[..8].try_into().ok()?))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_input_returns_none() {
        assert_eq!(extract_document_id_from_file_id(None), None);
        assert_eq!(extract_document_id_from_file_id(Some("")), None);
    }

    #[test]
    fn invalid_base64_returns_none() {
        assert_eq!(
            extract_document_id_from_file_id(Some("not!!!valid!!!base64")),
            None
        );
    }

    #[test]
    fn decodes_file_id_without_file_reference() {
        // RLE+base64url fixture verified against Python extract_document_id_from_file_id.
        assert_eq!(
            extract_document_id_from_file_id(Some("BAADAgADFc1bBwAM")),
            Some(123456789)
        );
    }

    #[test]
    fn rejects_truncated_remaining_bytes() {
        // Fewer than 16 trailing bytes must return None (document_id + access_hash).
        assert_eq!(extract_document_id_from_file_id(Some("BAADAgAD")), None);
    }
}
