pub mod decrypt;
pub mod mediaflow;
pub mod profile;

pub use decrypt::decrypt_user_data;

/// Decode a plain base64url-encoded UserData JSON value (no encryption, no compression).
///
/// This is the format used by the `encoded_user_data` HTTP header:
///   `base64url(json_bytes_of_UserData_object)`
pub fn decode_encoded_user_data(header_val: &str) -> Option<serde_json::Value> {
    use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
    // Add padding if needed before stripping — we strip after adding to be safe.
    let padded = match header_val.len() % 4 {
        2 => format!("{}==", header_val),
        3 => format!("{}=", header_val),
        _ => header_val.to_string(),
    };
    let bytes = URL_SAFE_NO_PAD.decode(padded.trim_end_matches('=')).ok()?;
    serde_json::from_slice(&bytes).ok()
}

/// Encrypt a JSON string into a MediaFusion D-prefixed secret.
///
/// Mirrors Python `CryptoUtils._compress_and_encrypt`:
///   json_str → zlib.compress → random IV(16) → AES-256-CBC + PKCS7 pad → base64url → "D-"
pub fn encrypt_user_data(
    json_str: &str,
    key: &[u8; 32],
) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
    use aes::Aes256;
    use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
    use cbc::cipher::{block_padding::Pkcs7, BlockModeEncrypt, KeyIvInit};
    use flate2::write::ZlibEncoder;
    use flate2::Compression;

    use std::io::Write;

    type Aes256CbcEnc = cbc::Encryptor<Aes256>;

    let mut compressed = Vec::new();
    {
        let mut enc = ZlibEncoder::new(&mut compressed, Compression::default());
        enc.write_all(json_str.as_bytes())?;
        enc.finish()?;
    }

    let mut iv = [0u8; 16];
    {
        use rand_core::Rng;
        rand::rng().fill_bytes(&mut iv);
    }

    // Allocate buffer with space for PKCS7 padding (always adds 1–16 bytes)
    let padded_len = (compressed.len() / 16 + 1) * 16;
    let mut buf = vec![0u8; padded_len];
    buf[..compressed.len()].copy_from_slice(&compressed);

    let ciphertext = Aes256CbcEnc::new(key.into(), &iv.into())
        .encrypt_padded::<Pkcs7>(&mut buf, compressed.len())
        .map_err(|e| format!("AES encrypt: {e}"))?
        .to_vec();

    let mut combined = Vec::with_capacity(16 + ciphertext.len());
    combined.extend_from_slice(&iv);
    combined.extend_from_slice(&ciphertext);

    Ok(format!("D-{}", URL_SAFE_NO_PAD.encode(&combined)))
}

use serde_json::Value;
use sqlx::PgPool;

/// Resolve any secret_str (D-prefix, U-prefix, or empty) into a UserData JSON value.
/// D-prefix: AES-256-CBC decrypt inline.
/// U-prefix: Redis cache first, then Postgres DB lookup.
/// Empty/unknown: returns empty object → UserData::default().
pub async fn resolve_user_data(
    secret_str: &str,
    key: &[u8; 32],
    pool: &PgPool,
    redis: &fred::clients::Client,
) -> Value {
    if secret_str.is_empty() {
        return Value::Object(Default::default());
    }
    if let Some(uuid) = secret_str.strip_prefix("U-") {
        return profile::lookup(redis, pool, key, uuid)
            .await
            .unwrap_or_else(|| Value::Object(Default::default()));
    }
    decrypt_user_data(secret_str, key).unwrap_or_else(|e| {
        tracing::debug!("resolve_user_data: {e}");
        Value::Object(Default::default())
    })
}
