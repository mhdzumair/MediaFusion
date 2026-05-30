/// Watchlist endpoints.
///
/// Routes:
///   GET /api/v1/watchlist/providers         → get_providers
///   GET /api/v1/watchlist/{provider}        → get_watchlist
///   GET /streaming_provider/{secret_str}/delete_all_watchlist → delete_all_handler
use std::sync::Arc;

use axum::{
    body::Body,
    extract::{Path, Query, State},
    http::{header, HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use serde::Deserialize;
use serde_json::json;

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::Utc;
use hmac::{Hmac, KeyInit, Mac};
use sha2::Sha256;

use crate::{crypto, models::user_data::UserData, providers, state::AppState};

// ─── Providers that support watchlist (cached hash lookup) ───────────────────

const WATCHLIST_PROVIDERS: &[&str] = &[
    "realdebrid",
    "alldebrid",
    "premiumize",
    "debridlink",
    "torbox",
    "stremthru",
    "offcloud",
    "easydebrid",
    "seedr",
    "pikpak",
];

fn validate_token(headers: &HeaderMap, secret_key: &str) -> Option<i64> {
    let token = headers
        .get("authorization")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "))?;
    let dot = token.rfind('.')?;
    let (payload_str, sig) = token.split_at(dot);
    let sig = &sig[1..];
    let mut mac = Hmac::<Sha256>::new_from_slice(secret_key.as_bytes()).ok()?;
    mac.update(payload_str.as_bytes());
    let expected: String = mac
        .finalize()
        .into_bytes()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    if expected != sig {
        return None;
    }
    let decoded = URL_SAFE_NO_PAD.decode(payload_str).ok()?;
    let data: serde_json::Value = serde_json::from_slice(&decoded).ok()?;
    let exp = data["exp"].as_f64()?;
    if exp < Utc::now().timestamp() as f64 {
        return None;
    }
    if data["type"].as_str() != Some("access") {
        return None;
    }
    data["sub"].as_str()?.parse().ok()
}

// ─── GET /api/v1/watchlist/providers ─────────────────────────────────────────

#[derive(Deserialize)]
pub struct ProvidersQuery {
    profile_id: Option<i32>,
}

pub async fn get_providers(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<ProvidersQuery>,
) -> Response {
    // Auth is optional — unauthenticated requests return an empty provider list.
    let user_id = validate_token(&headers, &state.config.secret_key_raw);

    // Fetch the profile config (with secrets decrypted), including the actual profile id.
    type ProfileRecord = (i32, Option<serde_json::Value>, Option<String>);
    let row: Option<ProfileRecord> = if let Some(uid) = user_id {
        if let Some(pid) = params.profile_id {
            sqlx::query_as::<_, ProfileRecord>(
                "SELECT id, config, encrypted_secrets FROM user_profiles WHERE id = $1 AND user_id = $2",
            )
            .bind(pid)
            .bind(uid as i32)
            .fetch_optional(&state.pool_ro)
            .await
            .ok()
            .flatten()
        } else {
            sqlx::query_as::<_, ProfileRecord>(
                "SELECT id, config, encrypted_secrets FROM user_profiles WHERE user_id = $1 AND is_default = true",
            )
            .bind(uid as i32)
            .fetch_optional(&state.pool_ro)
            .await
            .ok()
            .flatten()
        }
    } else {
        None
    };

    let (profile_id, config, encrypted_secrets) = match row {
        Some((id, cfg, enc)) => (id, cfg.unwrap_or(json!({})), enc),
        None => return Json(json!({"providers": [], "profile_id": 0})).into_response(),
    };

    // Decrypt secrets and merge
    let mut full_config = config.clone();
    if let Some(enc) = encrypted_secrets {
        let secrets = crate::crypto::profile::decrypt_secrets(&enc, &state.config.secret_key);
        crate::crypto::profile::merge_secrets(&mut full_config, &secrets);
    }

    let providers = extract_watchlist_providers(&full_config);
    Json(json!({"providers": providers, "profile_id": profile_id})).into_response()
}

fn get_str<'a>(obj: &'a serde_json::Value, keys: &[&str]) -> Option<&'a str> {
    keys.iter()
        .find_map(|k| obj.get(*k).and_then(|v| v.as_str()))
}

fn get_bool_default_true(obj: &serde_json::Value, keys: &[&str]) -> bool {
    keys.iter()
        .find_map(|k| obj.get(*k).and_then(|v| v.as_bool()))
        .unwrap_or(true)
}

fn extract_watchlist_providers(config: &serde_json::Value) -> Vec<serde_json::Value> {
    let mut result = Vec::new();

    // Check multi-provider array — supports both "streaming_providers" and "sps" aliases.
    let arr = config
        .get("sps")
        .or_else(|| config.get("streaming_providers"))
        .and_then(|v| v.as_array());

    if let Some(sps) = arr {
        for sp in sps {
            let service = match get_str(sp, &["sv", "service"]) {
                Some(s) if !s.is_empty() => s,
                _ => continue,
            };
            if !WATCHLIST_PROVIDERS.contains(&service) {
                continue;
            }
            // enabled defaults to true when key absent
            let enabled = get_bool_default_true(sp, &["en", "enabled"]);
            // ewc (enable_watchlist_catalogs) defaults to true when key absent
            let ewc = get_bool_default_true(sp, &["ewc", "enable_watchlist_catalogs"]);
            if enabled && ewc {
                let display_name =
                    get_str(sp, &["n", "name"]).unwrap_or_else(|| provider_display_name(service));
                result.push(json!({
                    "service": service,
                    "name": display_name,
                    "supports_watchlist": true,
                }));
            }
        }
    }

    // Legacy single provider — supports "streaming_provider" and "sp" aliases.
    if result.is_empty() {
        let sp = config
            .get("sp")
            .or_else(|| config.get("streaming_provider"));
        if let Some(sp) = sp {
            let service = match get_str(sp, &["sv", "service"]) {
                Some(s) if !s.is_empty() => s,
                _ => return result,
            };
            if WATCHLIST_PROVIDERS.contains(&service) {
                let display_name =
                    get_str(sp, &["n", "name"]).unwrap_or_else(|| provider_display_name(service));
                result.push(json!({
                    "service": service,
                    "name": display_name,
                    "supports_watchlist": true,
                }));
            }
        }
    }

    result
}

fn provider_display_name(service: &str) -> &str {
    match service {
        "realdebrid" => "Real-Debrid",
        "alldebrid" => "AllDebrid",
        "premiumize" => "Premiumize",
        "debridlink" => "Debrid-Link",
        "torbox" => "TorBox",
        "stremthru" => "StremThru",
        "offcloud" => "Offcloud",
        "easydebrid" => "EasyDebrid",
        other => other,
    }
}


// ─── GET /api/v1/watchlist/{provider} ────────────────────────────────────────

#[derive(Deserialize)]
pub struct WatchlistQuery {
    profile_id: Option<i32>,
    media_type: Option<String>,
    page: Option<u32>,
    page_size: Option<u32>,
}

pub async fn get_watchlist(
    Path(_provider): Path<String>,
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<WatchlistQuery>,
) -> Response {
    let _user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let page = params.page.unwrap_or(1).max(1);
    let page_size = params.page_size.unwrap_or(25).clamp(1, 100);
    let _media_type = params.media_type;
    let _ = params.profile_id;

    // Provider-specific watchlist fetching is not yet implemented.
    // Return empty paginated response so the UI doesn't break.
    Json(json!({
        "items": [],
        "total": 0,
        "page": page,
        "page_size": page_size,
        "has_more": false,
    }))
    .into_response()
}

pub async fn delete_all_handler(
    Path(secret_str): Path<String>,
    State(state): State<Arc<AppState>>,
) -> Response {
    match dispatch(&state, &secret_str).await {
        Ok(()) => redirect(format!(
            "{}/static/exceptions/watchlist_deleted.mp4",
            state.config.host_url
        )),
        Err(e) => {
            tracing::warn!("delete_all_watchlist: {e}");
            let video = e.video_file();
            redirect(error_video_url(&state, video))
        }
    }
}

async fn dispatch(state: &AppState, secret_str: &str) -> Result<(), providers::ProviderError> {
    let raw = crypto::resolve_user_data(
        secret_str,
        &state.config.secret_key,
        &state.pool,
        &state.redis,
    )
    .await;
    let user_data: UserData = serde_json::from_value(raw).unwrap_or_default();

    let provider = user_data.get_primary_provider().ok_or_else(|| {
        providers::ProviderError::api("No streaming provider configured", "api_error.mp4")
    })?;

    let token = provider.token.as_deref().ok_or_else(|| {
        providers::ProviderError::api("Provider token is missing", "invalid_token.mp4")
    })?;

    match provider.service.as_str() {
        "realdebrid" => {
            providers::torrents::realdebrid::delete_all_torrents(&state.http, token).await
        }
        "alldebrid" => {
            providers::torrents::alldebrid::delete_all_torrents(&state.http, token).await
        }
        "premiumize" => {
            providers::torrents::premiumize::delete_all_torrents(&state.http, token).await
        }
        "debridlink" => {
            providers::torrents::debridlink::delete_all_torrents(&state.http, token).await
        }
        "torbox" => providers::torrents::torbox::delete_all_torrents(&state.http, token).await,
        "stremthru" => {
            providers::torrents::stremthru::delete_all_torrents(&state.http, token).await
        }
        "offcloud" => providers::torrents::offcloud::delete_all_torrents(&state.http, token).await,
        "easydebrid" => {
            providers::torrents::easydebrid::delete_all_torrents(&state.http, token).await
        }
        other => Err(providers::ProviderError::api(
            format!("Provider '{other}' does not support delete-all-watchlist"),
            "provider_error.mp4",
        )),
    }
}

fn redirect(url: String) -> Response {
    Response::builder()
        .status(StatusCode::FOUND)
        .header(header::LOCATION, url)
        .header(header::CACHE_CONTROL, "no-store, no-cache, must-revalidate")
        .body(Body::empty())
        .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
}

fn error_video_url(state: &AppState, video_file: &str) -> String {
    format!("{}/static/exceptions/{video_file}", state.config.host_url)
}
