use fred::prelude::{Expiration, KeysInterface};
use serde_json::Value;
use sqlx::PgPool;
use tracing::warn;

const REDIS_KEY_PREFIX: &str = "user_profile:";
const UUID_CACHE_TTL: u64 = 2_592_000; // 30 days, matches Python

/// Lookup a U-prefixed profile UUID.
/// Checks Redis first (`user_profile:{uuid}`), falls back to Postgres.
/// Decrypts `encrypted_secrets` from Redis cache and merges tokens back
/// into the `sps` (streaming_providers) array before returning.
fn config_is_usable(config: &Value) -> bool {
    config.as_object().is_some_and(|o| !o.is_empty())
}

pub async fn lookup(
    redis: &fred::clients::Client,
    pool: &PgPool,
    key: &[u8; 32],
    uuid: &str,
) -> Option<Value> {
    // 1. Try Redis cache (skip stale entries that only contain `{}` — always re-read DB)
    if let Some(v) = lookup_redis(redis, key, uuid).await {
        if config_is_usable(&v) {
            return Some(v);
        }
        warn!("profile redis cache uuid={uuid} has empty config; refreshing from database");
    }
    // 2. Fall back to Postgres, then write back to Redis
    lookup_postgres(redis, pool, key, uuid).await
}

async fn lookup_redis(redis: &fred::clients::Client, key: &[u8; 32], uuid: &str) -> Option<Value> {
    let cache_key = format!("{REDIS_KEY_PREFIX}{uuid}");
    let raw: Option<Vec<u8>> = redis.get(&cache_key).await.ok().flatten();
    let raw = raw?;
    let cached: Value = serde_json::from_slice(&raw).ok()?;

    let config_val = cached.get("config")?;
    if config_val.is_null() {
        return None;
    }
    let mut config: Value = config_val.clone();
    let user_id = cached.get("user_id").and_then(|v| v.as_i64());
    let profile_id = cached.get("profile_id").and_then(|v| v.as_i64());

    // Decrypt and merge secrets
    if let Some(enc) = cached
        .get("encrypted_secrets")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
    {
        let secrets = decrypt_secrets(enc, key);
        merge_secrets(&mut config, &secrets);
    }

    // Inject uid / pid for user identification
    if let Some(obj) = config.as_object_mut() {
        if let Some(uid) = user_id {
            obj.insert("uid".into(), serde_json::json!(uid));
        }
        if let Some(pid) = profile_id {
            obj.insert("pid".into(), serde_json::json!(pid));
        }
        // api_password may be cached at top level of the Redis object
        if !obj.contains_key("ap")
            && let Some(ap) = cached.get("api_password").and_then(|v| v.as_str()) {
                obj.insert("ap".into(), serde_json::json!(ap));
            }
    }

    Some(config)
}

async fn lookup_postgres(
    redis: &fred::clients::Client,
    pool: &PgPool,
    key: &[u8; 32],
    uuid: &str,
) -> Option<Value> {
    let row: Option<(serde_json::Value, i32, i32, Option<String>)> = sqlx::query_as(
        "SELECT config, id, user_id, encrypted_secrets FROM user_profiles WHERE uuid = $1 LIMIT 1",
    )
    .bind(uuid)
    .fetch_optional(pool)
    .await
    .unwrap_or_else(|e| {
        warn!("profile postgres lookup uuid={uuid}: {e}");
        None
    });

    let Some((config, profile_id, user_id, encrypted_secrets)) = row else {
        tracing::debug!("profile postgres lookup uuid={uuid}: no row in user_profiles");
        return None;
    };

    // Write back to Redis: store config + encrypted_secrets as-is (AES-encrypted),
    // never plaintext api_password — secrets are decrypted per-request by lookup_redis.
    if let Ok(payload) = serde_json::to_string(&serde_json::json!({
        "config": config,
        "encrypted_secrets": encrypted_secrets,
        "user_id": user_id,
        "profile_id": profile_id,
        "profile_uuid": uuid,
    })) {
        let cache_key = format!("{REDIS_KEY_PREFIX}{uuid}");
        if let Err(e) = redis
            .set::<(), _, _>(
                &cache_key,
                payload,
                Some(Expiration::EX(UUID_CACHE_TTL as i64)),
                None,
                false,
            )
            .await
        {
            warn!("profile redis write-back uuid={uuid}: {e}");
        }
    }

    // Decrypt secrets and build the full config to return
    let mut full_config = config;
    if let Some(enc) = encrypted_secrets.as_deref().filter(|s| !s.is_empty()) {
        let secrets = decrypt_secrets(enc, key);
        merge_secrets(&mut full_config, &secrets);
    }
    if let Some(obj) = full_config.as_object_mut() {
        obj.insert("uid".into(), serde_json::json!(user_id as i64));
        obj.insert("pid".into(), serde_json::json!(profile_id as i64));
    }
    Some(full_config)
}

/// Merge decrypted secrets back into config — mirrors Python's `merge_secrets`.
///
/// Secrets structure (from `encrypted_secrets`):
/// ```json
/// {
///   "sps": [{"_index": 0, "tk": "..."}, {"_index": 1, "tk": "..."}],
///   "mfc": {"ap": "..."},
///   "ap": "..."
/// }
/// ```
pub fn merge_secrets(config: &mut Value, secrets: &Value) {
    let Some(secrets_obj) = secrets.as_object() else {
        return;
    };

    // Merge streaming provider tokens by _index
    for sps_key in ["sps", "streaming_providers"] {
        if let (Some(provider_secrets_arr), Some(config_sps)) = (
            secrets_obj.get(sps_key).and_then(|v| v.as_array()),
            config.get_mut(sps_key).and_then(|v| v.as_array_mut()),
        ) {
            for ps in provider_secrets_arr {
                let Some(index) = ps.get("_index").and_then(|v| v.as_u64()) else {
                    continue;
                };
                let Some(provider) = config_sps.get_mut(index as usize) else {
                    continue;
                };
                let Some(provider_obj) = provider.as_object_mut() else {
                    continue;
                };

                // Token fields
                for field in ["tk", "token", "pw", "password", "em", "email"] {
                    if let Some(val) = ps.get(field)
                        && !val.is_null() {
                            provider_obj.insert(field.into(), val.clone());
                        }
                }
                // Nested configs
                for nested_key in [
                    "qbc",
                    "qbittorrent_config",
                    "ndc",
                    "nzbdav_config",
                    "sbc",
                    "sabnzbd_config",
                    "ngc",
                    "nzbget_config",
                ] {
                    if let Some(nested_secrets) = ps.get(nested_key).and_then(|v| v.as_object()) {
                        let entry = provider_obj
                            .entry(nested_key)
                            .or_insert(Value::Object(Default::default()));
                        if let Some(entry_obj) = entry.as_object_mut() {
                            for (k, v) in nested_secrets {
                                if !v.is_null() {
                                    entry_obj.insert(k.clone(), v.clone());
                                }
                            }
                        }
                    }
                }
            }
        }
        if secrets_obj.contains_key(sps_key) {
            break;
        }
    }

    let config_obj = config.as_object_mut().unwrap();

    // MediaFlow api_password
    for mfc_key in ["mfc", "mediaflow_config"] {
        if let Some(mfc_secrets) = secrets_obj.get(mfc_key).and_then(|v| v.as_object())
            && let Some(mfc) = config_obj.get_mut(mfc_key).and_then(|v| v.as_object_mut()) {
                for (k, v) in mfc_secrets {
                    if !v.is_null() {
                        mfc.insert(k.clone(), v.clone());
                    }
                }
            }
    }

    // EasyNews credentials (stored under "enc" or "easynews_config")
    for enc_key in ["enc", "easynews_config"] {
        if let Some(enc_secrets) = secrets_obj.get(enc_key).and_then(|v| v.as_object())
            && let Some(enc) = config_obj.get_mut(enc_key).and_then(|v| v.as_object_mut()) {
                for (k, v) in enc_secrets {
                    if !v.is_null() {
                        enc.insert(k.clone(), v.clone());
                    }
                }
            }
        if secrets_obj.contains_key(enc_key) {
            break;
        }
    }

    // Top-level api_password — only fill in if not already present in config.
    // Always stored under the canonical short key "ap" to avoid the serde alias
    // "api_password" appearing alongside "ap" and triggering a duplicate-field error.
    if !config_obj.contains_key("ap") && !config_obj.contains_key("api_password") {
        for ap_key in ["ap", "api_password"] {
            if let Some(ap) = secrets_obj.get(ap_key)
                && !ap.is_null() {
                    config_obj.insert("ap".into(), ap.clone());
                    break;
                }
        }
    }
}

/// Decrypt AES-256-CBC encrypted secrets blob (no zlib, unlike D- prefix).
/// Returns empty JSON object on any error.
pub fn decrypt_secrets(encrypted: &str, key: &[u8; 32]) -> serde_json::Value {
    use aes::Aes256;
    use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
    use cbc::cipher::{BlockModeDecrypt, KeyIvInit, block_padding::Pkcs7};
    type Dec = cbc::Decryptor<Aes256>;

    // Strip base64 padding that Python's urlsafe_b64encode adds (=) so we can
    // decode both Python-encrypted (padded) and Rust-encrypted (unpadded) values.
    let stripped = encrypted.trim_end_matches('=');
    let raw = match URL_SAFE_NO_PAD.decode(stripped) {
        Ok(r) if r.len() >= 17 => r,
        _ => return serde_json::Value::Object(Default::default()),
    };
    let iv: [u8; 16] = raw[..16].try_into().unwrap();
    let mut buf = raw[16..].to_vec();
    let decrypted = match Dec::new(key.into(), &iv.into()).decrypt_padded::<Pkcs7>(&mut buf) {
        Ok(d) => d.to_vec(),
        Err(_) => return serde_json::Value::Object(Default::default()),
    };
    let s = match std::str::from_utf8(&decrypted) {
        Ok(s) => s,
        Err(_) => return serde_json::Value::Object(Default::default()),
    };
    serde_json::from_str(s).unwrap_or_else(|_| serde_json::Value::Object(Default::default()))
}

/// Encrypt a secrets JSON object with AES-256-CBC (no zlib compression).
/// Returns None if secrets is empty or encryption fails.
pub fn encrypt_secrets(secrets: &serde_json::Value, key: &[u8; 32]) -> Option<String> {
    // Only encrypt if there are actual secrets
    if !secrets.as_object().map(|o| !o.is_empty()).unwrap_or(false) {
        return None;
    }
    use aes::Aes256;
    use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
    use cbc::cipher::{BlockModeEncrypt, KeyIvInit, block_padding::Pkcs7};

    type Enc = cbc::Encryptor<Aes256>;

    let json = serde_json::to_string(secrets).ok()?;
    let bytes = json.as_bytes();
    let mut iv = [0u8; 16];
    {
        use rand_core::Rng;
        rand::rng().fill_bytes(&mut iv);
    }
    let padded_len = (bytes.len() / 16 + 1) * 16;
    let mut buf = vec![0u8; padded_len];
    buf[..bytes.len()].copy_from_slice(bytes);
    let ct = Enc::new(key.into(), &iv.into())
        .encrypt_padded::<Pkcs7>(&mut buf, bytes.len())
        .ok()?
        .to_vec();
    let mut combined = Vec::with_capacity(16 + ct.len());
    combined.extend_from_slice(&iv);
    combined.extend_from_slice(&ct);
    Some(URL_SAFE_NO_PAD.encode(&combined))
}

/// Patch a single streaming-provider token inside `encrypted_secrets` for a stored profile.
///
/// Finds the provider at `provider_index` (its position in the `sps` array) within the
/// decrypted secrets, updates its `tk` field, re-encrypts, writes back to Postgres, and
/// invalidates the Redis profile cache so the next lookup picks up the new token.
///
/// Non-fatal: all errors are logged as warnings so the calling request is never blocked.
pub async fn patch_provider_token(
    pool: &PgPool,
    redis: &fred::clients::Client,
    secret_key: &[u8; 32],
    profile_id: i64,
    provider_index: usize,
    new_token: &str,
) {
    // 1. Fetch current encrypted_secrets + uuid from DB
    let row: Option<(String, Option<String>)> =
        sqlx::query_as("SELECT uuid, encrypted_secrets FROM user_profiles WHERE id = $1 LIMIT 1")
            .bind(profile_id)
            .fetch_optional(pool)
            .await
            .unwrap_or_else(|e| {
                warn!("patch_provider_token: DB fetch failed profile_id={profile_id}: {e}");
                None
            });
    let Some((uuid, enc)) = row else {
        warn!("patch_provider_token: profile_id={profile_id} not found");
        return;
    };

    // 2. Decrypt existing secrets (empty object if none)
    let mut secrets = enc
        .as_deref()
        .filter(|s| !s.is_empty())
        .map(|s| decrypt_secrets(s, secret_key))
        .unwrap_or_else(|| Value::Object(Default::default()));

    // 3. Patch the token: find or create the provider entry in secrets["sps"]
    let sps = secrets
        .as_object_mut()
        .unwrap()
        .entry("sps")
        .or_insert_with(|| Value::Array(vec![]))
        .as_array_mut()
        .unwrap();

    if let Some(entry) = sps
        .iter_mut()
        .find(|e| e.get("_index").and_then(|v| v.as_u64()) == Some(provider_index as u64))
    {
        entry
            .as_object_mut()
            .unwrap()
            .insert("tk".into(), Value::String(new_token.to_string()));
    } else {
        sps.push(serde_json::json!({
            "_index": provider_index,
            "tk": new_token,
        }));
    }

    // 4. Re-encrypt
    let Some(new_enc) = encrypt_secrets(&secrets, secret_key) else {
        warn!("patch_provider_token: re-encryption failed profile_id={profile_id}");
        return;
    };

    // 5. Write back to Postgres
    if let Err(e) = sqlx::query(
        "UPDATE user_profiles SET encrypted_secrets = $1, updated_at = NOW() WHERE id = $2",
    )
    .bind(&new_enc)
    .bind(profile_id)
    .execute(pool)
    .await
    {
        warn!("patch_provider_token: DB update failed profile_id={profile_id}: {e}");
        return;
    }

    // 6. Bust Redis profile cache so next lookup reads the fresh token from DB
    let cache_key = format!("{REDIS_KEY_PREFIX}{uuid}");
    if let Err(e) = redis.del::<(), _>(cache_key).await {
        warn!("patch_provider_token: Redis invalidation failed uuid={uuid}: {e}");
    }
}

/// Remove a streaming-provider token from `encrypted_secrets` (e.g. after format migration).
///
/// Same persistence path as [`patch_provider_token`], but deletes the `tk` field instead of
/// updating it. Non-fatal — errors are logged as warnings.
pub async fn clear_provider_token(
    pool: &PgPool,
    redis: &fred::clients::Client,
    secret_key: &[u8; 32],
    profile_id: i64,
    provider_index: usize,
) {
    let row: Option<(String, Option<String>)> =
        sqlx::query_as("SELECT uuid, encrypted_secrets FROM user_profiles WHERE id = $1 LIMIT 1")
            .bind(profile_id)
            .fetch_optional(pool)
            .await
            .unwrap_or_else(|e| {
                warn!("clear_provider_token: DB fetch failed profile_id={profile_id}: {e}");
                None
            });
    let Some((uuid, enc)) = row else {
        warn!("clear_provider_token: profile_id={profile_id} not found");
        return;
    };

    let Some(enc_str) = enc.filter(|s| !s.is_empty()) else {
        return;
    };
    let mut secrets = decrypt_secrets(&enc_str, secret_key);
    let Some(sps) = secrets
        .as_object_mut()
        .and_then(|o| o.get_mut("sps"))
        .and_then(|v| v.as_array_mut())
    else {
        return;
    };

    let Some(entry) = sps
        .iter_mut()
        .find(|e| e.get("_index").and_then(|v| v.as_u64()) == Some(provider_index as u64))
    else {
        return;
    };
    entry.as_object_mut().map(|o| o.remove("tk"));

    let Some(new_enc) = encrypt_secrets(&secrets, secret_key) else {
        warn!("clear_provider_token: re-encryption failed profile_id={profile_id}");
        return;
    };

    if let Err(e) = sqlx::query(
        "UPDATE user_profiles SET encrypted_secrets = $1, updated_at = NOW() WHERE id = $2",
    )
    .bind(&new_enc)
    .bind(profile_id)
    .execute(pool)
    .await
    {
        warn!("clear_provider_token: DB update failed profile_id={profile_id}: {e}");
        return;
    }

    let cache_key = format!("{REDIS_KEY_PREFIX}{uuid}");
    if let Err(e) = redis.del::<(), _>(cache_key).await {
        warn!("clear_provider_token: Redis invalidation failed uuid={uuid}: {e}");
    }
}
