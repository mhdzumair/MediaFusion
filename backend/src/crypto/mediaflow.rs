use aes::Aes256;
use base64::{Engine, engine::general_purpose::URL_SAFE};
use cbc::cipher::{BlockModeEncrypt, KeyIvInit, block_padding::Pkcs7};
use rand::Rng;
use serde_json::Value;

type Aes256CbcEnc = cbc::Encryptor<Aes256>;

/// Encrypt query parameters into a MediaFlow token.
///
/// Mirrors Python `crypto.encrypt_data(secret_key, data, expiration, ip)`.
pub fn encrypt_mediaflow_token(
    api_password: &str,
    mut params: serde_json::Map<String, Value>,
    expiration: Option<i64>,
    ip: Option<&str>,
) -> Result<String, String> {
    if let Some(exp) = expiration {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_err(|e| e.to_string())?
            .as_secs() as i64;
        params.insert("exp".into(), Value::from(now + exp));
    }
    if let Some(ip_addr) = ip.filter(|s| !s.is_empty()) {
        params.insert("ip".into(), Value::String(ip_addr.to_string()));
    }

    let json = serde_json::to_vec(&Value::Object(params)).map_err(|e| e.to_string())?;

    let mut key = [b' '; 32];
    let pw = api_password.as_bytes();
    let len = pw.len().min(32);
    key[..len].copy_from_slice(&pw[..len]);

    let mut iv = [0u8; 16];
    rand::rng().fill_bytes(&mut iv);

    let padded_len = (json.len() / 16 + 1) * 16;
    let mut buf = vec![0u8; padded_len];
    buf[..json.len()].copy_from_slice(&json);

    let ciphertext = Aes256CbcEnc::new(&key.into(), &iv.into())
        .encrypt_padded::<Pkcs7>(&mut buf, json.len())
        .map_err(|e| format!("AES encrypt: {e}"))?;

    let mut combined = Vec::with_capacity(16 + ciphertext.len());
    combined.extend_from_slice(&iv);
    combined.extend_from_slice(ciphertext);

    Ok(URL_SAFE.encode(combined))
}
