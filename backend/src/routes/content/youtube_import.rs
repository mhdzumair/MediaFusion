/// YouTube channel/video import endpoints.
///
/// Routes (prefix /api/v1/import):
///   POST /youtube/analyze  → analyze_youtube_url
///   POST /youtube          → import_youtube_video
use std::sync::{Arc, OnceLock};

use axum::{
    Json,
    extract::State,
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use chrono::Utc;
use hmac::{Hmac, KeyInit, Mac};
use serde::Deserialize;
use serde_json::json;
use sha2::Sha256;

use super::import_helpers::{
    award_contribution_points, create_contribution_record, enforce_upload_permissions,
    fetch_user_info, notify_pending_contribution, resolve_uploader_identity,
    should_auto_approve_import,
};
use crate::{db::UserId, state::AppState};

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
        if let Some(caps) = re.captures(url)
            && let Some(m) = caps.get(1) {
                return Some(m.as_str().to_string());
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

#[derive(Debug, Default, Clone)]
struct YouTubeFetchedMeta {
    title: Option<String>,
    channel_name: Option<String>,
    channel_id: Option<String>,
    duration_seconds: Option<i64>,
    is_live: bool,
    geo_restriction_type: Option<String>,
    geo_restriction_countries: Vec<String>,
}

fn parse_iso8601_duration(duration: &str) -> i64 {
    static RE: OnceLock<regex::Regex> = OnceLock::new();
    let re = RE.get_or_init(|| regex::Regex::new(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?").unwrap());
    let Some(caps) = re.captures(duration) else {
        return 0;
    };
    let hours: i64 = caps
        .get(1)
        .and_then(|m| m.as_str().parse().ok())
        .unwrap_or(0);
    let minutes: i64 = caps
        .get(2)
        .and_then(|m| m.as_str().parse().ok())
        .unwrap_or(0);
    let seconds: i64 = caps
        .get(3)
        .and_then(|m| m.as_str().parse().ok())
        .unwrap_or(0);
    hours * 3600 + minutes * 60 + seconds
}

fn normalize_country_list(countries: &[serde_json::Value]) -> Vec<String> {
    let mut seen = std::collections::HashSet::new();
    let mut out = Vec::new();
    for country in countries {
        let Some(raw) = country.as_str() else {
            continue;
        };
        let mut value = raw.trim().to_string();
        if value.is_empty() {
            continue;
        }
        if value.len() == 2 {
            value = value.to_uppercase();
        }
        if seen.insert(value.clone()) {
            out.push(value);
        }
    }
    out
}

async fn fetch_youtube_metadata(
    http: &reqwest::Client,
    video_id: &str,
    api_key: Option<&str>,
) -> YouTubeFetchedMeta {
    let mut meta = YouTubeFetchedMeta::default();

    if let Some(key) = api_key.filter(|k| !k.is_empty()) {
        let url = "https://www.googleapis.com/youtube/v3/videos";
        if let Ok(resp) = http
            .get(url)
            .query(&[
                ("id", video_id),
                ("part", "snippet,contentDetails,status"),
                ("key", key),
            ])
            .timeout(std::time::Duration::from_secs(10))
            .send()
            .await
            && resp.status().is_success()
                && let Ok(data) = resp.json::<serde_json::Value>().await
                    && let Some(item) = data["items"].as_array().and_then(|a| a.first()) {
                        let snippet = &item["snippet"];
                        let content_details = &item["contentDetails"];
                        let status = &item["status"];
                        meta.title = snippet["title"].as_str().map(str::to_string);
                        meta.channel_name = snippet["channelTitle"].as_str().map(str::to_string);
                        meta.channel_id = snippet["channelId"].as_str().map(str::to_string);
                        meta.is_live = snippet["liveBroadcastContent"].as_str() == Some("live");
                        if let Some(dur) = content_details["duration"].as_str() {
                            let secs = parse_iso8601_duration(dur);
                            if secs > 0 {
                                meta.duration_seconds = Some(secs);
                            }
                        }
                        let region = &status["regionRestriction"];
                        let allowed =
                            normalize_country_list(region["allowed"].as_array().unwrap_or(&vec![]));
                        let blocked =
                            normalize_country_list(region["blocked"].as_array().unwrap_or(&vec![]));
                        if !allowed.is_empty() {
                            meta.geo_restriction_type = Some("allowed".to_string());
                            meta.geo_restriction_countries = allowed;
                        } else if !blocked.is_empty() {
                            meta.geo_restriction_type = Some("blocked".to_string());
                            meta.geo_restriction_countries = blocked;
                        }
                    }
    }

    if (meta.title.is_none() || meta.channel_name.is_none())
        && let Some((title, channel)) = fetch_oembed(http, video_id).await {
            if meta.title.is_none() && !title.is_empty() {
                meta.title = Some(title);
            }
            if meta.channel_name.is_none() && !channel.is_empty() {
                meta.channel_name = Some(channel);
            }
        }

    meta
}

async fn persist_youtube_geo_restriction(
    pool: &sqlx::PgPool,
    stream_id: i64,
    geo_type: Option<&str>,
    countries: &[String],
) {
    let Some(geo_type) = geo_type.filter(|t| *t == "allowed" || *t == "blocked") else {
        return;
    };
    let countries_json = serde_json::json!(countries);
    let _ = sqlx::query(
        "UPDATE youtube_stream SET geo_restriction_type = $1, geo_restriction_countries = $2 \
         WHERE stream_id = $3",
    )
    .bind(geo_type)
    .bind(countries_json)
    .bind(stream_id as i32)
    .execute(pool)
    .await;
}

// ─── Request structs ──────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct AnalyzeYouTubeRequest {
    pub url: String,
    pub meta_type: Option<String>,
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
    pub geo_restriction_type: Option<String>,
    pub geo_restriction_countries: Option<Vec<String>>,
    pub is_anonymous: Option<bool>,
    pub anonymous_display_name: Option<String>,
    pub languages: Option<Vec<String>>,
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

    // Fetch metadata (YouTube Data API when configured, oEmbed fallback)
    let fetched = fetch_youtube_metadata(
        &state.http,
        &video_id,
        state.config.youtube_api_key.as_deref(),
    )
    .await;

    let already_exists: bool =
        sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM youtube_stream WHERE video_id = $1)")
            .bind(&video_id)
            .fetch_one(&state.pool)
            .await
            .unwrap_or(false);

    // Fetch oEmbed metadata when API did not return title/channel
    let title = fetched.title.clone().unwrap_or_default();
    let channel_name = fetched.channel_name.clone().unwrap_or_default();

    let meta_type = body.meta_type.as_deref().unwrap_or("movie");
    let mut response = json!({
        "status": "success",
        "video_id": video_id,
        "url": format!("https://www.youtube.com/watch?v={video_id}"),
        "title": title,
        "channel_name": channel_name,
        "channel_id": fetched.channel_id,
        "duration_seconds": fetched.duration_seconds,
        "is_live": fetched.is_live,
        "geo_restriction_type": fetched.geo_restriction_type,
        "geo_restriction_countries": fetched.geo_restriction_countries,
        "already_exists": already_exists,
    });

    if !title.is_empty() {
        let matches = super::import_helpers::search_analyze_matches(
            &state,
            UserId::from_auth_id(user_id),
            &title,
            None,
            meta_type,
        )
        .await;
        if let Some(obj) = response.as_object_mut() {
            obj.insert("matches".to_string(), serde_json::Value::Array(matches));
        }
    }

    Json(response).into_response()
}

pub async fn analyze_youtube_for_bot(
    state: &AppState,
    url: &str,
    meta_type: &str,
) -> serde_json::Value {
    let video_id = match extract_video_id(url) {
        Some(id) => id,
        None => {
            return json!({"success": false, "error": "Could not extract YouTube video ID"});
        }
    };
    let fetched = fetch_youtube_metadata(
        &state.http,
        &video_id,
        state.config.youtube_api_key.as_deref(),
    )
    .await;
    let title = fetched.title.clone().unwrap_or_default();
    let channel_name = fetched.channel_name.clone().unwrap_or_default();
    let matches = if !title.is_empty() {
        super::import_helpers::search_analyze_matches(state, None, &title, None, meta_type).await
    } else {
        vec![]
    };
    json!({
        "success": true,
        "video_id": video_id,
        "url": format!("https://www.youtube.com/watch?v={video_id}"),
        "title": title,
        "channel_name": channel_name,
        "duration_seconds": fetched.duration_seconds,
        "geo_restriction_type": fetched.geo_restriction_type,
        "geo_restriction_countries": fetched.geo_restriction_countries,
        "parsed_title": title,
        "matches": matches,
    })
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
    let auto_approve =
        should_auto_approve_import(is_privileged, user.is_active, resolved_is_anonymous);

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

    // Fetch metadata when title/channel/duration not provided
    let fetched = fetch_youtube_metadata(
        &state.http,
        &video_id,
        state.config.youtube_api_key.as_deref(),
    )
    .await;

    let stream_name = body
        .name
        .as_deref()
        .filter(|s| !s.is_empty())
        .or(fetched.title.as_deref())
        .or(body.title.as_deref())
        .unwrap_or("YouTube Video")
        .to_string();

    let channel_name = body
        .channel_name
        .as_deref()
        .filter(|s| !s.is_empty())
        .or(fetched.channel_name.as_deref())
        .unwrap_or("")
        .to_string();

    let channel_id = body
        .channel_id
        .as_deref()
        .filter(|s| !s.is_empty())
        .or(fetched.channel_id.as_deref())
        .map(str::to_string);

    let duration_seconds = body.duration_seconds.or(fetched.duration_seconds);
    let is_live = body.is_live || fetched.is_live;
    let geo_restriction_type = body
        .geo_restriction_type
        .as_deref()
        .filter(|s| !s.is_empty())
        .or(fetched.geo_restriction_type.as_deref())
        .map(str::to_string);
    let geo_restriction_countries = body
        .geo_restriction_countries
        .clone()
        .filter(|c| !c.is_empty())
        .unwrap_or(fetched.geo_restriction_countries);

    let (uploader_name, uploader_user_id) = resolve_uploader_identity(
        resolved_is_anonymous,
        body.anonymous_display_name.as_deref(),
        &user.username,
        user_id,
    );
    let is_public = super::import_helpers::stream_is_public_on_submit(auto_approve, body.is_public);

    let meta_type = body.meta_type.as_deref().unwrap_or("movie");
    let effective_meta_id = body
        .meta_id
        .as_deref()
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| super::import_helpers::synthetic_import_meta_id("youtube", &video_id));

    let media_id = super::import_helpers::resolve_media_for_import(
        &state.pool,
        &state.http,
        state.config.tmdb_api_key.as_deref(),
        state.config.tvdb_api_key.as_deref(),
        &effective_meta_id,
        meta_type,
        crate::scrapers::media_resolve::ImportMediaOverrides {
            title: body.title.as_deref().or(Some(stream_name.as_str())),
            poster: None,
            background: None,
            release_date: None,
            year: None,
        },
        None,
        state.config.poster_nsfw_enabled,
    )
    .await
    .map(i64::from);

    let base = crate::db::StreamStoreBase {
        name: stream_name.clone(),
        source: "youtube".to_string(),
        uploader: Some(uploader_name.clone()),
        uploader_user_id: uploader_user_id.map(|id| id as i32),
        is_public,
        ..Default::default()
    };

    let normalized = crate::db::YoutubeStoreInput {
        base,
        video_id: video_id.clone(),
        channel_id: channel_id.clone(),
        channel_name: Some(channel_name.clone()),
        duration_seconds: duration_seconds.map(|n| n as i32),
        is_live,
        is_premiere: false,
    };

    let media_type =
        crate::db::MediaType::from_wire(meta_type).unwrap_or(crate::db::MediaType::Movie);
    let opts = media_id.map_or_else(
        || crate::db::StoreStreamOpts {
            media_id: crate::db::MediaId(0),
            media_type,
            season: None,
            episode: None,
            episode_end: None,
            link_source: crate::db::LinkSource::User,
            is_primary: true,
            is_verified: false,
        },
        |mid| crate::db::StoreStreamOpts::user_import(crate::db::MediaId(mid as i32), media_type),
    );

    let stream_id: i64 =
        match crate::db::store_youtube_stream(&state.pool, &normalized, &opts).await {
            Ok(r) => r.stream_id().0 as i64,
            Err(e) => {
                tracing::error!("import_youtube_video store: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        };

    persist_youtube_geo_restriction(
        &state.pool,
        stream_id,
        geo_restriction_type.as_deref(),
        &geo_restriction_countries,
    )
    .await;

    let data = serde_json::json!({
        "name": stream_name,
        "title": body.title.as_deref().unwrap_or(&stream_name),
        "video_id": video_id,
        "url": body.url,
        "channel_id": channel_id,
        "channel_name": channel_name,
        "meta_type": body.meta_type.as_deref().unwrap_or("movie"),
        "meta_id": effective_meta_id,
        "duration_seconds": duration_seconds,
        "is_live": is_live,
        "geo_restriction_type": geo_restriction_type,
        "geo_restriction_countries": geo_restriction_countries,
        "languages": body.languages.clone().unwrap_or_default(),
        "uploader_name": uploader_name,
        "anonymous_display_name": body.anonymous_display_name,
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
                award_contribution_points(&state.pool, uid, "youtube").await;
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

    let message = if auto_approve {
        "YouTube stream imported successfully!".to_string()
    } else {
        super::import_helpers::pending_import_message("YouTube stream")
    };

    (
        StatusCode::CREATED,
        Json(json!({
            "status": if auto_approve { "success" } else { "pending" },
            "message": message,
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
