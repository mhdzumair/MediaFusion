/// AceStream hash import endpoints.
///
/// Routes (prefix /api/v1/import):
///   POST /acestream/analyze  → analyze_acestream
///   POST /acestream          → import_acestream
use std::sync::Arc;

use axum::{
    extract::State,
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::Utc;
use hmac::{Hmac, KeyInit, Mac};
use serde::Deserialize;
use serde_json::json;
use sha2::Sha256;

use super::import_helpers::{
    award_contribution_points, create_contribution_record, enforce_upload_permissions,
    fetch_user_info, notify_pending_contribution, resolve_uploader_identity,
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

fn validate_hex_40(s: &str) -> bool {
    s.len() == 40 && s.chars().all(|c| c.is_ascii_hexdigit())
}

fn extract_acestream_id(input: &str) -> Option<String> {
    // Handle acestream:// URI scheme
    let candidate = if input.starts_with("acestream://") {
        input.trim_start_matches("acestream://")
    } else {
        input
    };
    let candidate = candidate.trim();
    if validate_hex_40(candidate) {
        Some(candidate.to_lowercase())
    } else {
        None
    }
}

// ─── Request structs ──────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct AnalyzeAcestreamRequest {
    /// content_id is the primary 40-char hex AceStream ID
    pub content_id: Option<String>,
    /// info_hash is an optional alternative 40-char hex
    pub info_hash: Option<String>,
    /// Convenience: raw acestream:// URI
    pub acestream_url: Option<String>,
}

#[derive(Deserialize)]
pub struct ImportAcestreamRequest {
    pub content_id: Option<String>,
    pub info_hash: Option<String>,
    pub acestream_url: Option<String>,
    pub name: Option<String>,
    pub meta_id: Option<String>,
    pub meta_type: Option<String>,
    pub title: Option<String>,
    #[serde(default = "default_true")]
    pub is_public: bool,
    pub is_anonymous: Option<bool>,
    pub anonymous_display_name: Option<String>,
}

fn default_true() -> bool {
    true
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// POST /api/v1/import/acestream/analyze
pub async fn analyze_acestream(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<AnalyzeAcestreamRequest>,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }

    // Resolve content_id from body fields
    let content_id_raw = body
        .content_id
        .as_deref()
        .filter(|s| !s.is_empty())
        .or_else(|| body.acestream_url.as_deref().filter(|s| !s.is_empty()));

    let content_id = match content_id_raw.and_then(extract_acestream_id) {
        Some(id) => id,
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "content_id must be a 40-character hex string or acestream:// URI"})),
            )
                .into_response();
        }
    };

    // Validate optional info_hash
    let info_hash = match &body.info_hash {
        Some(h) if !h.is_empty() => {
            if !validate_hex_40(h) {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(json!({"detail": "info_hash must be a 40-character hex string"})),
                )
                    .into_response();
            }
            Some(h.to_lowercase())
        }
        _ => None,
    };

    // Check existing by content_id
    let existing_by_cid: Option<i64> =
        sqlx::query_scalar("SELECT stream_id FROM acestream_stream WHERE content_id = $1 LIMIT 1")
            .bind(&content_id)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None);

    // Check existing by info_hash
    let existing_by_hash: Option<i64> = if let Some(ref h) = info_hash {
        sqlx::query_scalar("SELECT stream_id FROM acestream_stream WHERE info_hash = $1 LIMIT 1")
            .bind(h)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None)
    } else {
        None
    };

    let already_exists = existing_by_cid.is_some() || existing_by_hash.is_some();
    let existing_stream_id = existing_by_cid.or(existing_by_hash);

    Json(json!({
        "content_id": content_id,
        "info_hash": info_hash,
        "already_exists": already_exists,
        "existing_stream_id": existing_stream_id,
    }))
    .into_response()
}

/// POST /api/v1/import/acestream
pub async fn import_acestream(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<ImportAcestreamRequest>,
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

    let resolved_is_anonymous = body.is_anonymous.unwrap_or(user.contribute_anonymously);
    let is_privileged = matches!(user.role.as_str(), "moderator" | "admin");
    let auto_approve = is_privileged || !resolved_is_anonymous;
    let (uploader_name, uploader_user_id) = resolve_uploader_identity(
        resolved_is_anonymous,
        body.anonymous_display_name.as_deref(),
        &user.username,
        user_id,
    );
    let is_public = auto_approve && body.is_public;

    // Resolve content_id
    let content_id_raw = body
        .content_id
        .as_deref()
        .filter(|s| !s.is_empty())
        .or_else(|| body.acestream_url.as_deref().filter(|s| !s.is_empty()));

    let content_id = match content_id_raw.and_then(extract_acestream_id) {
        Some(id) => id,
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "content_id must be a 40-character hex string or acestream:// URI"})),
            )
                .into_response();
        }
    };

    // Validate optional info_hash
    let info_hash = match &body.info_hash {
        Some(h) if !h.is_empty() => {
            if !validate_hex_40(h) {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(json!({"detail": "info_hash must be a 40-character hex string"})),
                )
                    .into_response();
            }
            Some(h.to_lowercase())
        }
        _ => None,
    };

    // Check for duplicate
    let existing: Option<i64> =
        sqlx::query_scalar("SELECT stream_id FROM acestream_stream WHERE content_id = $1 LIMIT 1")
            .bind(&content_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);

    if let Some(existing_id) = existing {
        return (
            StatusCode::CONFLICT,
            Json(json!({"detail": "AceStream already imported", "stream_id": existing_id})),
        )
            .into_response();
    }

    // Check by info_hash too
    if let Some(ref h) = info_hash {
        let existing: Option<i64> = sqlx::query_scalar(
            "SELECT stream_id FROM acestream_stream WHERE info_hash = $1 LIMIT 1",
        )
        .bind(h)
        .fetch_optional(&state.pool)
        .await
        .unwrap_or(None);

        if let Some(existing_id) = existing {
            return (
                StatusCode::CONFLICT,
                Json(
                    json!({"detail": "AceStream already imported (by info_hash)", "stream_id": existing_id}),
                ),
            )
                .into_response();
        }
    }

    let stream_name = body
        .name
        .as_deref()
        .filter(|s| !s.is_empty())
        .or(body.title.as_deref())
        .unwrap_or("AceStream")
        .to_string();

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

    // Fallback: create media
    let media_id: Option<i64> = if media_id.is_none() {
        if let Some(ref title) = body.title {
            if !title.is_empty() {
                let meta_type = body.meta_type.as_deref().unwrap_or("tv");
                let db_type = match meta_type {
                    "movie" => "MOVIE",
                    "series" => "SERIES",
                    _ => "TV",
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

    // Insert stream
    let stream_id: i64 = match sqlx::query_scalar(
        r#"INSERT INTO stream (stream_type, name, source, uploader, uploader_user_id, is_active, is_blocked, is_public, playback_count, is_remastered, is_upscaled, is_proper, is_repack, is_extended, is_complete, is_dubbed, is_subbed, created_at, resolution, quality, codec)
           VALUES ('ACESTREAM', $1, 'user_import', $2, $3, true, false, $4, 0, false, false, false, false, false, false, false, false, NOW(), NULL, NULL, NULL)
           RETURNING id"#,
    )
    .bind(&stream_name)
    .bind(&uploader_name)
    .bind(uploader_user_id)
    .bind(is_public)
    .fetch_one(&state.pool)
    .await
    {
        Ok(id) => id,
        Err(e) => {
            tracing::error!("import_acestream insert stream: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    // Insert acestream_stream
    if let Err(e) = sqlx::query(
        "INSERT INTO acestream_stream (stream_id, content_id, info_hash) VALUES ($1, $2, $3)",
    )
    .bind(stream_id)
    .bind(&content_id)
    .bind(&info_hash)
    .execute(&state.pool)
    .await
    {
        tracing::error!("import_acestream insert acestream_stream: {e}");
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
        "content_id": content_id,
        "info_hash": info_hash,
        "meta_type": body.meta_type.as_deref().unwrap_or("tv"),
        "uploader_name": uploader_name,
        "is_anonymous": resolved_is_anonymous,
        "is_public": is_public,
    });

    let mut contrib_id: Option<String> = None;
    if let Ok(cid) = create_contribution_record(
        &state.pool,
        uploader_user_id,
        "acestream",
        Some(&content_id),
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
                "acestream",
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
            "content_id": content_id,
            "info_hash": info_hash,
            "name": stream_name,
            "media_id": media_id,
            "contribution_id": contrib_id,
            "auto_approved": auto_approve,
        })),
    )
        .into_response()
}
