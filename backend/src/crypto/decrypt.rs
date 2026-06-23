/*!
Decrypt MediaFusion D- prefixed secret_str.

Python equivalent:
    key = settings.secret_key.encode("utf-8").ljust(32)[:32]
    final_data = base64url_decode(data)
    iv = final_data[:16]
    encrypted = final_data[16:]
    cipher = AES.new(key, AES.MODE_CBC, iv)
    decrypted = unpad(cipher.decrypt(encrypted), 16)
    json_str = zlib.decompress(decrypted).decode("utf-8")
*/

use aes::Aes256;
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use cbc::cipher::{BlockModeDecrypt, KeyIvInit, block_padding::Pkcs7};
use serde_json::Value;

type Aes256CbcDec = cbc::Decryptor<Aes256>;

pub fn decrypt_user_data(
    secret_str: &str,
    key: &[u8; 32],
) -> Result<Value, Box<dyn std::error::Error + Send + Sync>> {
    if secret_str.is_empty() {
        return Ok(Value::Object(serde_json::Map::new()));
    }

    if let Some(data) = secret_str.strip_prefix("D-") {
        let raw = URL_SAFE_NO_PAD
            .decode(data)
            .map_err(|e| format!("base64 decode: {e}"))?;

        if raw.len() < 17 {
            return Err("payload too short".into());
        }

        let iv: [u8; 16] = raw[..16].try_into().unwrap();
        let encrypted = &raw[16..];

        let mut buf = encrypted.to_vec();
        let decrypted = Aes256CbcDec::new(key.into(), &iv.into())
            .decrypt_padded::<Pkcs7>(&mut buf)
            .map_err(|e| format!("AES decrypt: {e}"))?;

        // Python always zlib-decompresses D- payloads after AES decrypt.
        let mut decoder = flate2::read::ZlibDecoder::new(decrypted);
        let mut json_str = String::new();
        std::io::Read::read_to_string(&mut decoder, &mut json_str)
            .map_err(|e| format!("zlib: {e}"))?;

        return serde_json::from_str(&json_str).map_err(|e| format!("json: {e}").into());
    }

    if secret_str.starts_with("U-") {
        // U- prefix requires DB profile lookup — handled by the route layer which has pool access.
        // Returning empty config here is safe for public-scoped requests.
        return Ok(Value::Object(serde_json::Map::new()));
    }

    Err("unknown secret_str prefix".into())
}
