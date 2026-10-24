//! Credential fields shared by profile masking, storage, and the startup backfill.
use serde_json::{Map, Value, json};

pub const FIELDS: &[&str] = &[
    "qb_password",
    "qpw",
    "qb_username",
    "qus",
    "qbittorrent_password",
    "qbittorrent_username",
    "webdav_password",
    "wpw",
    "webdav_username",
    "wus",
];

pub fn extract(provider: &mut Map<String, Value>) -> Map<String, Value> {
    let mut secrets = Map::new();
    for key in ["qbc", "qbittorrent_config"] {
        if let Some(config) = provider.get_mut(key).and_then(Value::as_object_mut) {
            let mut fields = Map::new();
            for field in FIELDS {
                if let Some(value) = config.remove(*field) {
                    fields.insert((*field).into(), value);
                }
            }
            if !fields.is_empty() {
                secrets.insert(key.into(), Value::Object(fields));
            }
        }
    }
    secrets
}

fn merge_provider(
    destination: &mut Value,
    extracted: Map<String, Value>,
) -> Result<(), &'static str> {
    let provider = destination
        .as_object_mut()
        .ok_or("Invalid provider secrets")?;
    for (key, fields) in extracted {
        let config = provider.entry(key).or_insert_with(|| json!({}));
        let destination = config
            .as_object_mut()
            .ok_or("Invalid qBittorrent secrets")?;
        for (field, value) in fields.as_object().unwrap() {
            // Encrypted values are authoritative, as in normal profile reads.
            if destination.get(field).is_none_or(Value::is_null) {
                destination.insert(field.clone(), value.clone());
            }
        }
    }
    Ok(())
}

/// Move plaintext credentials into the existing encrypted blob without discarding
/// other providers or unknown secret fields. Refuse to overwrite an unreadable blob.
pub fn reprotect(
    config: &Value,
    encrypted: Option<&str>,
    key: &[u8; 32],
) -> Result<Option<(Value, String)>, &'static str> {
    let mut clean = config.clone();
    let mut updates = Vec::new();
    for array_key in ["sps", "streaming_providers"] {
        if let Some(providers) = clean.get_mut(array_key).and_then(Value::as_array_mut) {
            for (index, provider) in providers.iter_mut().enumerate() {
                if let Some(provider) = provider.as_object_mut() {
                    let extracted = extract(provider);
                    if !extracted.is_empty() {
                        updates.push((array_key, Some(index), extracted));
                    }
                }
            }
        }
    }
    for single_key in ["sp", "streaming_provider"] {
        if let Some(provider) = clean.get_mut(single_key).and_then(Value::as_object_mut) {
            let extracted = extract(provider);
            if !extracted.is_empty() {
                updates.push((single_key, None, extracted));
            }
        }
    }
    if updates.is_empty() {
        return Ok(None);
    }
    let mut secrets = match encrypted.filter(|s| !s.is_empty()) {
        Some(blob) => super::profile::try_decrypt_secrets(blob, key)
            .ok_or("Cannot decrypt existing profile secrets")?,
        None => json!({}),
    };
    let secrets_obj = secrets.as_object_mut().ok_or("Invalid profile secrets")?;
    for (provider_key, index, extracted) in updates {
        let destination = if let Some(index) = index {
            let entries = secrets_obj
                .entry(provider_key)
                .or_insert_with(|| json!([]))
                .as_array_mut()
                .ok_or("Invalid provider secrets array")?;
            let position = entries
                .iter()
                .position(|p| p["_index"].as_u64() == Some(index as u64));
            let position = position.unwrap_or_else(|| {
                entries.push(json!({"_index": index}));
                entries.len() - 1
            });
            &mut entries[position]
        } else {
            secrets_obj.entry(provider_key).or_insert_with(|| json!({}))
        };
        merge_provider(destination, extracted)?;
    }
    let encrypted =
        super::profile::encrypt_secrets(&secrets, key).ok_or("Cannot encrypt profile secrets")?;
    Ok(Some((clean, encrypted)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::crypto::profile::{decrypt_secrets, encrypt_secrets, merge_secrets};

    #[test]
    fn backfill_preserves_other_secrets_and_is_idempotent() {
        let key = [17; 32];
        for provider_key in ["sps", "streaming_providers", "sp", "streaming_provider"] {
            for config_key in ["qbc", "qbittorrent_config"] {
                let plural = matches!(provider_key, "sps" | "streaming_providers");
                let provider =
                    json!({"sv":"qbittorrent", config_key:{"wpw":"dav-secret", "qur":"http://qb"}});
                let config = json!({provider_key: if plural {json!([provider])} else {provider}});
                let existing =
                    json!({"_index":0,"tk":"keep-token", config_key:{"qpw":"qb-secret"}});
                let secrets = json!({"unknown":{"keep":true}, provider_key: if plural {json!([existing])} else {existing}});
                let encrypted = encrypt_secrets(&secrets, &key).unwrap();
                let (clean, new_encrypted) =
                    reprotect(&config, Some(&encrypted), &key).unwrap().unwrap();
                let new_secrets = decrypt_secrets(&new_encrypted, &key);
                assert_eq!(new_secrets["unknown"], secrets["unknown"]);
                let provider = if plural {
                    &new_secrets[provider_key][0]
                } else {
                    &new_secrets[provider_key]
                };
                assert_eq!(provider["tk"], "keep-token");
                assert_eq!(provider[config_key]["qpw"], "qb-secret");
                assert_eq!(provider[config_key]["wpw"], "dav-secret");
                let mut restored = clean.clone();
                merge_secrets(&mut restored, &new_secrets);
                let provider = if plural {
                    &restored[provider_key][0]
                } else {
                    &restored[provider_key]
                };
                assert_eq!(provider[config_key]["wpw"], "dav-secret");
                assert_eq!(provider[config_key]["qpw"], "qb-secret");
                assert!(
                    reprotect(&clean, Some(&new_encrypted), &key)
                        .unwrap()
                        .is_none()
                );
            }
        }
    }

    #[test]
    fn backfill_keeps_authoritative_encrypted_values_when_plaintext_is_stale() {
        let key = [17; 32];
        let config = json!({"sps":[{"sv":"qbittorrent", "qbc":{"wpw":"stale"}}]});
        let encrypted =
            encrypt_secrets(&json!({"sps":[{"_index":0,"qbc":{"wpw":"current"}}]}), &key).unwrap();
        let (mut clean, encrypted) = reprotect(&config, Some(&encrypted), &key).unwrap().unwrap();
        merge_secrets(&mut clean, &decrypt_secrets(&encrypted, &key));
        assert_eq!(clean["sps"][0]["qbc"]["wpw"], "current");
    }

    #[test]
    fn backfill_refuses_to_replace_unreadable_secrets() {
        let config = json!({"sps":[{"sv":"qbittorrent", "qbc":{"wpw":"secret"}}]});
        let key = [17; 32];
        assert!(reprotect(&config, Some("invalid"), &key).is_err());
        let encrypted = encrypt_secrets(&json!({"tk":"existing"}), &[18; 32]).unwrap();
        assert!(reprotect(&config, Some(&encrypted), &key).is_err());
    }
}
