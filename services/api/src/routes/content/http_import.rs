/// HTTP stream URL import endpoints.
///
/// Routes (prefix /api/v1/import):
///   GET  /http/extractors   → get_mediaflow_extractors
///   POST /http/analyze      → analyze_http_url
///   POST /http              → import_http_stream
use std::sync::Arc;

use axum::{
    extract::State,
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::Utc;
use hmac::{Hmac, Mac};
use serde::Deserialize;
use serde_json::json;
use sha2::Sha256;

use super::import_helpers::{
    award_contribution_points, create_contribution_record, enforce_upload_permissions,
    fetch_user_info, is_adult_content, notify_pending_contribution, resolve_uploader_identity,
};
use crate::state::AppState;

// ─── Auth ─────────────────────────────────────────────────────────────────────

fn validate_token(headers: &HeaderMap, secret_key: &str) -> Option<i64> {
    let token = headers
        .get("authorization")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "))
        .map(str::to_string)?;
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

// ─── Helpers ──────────────────────────────────────────────────────────────────

fn detect_stream_format(url: &str) -> Option<&'static str> {
    let lower = url.to_lowercase();
    if lower.contains(".m3u8") {
        Some("hls")
    } else if lower.contains(".mpd") {
        Some("dash")
    } else if lower.contains(".mp4") {
        Some("mp4")
    } else if lower.contains(".mkv") {
        Some("mkv")
    } else if lower.contains(".webm") {
        Some("webm")
    } else if lower.contains(".avi") {
        Some("avi")
    } else if lower.contains(".flv") {
        Some("flv")
    } else {
        None
    }
}

static MEDIAFLOW_EXTRACTORS: &[&str] = &[
    "doodstream",
    "filelions",
    "filemoon",
    "f16px",
    "mixdrop",
    "uqload",
    "streamtape",
    "streamwish",
    "supervideo",
    "vixcloud",
    "okru",
    "maxstream",
    "lulustream",
    "fastream",
    "turbovidplay",
    "vidmoly",
    "vidoza",
    "voe",
    "sportsonline",
];

fn detect_extractor(url: &str) -> Option<&'static str> {
    let lower = url.to_lowercase();
    MEDIAFLOW_EXTRACTORS
        .iter()
        .find(|&&name| lower.contains(name))
        .copied()
        .map(|v| v as _)
}

// ─── Request structs ──────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct AnalyzeHttpRequest {
    pub url: String,
}

#[derive(Deserialize)]
pub struct ImportHttpRequest {
    pub url: String,
    pub name: Option<String>,
    pub meta_id: Option<String>,
    pub meta_type: Option<String>,
    pub title: Option<String>,
    #[serde(default = "default_true")]
    pub is_public: bool,
    pub resolution: Option<String>,
    pub quality: Option<String>,
    pub codec: Option<String>,
    pub behavior_hints: Option<serde_json::Value>,
    pub drm_key_id: Option<String>,
    pub drm_key: Option<String>,
    pub extractor_name: Option<String>,
    pub is_anonymous: Option<bool>,
    pub anonymous_display_name: Option<String>,
}

fn default_true() -> bool {
    true
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// GET /api/v1/import/http/extractors
pub async fn get_mediaflow_extractors(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }
    let names: Vec<&str> = MEDIAFLOW_EXTRACTORS.to_vec();
    Json(json!(names)).into_response()
}

/// POST /api/v1/import/http/analyze
pub async fn analyze_http_url(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<AnalyzeHttpRequest>,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }

    let url = body.url.trim();
    if url.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "url is required"})),
        )
            .into_response();
    }

    let format = detect_stream_format(url);
    let extractor = detect_extractor(url);

    // Parse domain from URL for display
    let domain = url
        .trim_start_matches("https://")
        .trim_start_matches("http://")
        .split('/')
        .next()
        .unwrap_or("");

    Json(json!({
        "url": url,
        "domain": domain,
        "format": format,
        "extractor_name": extractor,
    }))
    .into_response()
}

/// POST /api/v1/import/http
pub async fn import_http_stream(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<ImportHttpRequest>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let user = match fetch_user_info(&state.pool_ro, user_id).await {
        Some(u) => u,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "User not found"})),
            )
                .into_response();
        }
    };

    if let Err((status, msg)) = enforce_upload_permissions(
        &state.pool,
        &state.redis,
        user_id,
        user.uploads_restricted,
        &user.role,
    )
    .await
    {
        return (status, Json(json!({"detail": msg}))).into_response();
    }

    let url = body.url.trim().to_string();
    if url.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "url is required"})),
        )
            .into_response();
    }

    let stream_name = body
        .name
        .as_deref()
        .filter(|s| !s.is_empty())
        .or(body.title.as_deref())
        .unwrap_or("HTTP Stream")
        .to_string();

    // Adult content check
    if is_adult_content(&stream_name) {
        return (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({"detail": "Adult content is not allowed."})),
        )
            .into_response();
    }

    let resolved_is_anonymous = body.is_anonymous.unwrap_or(user.contribute_anonymously);
    let (uploader_name, uploader_user_id) = resolve_uploader_identity(
        resolved_is_anonymous,
        body.anonymous_display_name.as_deref(),
        &user.username,
        user_id,
    );
    let is_privileged = matches!(user.role.as_str(), "moderator" | "admin");
    let auto_approve = is_privileged || !resolved_is_anonymous;
    let is_public = auto_approve && body.is_public;

    let format = body
        .extractor_name
        .as_deref()
        .map(|_| None::<&str>)
        .unwrap_or_else(|| detect_stream_format(&url));

    // Resolve media_id
    let media_id: Option<i64> = if let Some(ref meta_id) = body.meta_id {
        if !meta_id.is_empty() {
            sqlx::query_scalar(
                "SELECT m.id FROM media m JOIN media_external_id meid ON m.id = meid.media_id WHERE meid.external_id = $1 LIMIT 1",
            )
            .bind(meta_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None)
        } else {
            None
        }
    } else {
        None
    };

    // Fallback: create media record if we have a title and type but no match
    let media_id: Option<i64> = if media_id.is_none() {
        if let Some(ref title) = body.title {
            if !title.is_empty() {
                let meta_type = body.meta_type.as_deref().unwrap_or("movie");
                let db_type = match meta_type {
                    "series" => "SERIES",
                    "tv" => "TV",
                    _ => "MOVIE",
                };
                sqlx::query_scalar(
                    "INSERT INTO media (title, type, created_at) VALUES ($1, $2::mediatype, NOW()) RETURNING id",
                )
                .bind(title)
                .bind(db_type)
                .fetch_optional(&state.pool)
                .await
                .unwrap_or(None)
            } else {
                None
            }
        } else {
            None
        }
    } else {
        media_id
    };

    // Check for duplicate
    if let Some(mid) = media_id {
        let existing: Option<i64> = sqlx::query_scalar(
            "SELECT hs.stream_id FROM http_stream hs JOIN stream_media_link sml ON sml.stream_id = hs.stream_id WHERE hs.url = $1 AND sml.media_id = $2 LIMIT 1",
        )
        .bind(&url)
        .bind(mid)
        .fetch_optional(&state.pool)
        .await
        .unwrap_or(None);

        if let Some(existing_id) = existing {
            return (
                StatusCode::CONFLICT,
                Json(json!({"detail": "Stream already exists", "stream_id": existing_id})),
            )
                .into_response();
        }
    }

    // Insert stream
    let stream_id: i64 = match sqlx::query_scalar(
        r#"INSERT INTO stream (stream_type, name, source, uploader, uploader_user_id, is_active, is_blocked, is_public, playback_count, is_remastered, is_upscaled, is_proper, is_repack, is_extended, is_complete, is_dubbed, is_subbed, created_at, resolution, quality, codec)
           VALUES ('HTTP', $1, 'user_import', $2, $3, true, false, $4, 0, false, false, false, false, false, false, false, false, NOW(), $5, $6, $7)
           RETURNING id"#,
    )
    .bind(&stream_name)
    .bind(&uploader_name)
    .bind(uploader_user_id)
    .bind(is_public)
    .bind(&body.resolution)
    .bind(&body.quality)
    .bind(&body.codec)
    .fetch_one(&state.pool)
    .await
    {
        Ok(id) => id,
        Err(e) => {
            tracing::error!("import_http_stream insert stream: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    // Insert http_stream
    let behavior_hints_json = body.behavior_hints.as_ref().map(|v| v.to_string());
    if let Err(e) = sqlx::query(
        "INSERT INTO http_stream (stream_id, url, format, behavior_hints, drm_key_id, drm_key, extractor_name) VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)",
    )
    .bind(stream_id)
    .bind(&url)
    .bind(format)
    .bind(behavior_hints_json.as_deref())
    .bind(&body.drm_key_id)
    .bind(&body.drm_key)
    .bind(&body.extractor_name)
    .execute(&state.pool)
    .await
    {
        tracing::error!("import_http_stream insert http_stream: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    // Link to media
    if let Some(mid) = media_id {
        let _ = sqlx::query(
            "INSERT INTO stream_media_link (stream_id, media_id, is_primary, is_verified, created_at) VALUES ($1, $2, true, true, NOW()) ON CONFLICT DO NOTHING",
        )
        .bind(stream_id)
        .bind(mid)
        .execute(&state.pool)
        .await;

        let _ = sqlx::query("UPDATE media SET total_streams = total_streams + 1 WHERE id = $1")
            .bind(mid)
            .execute(&state.pool)
            .await;
    }

    let data = serde_json::json!({
        "name": stream_name,
        "url": url,
        "meta_type": body.meta_type.as_deref().unwrap_or("movie"),
        "uploader_name": uploader_name,
        "is_anonymous": resolved_is_anonymous,
        "is_public": is_public,
    });

    let mut contrib_id: Option<String> = None;
    if let Ok(cid) = create_contribution_record(
        &state.pool,
        uploader_user_id,
        "http",
        Some(&stream_id.to_string()),
        &data,
        auto_approve,
        is_privileged,
    )
    .await
    {
        if auto_approve {
            if let Some(uid) = uploader_user_id {
                award_contribution_points(&state.pool, uid).await;
            }
        } else if let (Some(bot_token), Some(chat_id)) = (
            state.config.telegram_bot_token.as_deref(),
            state.config.telegram_chat_id.as_deref(),
        ) {
            notify_pending_contribution(
                &state.http,
                bot_token,
                chat_id,
                &state.config.host_url,
                "http",
                &uploader_name,
                &data,
            )
            .await;
        }
        contrib_id = Some(cid);
    }

    (
        StatusCode::CREATED,
        Json(json!({
            "stream_id": stream_id,
            "url": url,
            "format": format,
            "name": stream_name,
            "media_id": media_id,
            "contribution_id": contrib_id,
            "auto_approved": auto_approve,
        })),
    )
        .into_response()
}
