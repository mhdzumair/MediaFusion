//! Confidential, authenticated references; hashes are never encoded in cleartext.
use hmac::{Hmac, KeyInit, Mac};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

#[derive(Serialize, Deserialize)]
pub struct TorrentRef {
    pub hash: String,
    pub filename: Option<String>,
    pub season: Option<i32>,
    pub episode: Option<i32>,
    pub scope: String,
}

fn key_for(key: &[u8; 32], purpose: &[u8]) -> [u8; 32] {
    let mut mac = Hmac::<Sha256>::new_from_slice(key).expect("HMAC key");
    mac.update(purpose);
    mac.finalize().into_bytes().into()
}

pub fn scope(secret: &str, provider: &str) -> String {
    Sha256::digest(format!("{secret}\0{provider}"))
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

pub fn seal(value: &TorrentRef, key: &[u8; 32]) -> Option<String> {
    let encryption_key = key_for(key, b"mediafusion/torrent-ref/encryption/v1");
    let signing_key = key_for(key, b"mediafusion/torrent-ref/authentication/v1");
    let ciphertext =
        super::profile::encrypt_secrets(&serde_json::to_value(value).ok()?, &encryption_key)?;
    let mut mac = Hmac::<Sha256>::new_from_slice(&signing_key).ok()?;
    mac.update(ciphertext.as_bytes());
    use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
    Some(format!(
        "T-{ciphertext}.{}",
        URL_SAFE_NO_PAD.encode(mac.finalize().into_bytes())
    ))
}

pub fn open(token: &str, key: &[u8; 32]) -> Option<TorrentRef> {
    let (ciphertext, signature) = token.strip_prefix("T-")?.split_once('.')?;
    if ciphertext.len() > 16_384 {
        return None;
    }
    let signing_key = key_for(key, b"mediafusion/torrent-ref/authentication/v1");
    let mut mac = Hmac::<Sha256>::new_from_slice(&signing_key).ok()?;
    mac.update(ciphertext.as_bytes());
    use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
    mac.verify_slice(&URL_SAFE_NO_PAD.decode(signature).ok()?)
        .ok()?;
    let encryption_key = key_for(key, b"mediafusion/torrent-ref/encryption/v1");
    serde_json::from_value(super::profile::try_decrypt_secrets(
        ciphertext,
        &encryption_key,
    )?)
    .ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn deterministic_hash_looking_ciphertext_remains_playable_after_sanitizing() {
        use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
        use cbc::cipher::{BlockModeEncrypt, KeyIvInit, block_padding::Pkcs7};
        let key = [9; 32];
        let value = TorrentRef {
            hash: "1f1914a9d7a2163f08c540bae788806bfad2c732".into(),
            filename: None,
            season: None,
            episode: None,
            scope: scope("U-test", "realdebrid"),
        };
        let bytes = serde_json::to_vec(&value).unwrap();
        let encryption_key = key_for(&key, b"mediafusion/torrent-ref/encryption/v1");
        let signing_key = key_for(&key, b"mediafusion/torrent-ref/authentication/v1");
        // Fixed IVs make this regression deterministic. Production seal still
        // uses random IVs. Select a fixture the hash heuristic actually matches.
        for last in 0..=255u8 {
            let mut iv = [0u8; 16];
            iv[15] = last;
            let mut buf = vec![0; (bytes.len() / 16 + 1) * 16];
            buf[..bytes.len()].copy_from_slice(&bytes);
            let ciphertext =
                cbc::Encryptor::<aes::Aes256>::new((&encryption_key).into(), (&iv).into())
                    .encrypt_padded::<Pkcs7>(&mut buf, bytes.len())
                    .unwrap();
            let encoded = URL_SAFE_NO_PAD.encode([iv.as_slice(), ciphertext].concat());
            let mut mac = Hmac::<Sha256>::new_from_slice(&signing_key).unwrap();
            mac.update(encoded.as_bytes());
            let token = format!(
                "T-{encoded}.{}",
                URL_SAFE_NO_PAD.encode(mac.finalize().into_bytes())
            );
            if !crate::torrent_privacy_middleware::contains_hash(&token) {
                continue;
            }
            let url = format!(
                "https://example.test/streaming_provider/U-test/playback/realdebrid/{token}"
            );
            let nested = format!(
                "https://mf.test/proxy/stream?d={}",
                urlencoding::encode(&url)
            );
            let mut response = serde_json::json!({"url":url,"nested":nested});
            crate::torrent_privacy_middleware::sanitize(&mut response, &key, "api:1");
            assert_eq!(response["url"], url);
            assert_eq!(response["nested"], nested);
            assert_eq!(
                open(
                    response["url"]
                        .as_str()
                        .unwrap()
                        .rsplit('/')
                        .next()
                        .unwrap(),
                    &key
                )
                .unwrap()
                .hash,
                value.hash
            );
            return;
        }
        panic!("deterministic fixtures must exercise the hash heuristic");
    }

    #[test]
    fn references_are_confidential_authenticated_and_domain_separated() {
        let key = [9; 32];
        let hash = "1f1914a9d7a2163f08c540bae788806bfad2c732";
        let value = TorrentRef {
            hash: hash.into(),
            filename: Some(format!("{hash}.mkv")),
            season: Some(1),
            episode: Some(2),
            scope: scope("profile", "realdebrid"),
        };
        let token = seal(&value, &key).unwrap();
        assert!(!token.contains(hash));
        assert_eq!(open(&token, &key).unwrap().filename, value.filename);
        assert!(open(&token, &[8; 32]).is_none());
        assert!(open(&token.replace("T-", "T-x"), &key).is_none());
        assert!(
            super::super::profile::try_decrypt_secrets(
                token.trim_start_matches("T-").split('.').next().unwrap(),
                &key
            )
            .is_none()
        );
    }
}
