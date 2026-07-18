//! Encrypt/decrypt per-user Telegram MTProto session blobs at rest.

use crate::crypto::profile::{decrypt_secrets, encrypt_secrets};

const SESSION_KEY: &str = "tg_session";

pub fn encrypt_session(session: &str, key: &[u8; 32]) -> Option<String> {
    encrypt_secrets(&serde_json::json!({ SESSION_KEY: session }), key)
}

pub fn decrypt_session(encrypted: &str, key: &[u8; 32]) -> Option<String> {
    let secrets = decrypt_secrets(encrypted, key);
    secrets
        .get(SESSION_KEY)
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .map(str::to_string)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip() {
        let key = [7u8; 32];
        let session = "1ABCdef";
        let enc = encrypt_session(session, &key).expect("encrypt");
        let dec = decrypt_session(&enc, &key).expect("decrypt");
        assert_eq!(dec, session);
    }
}
