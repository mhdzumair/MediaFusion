/// User profile management endpoints.
///
/// Routes (prefix /api/v1/profiles):
///   GET    /user-config          → user_config
///   GET    /rpdb-key             → rpdb_key
///   GET    /                     → list_profiles
///   POST   /                     → create_profile
///   GET    /{id}                 → get_profile
///   PUT    /{id}                 → update_profile
///   DELETE /{id}                 → delete_profile
///   POST   /{id}/set-default     → set_default
///   POST   /{id}/reset-uuid      → reset_uuid
///   GET    /{id}/manifest-url    → manifest_url
///   GET    /{id}/kodi-addon      → kodi_addon
use std::sync::Arc;

use axum::{
    extract::{Path, State},
    http::{header, HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use chrono::{DateTime, Utc};
use serde::Deserialize;
use serde_json::Value;

use crate::{models::user_data::UserData, providers::validator, state::AppState};

/// Remove long-form alias keys when the canonical short-form key is also present.
/// This prevents serde "duplicate field" errors when a client sends both forms.
/// Pairs: ("ap", "api_password")
fn normalize_config_aliases(config: &mut Value) {
    if let Some(obj) = config.as_object_mut() {
        if obj.contains_key("ap") {
            obj.remove("api_password");
        } else if let Some(v) = obj.remove("api_password") {
            obj.insert("ap".into(), v);
        }
    }
}

async fn validate_profile_config(
    state: &AppState,
    headers: &HeaderMap,
    config: &Value,
) -> Option<Response> {
    let mut config_normalized = config.clone();
    normalize_config_aliases(&mut config_normalized);
    let user_data: UserData = match serde_json::from_value(config_normalized) {
        Ok(u) => u,
        Err(e) => {
            return Some(
                (
                    StatusCode::BAD_REQUEST,
                    Json(serde_json::json!({"detail": format!("Invalid profile config: {e}")})),
                )
                    .into_response(),
            );
        }
    };
    let default_nzbdav = state
        .config
        .default_nzbdav_url
        .as_ref()
        .zip(state.config.default_nzbdav_api_key.as_ref())
        .map(|(url, key)| {
            serde_json::json!({
                "url": url,
                "api_key": key,
            })
        });
    let user_ip = validator::client_ip_from_headers(headers);
    let (no_proxy, excluded) = state.proxy_bypass_clients();
    let result = validator::validate_provider_credentials(
        &state.http,
        no_proxy,
        excluded,
        &user_data,
        user_ip.as_deref(),
        default_nzbdav.as_ref(),
    )
    .await;
    validator::validation_error_response(&result).map(|msg| {
        (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"detail": msg})),
        )
            .into_response()
    })
}

// ─── Auth helper ─────────────────────────────────────────────────────────────

fn validate_token(headers: &HeaderMap, secret_key: &str) -> Option<i32> {
    crate::routes::auth_guard::decode_access_token(headers, secret_key)
        .ok()
        .map(|(id, _)| id)
}

// ─── DB row ──────────────────────────────────────────────────────────────────

struct ProfileRow {
    id: i32,
    uuid: String,
    user_id: i32,
    name: String,
    config: serde_json::Value,
    encrypted_secrets: Option<String>,
    is_default: bool,
    created_at: DateTime<Utc>,
}

// ─── Request/Response types ───────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct CreateProfileRequest {
    pub name: String,
    #[serde(default)]
    pub config: serde_json::Value,
    #[serde(default)]
    pub is_default: bool,
}

#[derive(Deserialize)]
pub struct UpdateProfileRequest {
    pub name: Option<String>,
    pub config: Option<serde_json::Value>,
    pub is_default: Option<bool>,
}

// ─── Config helpers ───────────────────────────────────────────────────────────

/// Merge secrets into the config JSON (secrets fields overlay config fields).
/// Arrays are merged element-by-element so the base structure (with sv, n, pr, en, etc.)
/// is preserved while secrets (tk, em, pw, etc.) are injected into each element.
fn deep_merge(base: &mut serde_json::Value, overlay: serde_json::Value) {
    match (base, overlay) {
        (Value::Object(base_map), Value::Object(overlay_map)) => {
            for (k, v) in overlay_map {
                let entry = base_map.entry(k).or_insert(Value::Null);
                deep_merge(entry, v);
            }
        }
        (Value::Array(base_arr), Value::Array(overlay_arr)) => {
            // Merge each element pair; base array is authoritative for length/structure.
            for (base_el, overlay_el) in base_arr.iter_mut().zip(overlay_arr) {
                deep_merge(base_el, overlay_el);
            }
        }
        (base, overlay) => {
            *base = overlay;
        }
    }
}

/// Restore full config by decrypting secrets and merging into stored config.
fn get_full_config(row: &ProfileRow, key: &[u8; 32]) -> serde_json::Value {
    let mut config = row.config.clone();
    if let Some(enc) = &row.encrypted_secrets {
        let secrets = crate::crypto::profile::decrypt_secrets(enc, key);
        deep_merge(&mut config, secrets);
    }
    config
}

const MASK: &str = "••••••••";

/// Mask a single JSON object's sensitive fields in-place.
fn mask_object_fields(obj: &mut serde_json::Map<String, Value>, fields: &[&str]) {
    for field in fields {
        if let Some(v) = obj.get_mut(*field) {
            if v.as_str().map(|s| !s.is_empty()).unwrap_or(false) {
                *v = Value::String(MASK.to_string());
            }
        }
    }
}

/// Mask all sensitive credential fields in a config value.
pub fn mask_config(mut config: serde_json::Value) -> serde_json::Value {
    // Top-level api_password / ap
    if let Some(obj) = config.as_object_mut() {
        mask_object_fields(obj, &["api_password", "ap"]);
    }

    // streaming_providers / sps array
    let sps_keys = ["streaming_providers", "sps"];
    for key in &sps_keys {
        if let Some(Value::Array(providers)) = config.get_mut(*key) {
            for provider in providers.iter_mut() {
                if let Some(obj) = provider.as_object_mut() {
                    mask_object_fields(obj, &["token", "tk", "password", "pw", "email", "em"]);
                    // qbittorrent_config / qbc
                    for qbc_key in &["qbittorrent_config", "qbc"] {
                        if let Some(Value::Object(qbc)) = obj.get_mut(*qbc_key) {
                            mask_object_fields(qbc, &["qb_password", "qpw", "qb_username", "qus"]);
                        }
                    }
                    // mediaflow_config / mfc
                    for mfc_key in &["mediaflow_config", "mfc"] {
                        if let Some(Value::Object(mfc)) = obj.get_mut(*mfc_key) {
                            mask_object_fields(mfc, &["api_password", "ap"]);
                        }
                    }
                    // rpdb_config / rpc
                    for rpc_key in &["rpdb_config", "rpc"] {
                        if let Some(Value::Object(rpc)) = obj.get_mut(*rpc_key) {
                            mask_object_fields(rpc, &["api_key", "ak"]);
                        }
                    }
                    // mdblist_config / mdb
                    for mdb_key in &["mdblist_config", "mdb"] {
                        if let Some(Value::Object(mdb)) = obj.get_mut(*mdb_key) {
                            mask_object_fields(mdb, &["api_key", "ak"]);
                        }
                    }
                    // easynews_config / enc
                    for enc_key in &["easynews_config", "enc"] {
                        if let Some(Value::Object(enc)) = obj.get_mut(*enc_key) {
                            mask_object_fields(enc, &["username", "un", "password", "pw"]);
                        }
                    }
                }
            }
        }
    }

    // streaming_provider / sp object
    let sp_keys = ["streaming_provider", "sp"];
    for key in &sp_keys {
        if let Some(Value::Object(obj)) = config.get_mut(*key) {
            mask_object_fields(obj, &["token", "tk", "password", "pw", "email", "em"]);
            for qbc_key in &["qbittorrent_config", "qbc"] {
                if let Some(Value::Object(qbc)) = obj.get_mut(*qbc_key) {
                    mask_object_fields(qbc, &["qb_password", "qpw", "qb_username", "qus"]);
                }
            }
            for mfc_key in &["mediaflow_config", "mfc"] {
                if let Some(Value::Object(mfc)) = obj.get_mut(*mfc_key) {
                    mask_object_fields(mfc, &["api_password", "ap"]);
                }
            }
            for rpc_key in &["rpdb_config", "rpc"] {
                if let Some(Value::Object(rpc)) = obj.get_mut(*rpc_key) {
                    mask_object_fields(rpc, &["api_key", "ak"]);
                }
            }
            for mdb_key in &["mdblist_config", "mdb"] {
                if let Some(Value::Object(mdb)) = obj.get_mut(*mdb_key) {
                    mask_object_fields(mdb, &["api_key", "ak"]);
                }
            }
            for enc_key in &["easynews_config", "enc"] {
                if let Some(Value::Object(enc)) = obj.get_mut(*enc_key) {
                    mask_object_fields(enc, &["username", "un", "password", "pw"]);
                }
            }
        }
    }

    config
}

/// Build the streaming_providers summary from a full (unmasked) config.
fn build_streaming_providers_summary(full_config: &serde_json::Value) -> serde_json::Value {
    let providers_arr = full_config
        .get("streaming_providers")
        .or_else(|| full_config.get("sps"))
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    let mut providers = Vec::new();
    let mut has_debrid = false;
    let mut primary_service: Option<String> = None;

    for (i, p) in providers_arr.iter().enumerate() {
        let service = p
            .get("service")
            .or_else(|| p.get("sv")) // Python alias used by frontend
            .or_else(|| p.get("svc"))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let enabled = p
            .get("enabled")
            .or_else(|| p.get("en"))
            .and_then(|v| v.as_bool())
            .unwrap_or(true);
        let priority = p
            .get("priority")
            .or_else(|| p.get("pr"))
            .and_then(|v| v.as_i64())
            .unwrap_or(i as i64);
        // Check if credentials exist (non-empty token/password/email)
        let has_credentials = ["token", "tk", "password", "pw", "email", "em"]
            .iter()
            .any(|k| {
                p.get(*k)
                    .and_then(|v| v.as_str())
                    .map(|s| !s.is_empty())
                    .unwrap_or(false)
            });
        if enabled && has_credentials {
            has_debrid = true;
        }
        if i == 0 && !service.is_empty() {
            primary_service = Some(service.clone());
        }
        providers.push(serde_json::json!({
            "service": service,
            "enabled": enabled,
            "priority": priority,
            "has_credentials": has_credentials,
        }));
    }

    serde_json::json!({
        "providers": providers,
        "has_debrid": has_debrid,
        "primary_service": primary_service,
    })
}

/// Build the single streaming_provider summary from full config.
fn build_streaming_provider_summary(full_config: &serde_json::Value) -> serde_json::Value {
    let sp = full_config
        .get("streaming_provider")
        .or_else(|| full_config.get("sp"));
    match sp {
        Some(Value::Object(obj)) => {
            let service = obj
                .get("service")
                .or_else(|| obj.get("sv"))
                .or_else(|| obj.get("svc"))
                .and_then(|v| v.as_str())
                .map(|s| s.to_string());
            let is_configured = ["token", "tk", "password", "pw", "email", "em"]
                .iter()
                .any(|k| {
                    obj.get(*k)
                        .and_then(|v| v.as_str())
                        .map(|s| !s.is_empty())
                        .unwrap_or(false)
                });
            serde_json::json!({"service": service, "is_configured": is_configured})
        }
        _ => serde_json::json!({"service": null, "is_configured": false}),
    }
}

/// Count enabled catalog configs.
fn count_catalogs_enabled(full_config: &serde_json::Value) -> usize {
    let cc = full_config
        .get("catalog_configs")
        .or_else(|| full_config.get("cc"))
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    cc.iter()
        .filter(|c| {
            c.get("enabled")
                .or_else(|| c.get("en"))
                .and_then(|v| v.as_bool())
                .unwrap_or(true)
        })
        .count()
}

/// Build a profile JSON response (masked or full config depending on flag).
fn build_profile_response(
    row: &ProfileRow,
    key: &[u8; 32],
    full_config_flag: bool,
) -> serde_json::Value {
    let full = get_full_config(row, key);
    let streaming_providers = build_streaming_providers_summary(&full);
    let streaming_provider = build_streaming_provider_summary(&full);
    let catalogs_enabled = count_catalogs_enabled(&full);
    let config = if full_config_flag {
        full
    } else {
        mask_config(full)
    };

    serde_json::json!({
        "id": row.id,
        "uuid": row.uuid,
        "user_id": row.user_id,
        "name": row.name,
        "config": config,
        "is_default": row.is_default,
        "created_at": row.created_at.to_rfc3339(),
        "streaming_providers": streaming_providers,
        "streaming_provider": streaming_provider,
        "catalogs_enabled": catalogs_enabled,
    })
}

// ─── Config split/merge helpers ───────────────────────────────────────────────

/// Extract sensitive fields from a provider object into a secrets dict entry.
/// The `_index` field is included so `crypto::profile::merge_secrets` can locate
/// the right provider when re-injecting secrets during stream UUID lookups.
/// Returns (clean provider obj, secrets entry).
fn extract_provider_secrets(
    provider: &serde_json::Map<String, Value>,
    index: usize,
) -> (
    serde_json::Map<String, Value>,
    serde_json::Map<String, Value>,
) {
    let mut clean = provider.clone();
    let mut secrets_entry: serde_json::Map<String, Value> = serde_json::Map::new();
    secrets_entry.insert("_index".to_string(), serde_json::json!(index));
    let sensitive_fields = ["token", "tk", "password", "pw", "email", "em"];
    for f in &sensitive_fields {
        if let Some(v) = clean.remove(*f) {
            if v.as_str().map(|s| !s.is_empty()).unwrap_or(false) {
                secrets_entry.insert((*f).to_string(), v);
            }
        }
    }
    // Also extract from sub-configs
    for sub_key in &["qbittorrent_config", "qbc"] {
        if let Some(Value::Object(sub)) = clean.get_mut(*sub_key) {
            let mut sub_secrets: serde_json::Map<String, Value> = serde_json::Map::new();
            for f in &["qb_password", "qpw", "qb_username", "qus"] {
                if let Some(v) = sub.remove(*f) {
                    if v.as_str().map(|s| !s.is_empty()).unwrap_or(false) {
                        sub_secrets.insert((*f).to_string(), v);
                    }
                }
            }
            if !sub_secrets.is_empty() {
                secrets_entry.insert((*sub_key).to_string(), Value::Object(sub_secrets));
            }
        }
    }
    for sub_key in &["mediaflow_config", "mfc"] {
        if let Some(Value::Object(sub)) = clean.get_mut(*sub_key) {
            let mut sub_secrets: serde_json::Map<String, Value> = serde_json::Map::new();
            for f in &["api_password", "ap"] {
                if let Some(v) = sub.remove(*f) {
                    if v.as_str().map(|s| !s.is_empty()).unwrap_or(false) {
                        sub_secrets.insert((*f).to_string(), v);
                    }
                }
            }
            if !sub_secrets.is_empty() {
                secrets_entry.insert((*sub_key).to_string(), Value::Object(sub_secrets));
            }
        }
    }
    for sub_key in &["rpdb_config", "rpc"] {
        if let Some(Value::Object(sub)) = clean.get_mut(*sub_key) {
            let mut sub_secrets: serde_json::Map<String, Value> = serde_json::Map::new();
            for f in &["api_key", "ak"] {
                if let Some(v) = sub.remove(*f) {
                    if v.as_str().map(|s| !s.is_empty()).unwrap_or(false) {
                        sub_secrets.insert((*f).to_string(), v);
                    }
                }
            }
            if !sub_secrets.is_empty() {
                secrets_entry.insert((*sub_key).to_string(), Value::Object(sub_secrets));
            }
        }
    }
    for sub_key in &["mdblist_config", "mdb"] {
        if let Some(Value::Object(sub)) = clean.get_mut(*sub_key) {
            let mut sub_secrets: serde_json::Map<String, Value> = serde_json::Map::new();
            for f in &["api_key", "ak"] {
                if let Some(v) = sub.remove(*f) {
                    if v.as_str().map(|s| !s.is_empty()).unwrap_or(false) {
                        sub_secrets.insert((*f).to_string(), v);
                    }
                }
            }
            if !sub_secrets.is_empty() {
                secrets_entry.insert((*sub_key).to_string(), Value::Object(sub_secrets));
            }
        }
    }
    for sub_key in &["easynews_config", "enc"] {
        if let Some(Value::Object(sub)) = clean.get_mut(*sub_key) {
            let mut sub_secrets: serde_json::Map<String, Value> = serde_json::Map::new();
            for f in &["username", "un", "password", "pw"] {
                if let Some(v) = sub.remove(*f) {
                    if v.as_str().map(|s| !s.is_empty()).unwrap_or(false) {
                        sub_secrets.insert((*f).to_string(), v);
                    }
                }
            }
            if !sub_secrets.is_empty() {
                secrets_entry.insert((*sub_key).to_string(), Value::Object(sub_secrets));
                // Store only under canonical short key to avoid duplicates on re-save
                break;
            }
        }
    }
    (clean, secrets_entry)
}

/// Split a config into (clean_config, encrypted_secrets).
/// Extracts sensitive credential fields from providers and encrypts them separately.
pub fn split_config(config: &Value, key: &[u8; 32]) -> (Value, Option<String>) {
    let mut clean = config.clone();
    let mut all_secrets = serde_json::Map::new();

    // Extract from streaming_providers / sps
    let sps_key = if clean.get("streaming_providers").is_some() {
        "streaming_providers"
    } else {
        "sps"
    };
    if let Some(Value::Array(providers)) = clean.get_mut(sps_key) {
        let mut secrets_list = Vec::new();
        let mut clean_providers = Vec::new();
        for (i, provider) in providers.drain(..).enumerate() {
            if let Value::Object(obj) = provider {
                let (clean_obj, secrets_entry) = extract_provider_secrets(&obj, i);
                clean_providers.push(Value::Object(clean_obj));
                // Always push the entry — it carries at minimum `_index` so merge_secrets
                // can locate the right provider slot during stream UUID lookups.
                secrets_list.push(Value::Object(secrets_entry));
            } else {
                clean_providers.push(provider);
                secrets_list.push(serde_json::json!({"_index": i}));
            }
        }
        *providers = clean_providers;
        // Only persist secrets when at least one provider has real credentials
        // (more keys than just `_index`).
        if secrets_list
            .iter()
            .any(|s| s.as_object().map(|o| o.len() > 1).unwrap_or(false))
        {
            all_secrets.insert(sps_key.to_string(), Value::Array(secrets_list));
        }
    }

    // Extract from streaming_provider / sp
    let sp_key = if clean.get("streaming_provider").is_some() {
        "streaming_provider"
    } else {
        "sp"
    };
    if let Some(Value::Object(obj)) = clean.get_mut(sp_key) {
        let (clean_obj, secrets_entry) = extract_provider_secrets(obj, 0);
        *obj = clean_obj;
        if !secrets_entry.is_empty() {
            all_secrets.insert(sp_key.to_string(), Value::Object(secrets_entry));
        }
    }

    // Extract top-level api_password / ap — normalize to "ap" to prevent duplicate-field errors
    // when secrets are later merged back and the config is deserialized.
    normalize_config_aliases(&mut clean);
    if let Some(obj) = clean.as_object_mut() {
        if let Some(v) = obj.remove("ap") {
            if v.as_str().map(|s| !s.is_empty()).unwrap_or(false) {
                all_secrets.insert("ap".to_string(), v);
            }
        }
    }

    let secrets_val = Value::Object(all_secrets);
    let encrypted = crate::crypto::profile::encrypt_secrets(&secrets_val, key);
    (clean, encrypted)
}

/// When updating, restore masked values from the existing full config.
/// If a field in new_config contains the MASK sentinel, replace with value from existing_full.
fn restore_masked(new_config: &mut Value, existing_full: &Value) {
    match (new_config, existing_full) {
        (Value::Object(new_map), Value::Object(existing_map)) => {
            for (k, new_v) in new_map.iter_mut() {
                if let Some(existing_v) = existing_map.get(k) {
                    if new_v.as_str() == Some(MASK) {
                        *new_v = existing_v.clone();
                    } else {
                        restore_masked(new_v, existing_v);
                    }
                }
            }
        }
        (Value::Array(new_arr), Value::Array(existing_arr)) => {
            for (new_v, existing_v) in new_arr.iter_mut().zip(existing_arr.iter()) {
                restore_masked(new_v, existing_v);
            }
        }
        _ => {}
    }
}

// ─── DB helpers ───────────────────────────────────────────────────────────────

#[allow(clippy::type_complexity)]
async fn fetch_profile_by_id(
    pool: &sqlx::PgPool,
    id: i32,
    user_id: i32,
) -> Result<Option<ProfileRow>, sqlx::Error> {
    let row: Option<(
        i32,
        String,
        i32,
        String,
        serde_json::Value,
        Option<String>,
        bool,
        DateTime<Utc>,
    )> = sqlx::query_as(
        r#"SELECT id, uuid, user_id, name, config, encrypted_secrets, is_default, created_at
               FROM user_profiles
               WHERE id = $1 AND user_id = $2"#,
    )
    .bind(id)
    .bind(user_id)
    .fetch_optional(pool)
    .await?;
    Ok(row.map(
        |(id, uuid, user_id, name, config, encrypted_secrets, is_default, created_at)| ProfileRow {
            id,
            uuid,
            user_id,
            name,
            config,
            encrypted_secrets,
            is_default,
            created_at,
        },
    ))
}

#[allow(clippy::type_complexity)]
async fn fetch_default_profile(
    pool: &sqlx::PgPool,
    user_id: i32,
) -> Result<Option<ProfileRow>, sqlx::Error> {
    let row: Option<(
        i32,
        String,
        i32,
        String,
        serde_json::Value,
        Option<String>,
        bool,
        DateTime<Utc>,
    )> = sqlx::query_as(
        r#"SELECT id, uuid, user_id, name, config, encrypted_secrets, is_default, created_at
               FROM user_profiles
               WHERE user_id = $1 AND is_default = true
               LIMIT 1"#,
    )
    .bind(user_id)
    .fetch_optional(pool)
    .await?;
    Ok(row.map(
        |(id, uuid, user_id, name, config, encrypted_secrets, is_default, created_at)| ProfileRow {
            id,
            uuid,
            user_id,
            name,
            config,
            encrypted_secrets,
            is_default,
            created_at,
        },
    ))
}

// ─── Handlers ────────────────────────────────────────────────────────────────

/// GET /api/v1/profiles/user-config
pub async fn user_config(headers: HeaderMap, State(state): State<Arc<AppState>>) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let profile = match fetch_default_profile(&state.pool_ro, user_id).await {
        Ok(Some(p)) => p,
        Ok(None) => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({"detail": "No default profile found"})),
            )
                .into_response();
        }
        Err(e) => {
            tracing::error!("user_config db error: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    let full = get_full_config(&profile, &state.config.secret_key);
    let masked = mask_config(full);

    (
        StatusCode::OK,
        [
            (header::CACHE_CONTROL, "no-cache, no-store, must-revalidate"),
            (header::PRAGMA, "no-cache"),
        ],
        Json(serde_json::json!({
            "user_data": masked,
            "configured_fields": [],
        })),
    )
        .into_response()
}

/// GET /api/v1/profiles/rpdb-key
pub async fn rpdb_key(headers: HeaderMap, State(state): State<Arc<AppState>>) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let profile = match fetch_default_profile(&state.pool_ro, user_id).await {
        Ok(Some(p)) => p,
        Ok(None) => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({"detail": "No default profile found"})),
            )
                .into_response();
        }
        Err(e) => {
            tracing::error!("rpdb_key db error: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    let full = get_full_config(&profile, &state.config.secret_key);
    let api_key = full
        .get("rpdb_config")
        .or_else(|| full.get("rpc"))
        .and_then(|v| v.get("api_key").or_else(|| v.get("ak")))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    Json(serde_json::json!({"rpdb_api_key": api_key})).into_response()
}

/// GET /api/v1/profiles
#[allow(clippy::type_complexity)]
pub async fn list_profiles(headers: HeaderMap, State(state): State<Arc<AppState>>) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let rows: Vec<(
        i32,
        String,
        i32,
        String,
        serde_json::Value,
        Option<String>,
        bool,
        DateTime<Utc>,
    )> = match sqlx::query_as(
        r#"SELECT id, uuid, user_id, name, config, encrypted_secrets, is_default, created_at
               FROM user_profiles
               WHERE user_id = $1
               ORDER BY is_default DESC, created_at ASC"#,
    )
    .bind(user_id)
    .fetch_all(&state.pool_ro)
    .await
    {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("list_profiles db error: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    let profiles: Vec<serde_json::Value> = rows
        .iter()
        .map(
            |(id, uuid, user_id, name, config, encrypted_secrets, is_default, created_at)| {
                let row = ProfileRow {
                    id: *id,
                    uuid: uuid.clone(),
                    user_id: *user_id,
                    name: name.clone(),
                    config: config.clone(),
                    encrypted_secrets: encrypted_secrets.clone(),
                    is_default: *is_default,
                    created_at: *created_at,
                };
                build_profile_response(&row, &state.config.secret_key, false)
            },
        )
        .collect();

    (
        StatusCode::OK,
        [
            (header::CACHE_CONTROL, "no-cache, no-store, must-revalidate"),
            (header::PRAGMA, "no-cache"),
        ],
        Json(serde_json::json!(profiles)),
    )
        .into_response()
}

/// POST /api/v1/profiles
pub async fn create_profile(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<CreateProfileRequest>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    // Check count
    let count: i64 =
        match sqlx::query_scalar("SELECT COUNT(*) FROM user_profiles WHERE user_id = $1")
            .bind(user_id)
            .fetch_one(&state.pool)
            .await
        {
            Ok(c) => c,
            Err(e) => {
                tracing::error!("create_profile count error: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        };

    if count >= 5 {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"detail": "Maximum of 5 profiles allowed"})),
        )
            .into_response();
    }

    let should_set_default = body.is_default || count == 0;

    // Split config into clean + encrypted secrets
    let config_val = if body.config.is_null() {
        Value::Object(serde_json::Map::new())
    } else {
        body.config
    };

    if let Some(resp) = validate_profile_config(&state, &headers, &config_val).await {
        return resp;
    }

    let (clean_config, encrypted_secrets) = split_config(&config_val, &state.config.secret_key);

    // Transaction: unset old default if needed, then insert
    let mut tx = match state.pool.begin().await {
        Ok(t) => t,
        Err(e) => {
            tracing::error!("create_profile begin tx: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    if should_set_default {
        if let Err(e) =
            sqlx::query("UPDATE user_profiles SET is_default = false WHERE user_id = $1")
                .bind(user_id)
                .execute(&mut *tx)
                .await
        {
            tracing::error!("create_profile unset default: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    }

    let profile_uuid = uuid::Uuid::new_v4().to_string();
    let row: (i32, String, i32, String, serde_json::Value, Option<String>, bool, DateTime<Utc>) =
        match sqlx::query_as(
            r#"INSERT INTO user_profiles (user_id, name, config, encrypted_secrets, is_default, uuid, created_at)
               VALUES ($1, $2, $3, $4, $5, $6, NOW())
               RETURNING id, uuid, user_id, name, config, encrypted_secrets, is_default, created_at"#,
        )
        .bind(user_id)
        .bind(&body.name)
        .bind(&clean_config)
        .bind(&encrypted_secrets)
        .bind(should_set_default)
        .bind(&profile_uuid)
        .fetch_one(&mut *tx)
        .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("create_profile insert: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        };

    if let Err(e) = tx.commit().await {
        tracing::error!("create_profile commit: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    let profile = ProfileRow {
        id: row.0,
        uuid: row.1,
        user_id: row.2,
        name: row.3,
        config: row.4,
        encrypted_secrets: row.5,
        is_default: row.6,
        created_at: row.7,
    };

    (
        StatusCode::CREATED,
        Json(build_profile_response(
            &profile,
            &state.config.secret_key,
            false,
        )),
    )
        .into_response()
}

/// GET /api/v1/profiles/{id}
pub async fn get_profile(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(id): Path<i32>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let profile = match fetch_profile_by_id(&state.pool_ro, id, user_id).await {
        Ok(Some(p)) => p,
        Ok(None) => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({"detail": "Profile not found"})),
            )
                .into_response();
        }
        Err(e) => {
            tracing::error!("get_profile db error: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    (
        StatusCode::OK,
        [
            (header::CACHE_CONTROL, "no-cache, no-store, must-revalidate"),
            (header::PRAGMA, "no-cache"),
        ],
        Json(build_profile_response(
            &profile,
            &state.config.secret_key,
            false,
        )),
    )
        .into_response()
}

/// PUT /api/v1/profiles/{id}
pub async fn update_profile(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(id): Path<i32>,
    Json(body): Json<UpdateProfileRequest>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(uid) => uid,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let profile = match fetch_profile_by_id(&state.pool, id, user_id).await {
        Ok(Some(p)) => p,
        Ok(None) => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({"detail": "Profile not found"})),
            )
                .into_response();
        }
        Err(e) => {
            tracing::error!("update_profile fetch: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    let name = body.name.as_deref().unwrap_or(&profile.name).to_string();

    // Restore masked sentinel values from existing full config, then use new_cfg as-is.
    // Do NOT deep_merge with existing_full as base — that would preserve removed providers.
    let merged_config = if let Some(new_cfg) = body.config {
        let existing_full = get_full_config(&profile, &state.config.secret_key);
        let mut new_cfg = new_cfg;
        restore_masked(&mut new_cfg, &existing_full);
        new_cfg
    } else {
        get_full_config(&profile, &state.config.secret_key)
    };

    if let Some(resp) = validate_profile_config(&state, &headers, &merged_config).await {
        return resp;
    }

    let (clean_config, encrypted_secrets) = split_config(&merged_config, &state.config.secret_key);

    let mut tx = match state.pool.begin().await {
        Ok(t) => t,
        Err(e) => {
            tracing::error!("update_profile begin tx: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    // Handle is_default change
    if let Some(true) = body.is_default {
        if let Err(e) =
            sqlx::query("UPDATE user_profiles SET is_default = false WHERE user_id = $1")
                .bind(user_id)
                .execute(&mut *tx)
                .await
        {
            tracing::error!("update_profile unset default: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    }

    let new_is_default = body.is_default.unwrap_or(profile.is_default);

    let row: (i32, String, i32, String, serde_json::Value, Option<String>, bool, DateTime<Utc>) =
        match sqlx::query_as(
            r#"UPDATE user_profiles
               SET name = $1, config = $2, encrypted_secrets = $3, is_default = $4, updated_at = NOW()
               WHERE id = $5 AND user_id = $6
               RETURNING id, uuid, user_id, name, config, encrypted_secrets, is_default, created_at"#,
        )
        .bind(&name)
        .bind(&clean_config)
        .bind(&encrypted_secrets)
        .bind(new_is_default)
        .bind(id)
        .bind(user_id)
        .fetch_one(&mut *tx)
        .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("update_profile update: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        };

    if let Err(e) = tx.commit().await {
        tracing::error!("update_profile commit: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    let updated_profile = ProfileRow {
        id: row.0,
        uuid: row.1.clone(),
        user_id: row.2,
        name: row.3,
        config: row.4,
        encrypted_secrets: row.5,
        is_default: row.6,
        created_at: row.7,
    };

    // Invalidate the Redis profile cache so stream lookups pick up the new config.
    let cache_key = format!("user_profile:{}", row.1);
    if let Err(e) = fred::prelude::KeysInterface::del::<(), _>(&state.redis, &cache_key).await {
        tracing::warn!("update_profile redis invalidate {cache_key}: {e}");
    }

    Json(build_profile_response(
        &updated_profile,
        &state.config.secret_key,
        false,
    ))
    .into_response()
}

/// DELETE /api/v1/profiles/{id}
pub async fn delete_profile(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(id): Path<i32>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(uid) => uid,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let profile = match fetch_profile_by_id(&state.pool, id, user_id).await {
        Ok(Some(p)) => p,
        Ok(None) => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({"detail": "Profile not found"})),
            )
                .into_response();
        }
        Err(e) => {
            tracing::error!("delete_profile fetch: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    // Can't delete last profile
    let count: i64 =
        match sqlx::query_scalar("SELECT COUNT(*) FROM user_profiles WHERE user_id = $1")
            .bind(user_id)
            .fetch_one(&state.pool)
            .await
        {
            Ok(c) => c,
            Err(e) => {
                tracing::error!("delete_profile count: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        };

    if count <= 1 {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"detail": "Cannot delete the last profile"})),
        )
            .into_response();
    }

    let mut tx = match state.pool.begin().await {
        Ok(t) => t,
        Err(e) => {
            tracing::error!("delete_profile begin tx: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    // If deleting the default, promote the oldest remaining profile.
    if profile.is_default {
        if let Err(e) = sqlx::query(
            r#"UPDATE user_profiles SET is_default = true
               WHERE id = (
                   SELECT id FROM user_profiles
                   WHERE user_id = $1 AND id != $2
                   ORDER BY created_at ASC
                   LIMIT 1
               )"#,
        )
        .bind(user_id)
        .bind(id)
        .execute(&mut *tx)
        .await
        {
            tracing::error!("delete_profile promote default: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    }

    if let Err(e) = sqlx::query("DELETE FROM user_profiles WHERE id = $1 AND user_id = $2")
        .bind(id)
        .bind(user_id)
        .execute(&mut *tx)
        .await
    {
        tracing::error!("delete_profile delete: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    if let Err(e) = tx.commit().await {
        tracing::error!("delete_profile commit: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    StatusCode::NO_CONTENT.into_response()
}

/// POST /api/v1/profiles/{id}/set-default
pub async fn set_default(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(id): Path<i32>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(uid) => uid,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let mut tx = match state.pool.begin().await {
        Ok(t) => t,
        Err(e) => {
            tracing::error!("set_default begin tx: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    if let Err(e) = sqlx::query("UPDATE user_profiles SET is_default = false WHERE user_id = $1")
        .bind(user_id)
        .execute(&mut *tx)
        .await
    {
        tracing::error!("set_default unset: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    let updated =
        sqlx::query("UPDATE user_profiles SET is_default = true WHERE id = $1 AND user_id = $2")
            .bind(id)
            .bind(user_id)
            .execute(&mut *tx)
            .await;

    match updated {
        Ok(r) if r.rows_affected() == 0 => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({"detail": "Profile not found"})),
            )
                .into_response();
        }
        Err(e) => {
            tracing::error!("set_default update: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
        _ => {}
    }

    if let Err(e) = tx.commit().await {
        tracing::error!("set_default commit: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    Json(serde_json::json!({"message": "Default profile updated"})).into_response()
}

/// POST /api/v1/profiles/{id}/reset-uuid
pub async fn reset_uuid(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(id): Path<i32>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(uid) => uid,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let new_uuid: Option<String> = match sqlx::query_scalar(
        r#"UPDATE user_profiles
           SET uuid = gen_random_uuid()
           WHERE id = $1 AND user_id = $2
           RETURNING uuid"#,
    )
    .bind(id)
    .bind(user_id)
    .fetch_optional(&state.pool)
    .await
    {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("reset_uuid db error: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    match new_uuid {
        Some(uuid) => Json(serde_json::json!({"profile_id": id, "new_uuid": uuid})).into_response(),
        None => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"detail": "Profile not found"})),
        )
            .into_response(),
    }
}

/// GET /api/v1/profiles/{id}/manifest-url
pub async fn manifest_url(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(id): Path<i32>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(uid) => uid,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let profile = match fetch_profile_by_id(&state.pool_ro, id, user_id).await {
        Ok(Some(p)) => p,
        Ok(None) => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({"detail": "Profile not found"})),
            )
                .into_response();
        }
        Err(e) => {
            tracing::error!("manifest_url db error: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    let secret = format!("U-{}", profile.uuid);
    let host = &state.config.host_url;
    let manifest_url = format!("{host}/{secret}/manifest.json");

    // Strip protocol for stremio:// URL
    let host_no_protocol = host
        .strip_prefix("https://")
        .or_else(|| host.strip_prefix("http://"))
        .unwrap_or(host);
    let stremio_install_url = format!("stremio://{host_no_protocol}/{secret}/manifest.json");
    let kodi_addon_url = format!("{host}/kodi/{secret}/addon.xml");

    Json(serde_json::json!({
        "profile_id": profile.id,
        "profile_uuid": profile.uuid,
        "profile_name": profile.name,
        "manifest_url": manifest_url,
        "stremio_install_url": stremio_install_url,
        "kodi_addon_url": kodi_addon_url,
    }))
    .into_response()
}

/// GET /api/v1/profiles/{id}/kodi-addon
pub async fn kodi_addon(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(id): Path<i32>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(uid) => uid,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let profile = match fetch_profile_by_id(&state.pool_ro, id, user_id).await {
        Ok(Some(p)) => p,
        Ok(None) => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({"detail": "Profile not found"})),
            )
                .into_response();
        }
        Err(e) => {
            tracing::error!("kodi_addon db error: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    let secret = format!("U-{}", profile.uuid);
    let host = &state.config.host_url;
    let manifest_url = format!("{host}/{secret}/manifest.json");

    let xml = format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<addon id="plugin.video.mediafusion.{profile_id}"
       name="MediaFusion - {profile_name}"
       version="1.0.0"
       provider-name="MediaFusion">
    <requires>
        <import addon="xbmc.python" version="3.0.0"/>
    </requires>
    <extension point="xbmc.addon.metadata">
        <summary>MediaFusion Stremio Addon for Kodi - {profile_name}</summary>
        <description>Stream content via MediaFusion using profile: {profile_name}</description>
        <platform>all</platform>
    </extension>
    <extension point="kodi.addon.metadata">
        <manifest_url>{manifest_url}</manifest_url>
        <profile_id>{profile_id}</profile_id>
        <profile_uuid>{profile_uuid}</profile_uuid>
    </extension>
</addon>"#,
        profile_id = profile.id,
        profile_name = xml_escape(&profile.name),
        manifest_url = xml_escape(&manifest_url),
        profile_uuid = profile.uuid,
    );

    (
        StatusCode::OK,
        [
            (header::CONTENT_TYPE, "application/xml"),
            (
                header::CONTENT_DISPOSITION,
                "attachment; filename=\"addon.xml\"",
            ),
        ],
        xml,
    )
        .into_response()
}

fn xml_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}
