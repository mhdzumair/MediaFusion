/// YouTube channel/video import endpoints.
///
/// Routes (prefix /api/v1/import):
///   POST /youtube/analyze  → analyze_youtube_url
///   POST /youtube          → import_youtube_video
use std::sync::{Arc, OnceLock};

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

fn extract_video_id(url: &str) -> Option<String> {
    static RE_WATCH: OnceLock<regex::Regex> = OnceLock::new();
    static RE_SHORT: OnceLock<regex::Regex> = OnceLock::new();
    static RE_EMBED: OnceLock<regex::Regex> = OnceLock::new();
    static RE_SHORTS: OnceLock<regex::Regex> = OnceLock::new();

    let re_watch = RE_WATCH.get_or_init(|| {
        regex::Regex::new(r"youtube\.com/watch\?(?:[^&]*&)*v=([a-zA-Z0-9_-]{11})").unwrap()
    });
    let re_short =
        RE_SHORT.get_or_init(|| regex::Regex::new(r"youtu\.be/([a-zA-Z0-9_-]{11})").unwrap());
    let re_embed = RE_EMBED
        .get_or_init(|| regex::Regex::new(r"youtube\.com/embed/([a-zA-Z0-9_-]{11})").unwrap());
    let re_shorts = RE_SHORTS
        .get_or_init(|| regex::Regex::new(r"youtube\.com/shorts/([a-zA-Z0-9_-]{11})").unwrap());

    for re in [re_watch, re_short, re_embed, re_shorts] {
        if let Some(caps) = re.captures(url) {
            if let Some(m) = caps.get(1) {
                return Some(m.as_str().to_string());
            }
        }
    }
    None
}

async fn fetch_oembed(http: &reqwest::Client, video_id: &str) -> Option<(String, String)> {
    let oembed_url = format!(
        "https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={}&format=json",
        video_id
    );
    let resp = http.get(&oembed_url).send().await.ok()?;
    if !resp.status().is_success() {
        return None;
    }
    let data: serde_json::Value = resp.json().await.ok()?;
    let title = data["title"].as_str()?.to_string();
    let channel = data["author_name"].as_str().unwrap_or("").to_string();
    Some((title, channel))
}

// ─── Request structs ──────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct AnalyzeYouTubeRequest {
    pub url: String,
}

#[derive(Deserialize)]
pub struct ImportYouTubeRequest {
    pub url: String,
    pub name: Option<String>,
    pub meta_id: Option<String>,
    pub meta_type: Option<String>,
    pub title: Option<String>,
    #[serde(default = "default_true")]
    pub is_public: bool,
    pub channel_id: Option<String>,
    pub channel_name: Option<String>,
    pub duration_seconds: Option<i64>,
    #[serde(default)]
    pub is_live: bool,
    pub is_anonymous: Option<bool>,
    pub anonymous_display_name: Option<String>,
}

fn default_true() -> bool {
    true
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// POST /api/v1/import/youtube/analyze
pub async fn analyze_youtube_url(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<AnalyzeYouTubeRequest>,
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

    let video_id = match extract_video_id(url) {
        Some(id) => id,
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Could not extract YouTube video ID from URL"})),
            )
                .into_response();
        }
    };

    // Check if already imported
    let already_exists: bool =
        sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM youtube_stream WHERE video_id = $1)")
            .bind(&video_id)
            .fetch_one(&state.pool)
            .await
            .unwrap_or(false);

    // Fetch oEmbed metadata
    let (title, channel_name) = fetch_oembed(&state.http, &video_id)
        .await
        .unwrap_or_else(|| (String::new(), String::new()));

    Json(json!({
        "video_id": video_id,
        "url": format!("https://www.youtube.com/watch?v={}", video_id),
        "title": title,
        "channel_name": channel_name,
        "already_exists": already_exists,
    }))
    .into_response()
}

/// POST /api/v1/import/youtube
pub async fn import_youtube_video(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<ImportYouTubeRequest>,
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

    let url = body.url.trim().to_string();
    if url.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "url is required"})),
        )
            .into_response();
    }

    let video_id = match extract_video_id(&url) {
        Some(id) => id,
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Could not extract YouTube video ID from URL"})),
            )
                .into_response();
        }
    };

    // Check for duplicate
    let existing: Option<i64> =
        sqlx::query_scalar("SELECT stream_id FROM youtube_stream WHERE video_id = $1 LIMIT 1")
            .bind(&video_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);

    if let Some(existing_id) = existing {
        return (
            StatusCode::CONFLICT,
            Json(json!({"detail": "YouTube video already imported", "stream_id": existing_id})),
        )
            .into_response();
    }

    // Fetch oEmbed for title/channel if not provided
    let (oembed_title, oembed_channel) = if body.name.is_none() || body.channel_name.is_none() {
        fetch_oembed(&state.http, &video_id)
            .await
            .unwrap_or_else(|| (String::new(), String::new()))
    } else {
        (String::new(), String::new())
    };

    let stream_name = body
        .name
        .as_deref()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| {
            if !oembed_title.is_empty() {
                &oembed_title
            } else {
                body.title.as_deref().unwrap_or("YouTube Video")
            }
        })
        .to_string();

    let channel_name = body
        .channel_name
        .as_deref()
        .filter(|s| !s.is_empty())
        .unwrap_or(&oembed_channel)
        .to_string();

    let (uploader_name, uploader_user_id) = resolve_uploader_identity(
        resolved_is_anonymous,
        body.anonymous_display_name.as_deref(),
        &user.username,
        user_id,
    );
    let is_public = auto_approve && body.is_public;

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
        let title_for_media = body.title.as_deref().unwrap_or(&stream_name);
        if !title_for_media.is_empty() {
            let meta_type = body.meta_type.as_deref().unwrap_or("movie");
            let db_type = match meta_type {
                "series" => "SERIES",
                "tv" => "TV",
                _ => "MOVIE",
            };
            sqlx::query_scalar(
                "INSERT INTO media (title, type, created_at) VALUES ($1, $2::mediatype, NOW()) RETURNING id",
            )
            .bind(title_for_media)
            .bind(db_type)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None)
        } else {
            None
        }
    } else {
        media_id
    };

    // Insert stream
    let stream_id: i64 = match sqlx::query_scalar(
        r#"INSERT INTO stream (stream_type, name, source, uploader, uploader_user_id, is_active, is_blocked, is_public, playback_count, is_remastered, is_upscaled, is_proper, is_repack, is_extended, is_complete, is_dubbed, is_subbed, created_at, resolution, quality, codec)
           VALUES ('YOUTUBE', $1, 'youtube', $2, $3, true, false, $4, 0, false, false, false, false, false, false, false, false, NOW(), NULL, NULL, NULL)
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
            tracing::error!("import_youtube_video insert stream: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    // Insert youtube_stream
    if let Err(e) = sqlx::query(
        "INSERT INTO youtube_stream (stream_id, video_id, channel_id, channel_name, duration_seconds, is_live, is_premiere) VALUES ($1, $2, $3, $4, $5, $6, false)",
    )
    .bind(stream_id)
    .bind(&video_id)
    .bind(&body.channel_id)
    .bind(&channel_name)
    .bind(body.duration_seconds)
    .bind(body.is_live)
    .execute(&state.pool)
    .await
    {
        tracing::error!("import_youtube_video insert youtube_stream: {e}");
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
        "video_id": video_id,
        "channel_name": channel_name,
        "meta_type": body.meta_type.as_deref().unwrap_or("movie"),
        "uploader_name": uploader_name,
        "is_anonymous": resolved_is_anonymous,
        "is_public": is_public,
    });

    let mut contrib_id: Option<String> = None;
    if let Ok(cid) = create_contribution_record(
        &state.pool,
        uploader_user_id,
        "youtube",
        Some(&video_id),
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
                "youtube",
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
            "video_id": video_id,
            "name": stream_name,
            "channel_name": channel_name,
            "media_id": media_id,
            "contribution_id": contrib_id,
            "auto_approved": auto_approve,
        })),
    )
        .into_response()
}
