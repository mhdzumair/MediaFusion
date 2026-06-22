/// M3U playlist parse and import endpoints.
///
/// Routes (prefix /api/v1/import):
///   POST /m3u/analyze       → analyze_m3u
///   POST /m3u               → import_m3u
///   GET  /job/{job_id}      → get_import_job_status
///   GET  /iptv-settings     → get_iptv_settings
use std::sync::Arc;

use axum::{
    extract::{Multipart, Path, Request, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::Utc;
use fred::prelude::*;
use hmac::{Hmac, KeyInit, Mac};
use serde_json::json;
use sha2::Sha256;
use uuid::Uuid;

use crate::db::{MediaType, UserId};
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

// ─── M3U parsing ──────────────────────────────────────────────────────────────

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct M3uEntry {
    pub name: String,
    pub url: String,
    pub logo: Option<String>,
    pub group: Option<String>,
    pub tvg_id: Option<String>,
    pub entry_type: String, // "tv", "movie", "series"
    #[serde(skip_serializing_if = "Option::is_none")]
    pub behavior_hints: Option<serde_json::Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub index: Option<usize>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub parsed_title: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub parsed_year: Option<i32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub season: Option<i32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub episode: Option<i32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub matched_media_id: Option<String>,
}

const TV_KEYWORDS: &[&str] = &[
    "live",
    "tv",
    "channel",
    "news",
    "sports",
    "24/7",
    "radio",
    "entertainment",
    "music",
    "kids",
    "documentary",
    "general",
];
const MOVIE_KEYWORDS: &[&str] = &[
    "movie",
    "movies",
    "film",
    "films",
    "cinema",
    "vod movie",
    "hd movies",
    "4k movies",
];
const SERIES_KEYWORDS: &[&str] = &[
    "series",
    "shows",
    "tv show",
    "tv shows",
    "episode",
    "season",
    "vod series",
    "drama",
    "sitcom",
];
const LIVE_STREAM_EXTENSIONS: &[&str] = &[".m3u8", ".ts", ".mpd"];
const VOD_EXTENSIONS: &[&str] = &[".mp4", ".mkv", ".avi", ".webm", ".mov", ".wmv", ".flv"];
// Audio-only streams (radio): classified as live "tv" channels — Stremio/MediaFusion
// have no dedicated radio type, so radio is modelled as a live channel.
const AUDIO_STREAM_EXTENSIONS: &[&str] = &[
    ".mp3", ".aac", ".m4a", ".ogg", ".opus", ".flac", ".wav", ".audio",
];

fn group_contains_keyword(group_lower: &str, keywords: &[&str]) -> bool {
    keywords.iter().any(|kw| group_lower.contains(kw))
}

fn detect_content_type_from_group(group_title: Option<&str>) -> Option<&'static str> {
    let group_lower = group_title?.trim().to_lowercase();
    if group_lower.is_empty() {
        return None;
    }
    if group_contains_keyword(&group_lower, SERIES_KEYWORDS) {
        return Some("series");
    }
    if group_contains_keyword(&group_lower, MOVIE_KEYWORDS) {
        return Some("movie");
    }
    if group_contains_keyword(&group_lower, TV_KEYWORDS) {
        return Some("tv");
    }
    None
}

fn detect_content_type_from_url(url: &str) -> Option<&'static str> {
    if url.trim().is_empty() {
        return None;
    }
    let path_lower = url::Url::parse(url)
        .ok()
        .map(|u| u.path().to_lowercase())
        .unwrap_or_else(|| url.to_lowercase());

    if LIVE_STREAM_EXTENSIONS
        .iter()
        .any(|ext| path_lower.ends_with(ext))
    {
        return Some("tv");
    }
    if AUDIO_STREAM_EXTENSIONS
        .iter()
        .any(|ext| path_lower.ends_with(ext))
    {
        return Some("tv");
    }
    if VOD_EXTENSIONS.iter().any(|ext| path_lower.ends_with(ext)) {
        return None;
    }
    if ["/live/", "/tv/", "/channel/"]
        .iter()
        .any(|seg| path_lower.contains(seg))
    {
        return Some("tv");
    }
    if ["/movie/", "/movies/", "/film/"]
        .iter()
        .any(|seg| path_lower.contains(seg))
    {
        return Some("movie");
    }
    if ["/series/", "/show/", "/episode/"]
        .iter()
        .any(|seg| path_lower.contains(seg))
    {
        return Some("series");
    }
    None
}

/// Multi-signal content type detection (Python `detect_content_type` parity).
fn detect_content_type(
    name: &str,
    url: &str,
    group_title: Option<&str>,
) -> (String, String, Option<i32>, Option<i32>, Option<i32>) {
    let (parsed_title, parsed_year, season, episode) =
        super::iptv_import::parse_iptv_title_info(name);

    let detected: &str = if let Some(t) = detect_content_type_from_url(url) {
        // 1. The URL is the strongest signal. A live-stream URL (HLS/DASH/TS,
        //    audio, or a /live//tv//channel/ path) wins over name parsing because
        //    live channel names routinely embed numbers ("[5] Canale 5", "Rai 4",
        //    "Sky Sport 24") that the title parser misreads as an episode number.
        t
    } else if let Some(t) = detect_content_type_from_group(group_title) {
        // 2. An explicit group-title keyword from the playlist author.
        t
    } else if season.is_some() {
        // 3. Name-based series detection requires a SEASON marker. A lone episode
        //    number with no season is almost always a channel number ("[20] 20"),
        //    not a series episode, so it must NOT force a series classification.
        "series"
    } else {
        // 4. No live/series/movie signal: a VOD file extension means a movie;
        //    otherwise default to a live "tv" channel (relinker / extension-less
        //    live URLs like RAI's land here) instead of skipping the entry.
        let path_lower = url::Url::parse(url)
            .ok()
            .map(|u| u.path().to_lowercase())
            .unwrap_or_default();
        if VOD_EXTENSIONS.iter().any(|ext| path_lower.ends_with(ext)) {
            "movie"
        } else {
            "tv"
        }
    };

    // Carry season/episode only for a series; otherwise drop the channel-number
    // artefacts so a live channel isn't tagged with a phantom episode.
    let (season, episode) = if detected == "series" {
        (season, episode)
    } else {
        (None, None)
    };

    (
        detected.to_string(),
        parsed_title,
        parsed_year,
        season,
        episode,
    )
}

/// Parse M3U playlist content into a list of entries.
/// Extracts `#EXTVLCOPT:http-user-agent` and `#EXTVLCOPT:http-referrer` directives
/// that appear between an `#EXTINF` line and its URL, storing them as `behavior_hints`.
pub fn parse_m3u(content: &str) -> Vec<M3uEntry> {
    let mut entries = Vec::new();
    #[allow(clippy::type_complexity)]
    let mut current_meta: Option<(String, Option<String>, Option<String>, Option<String>)> = None;
    // (name, logo, group, tvg_id)
    let mut pending_headers: std::collections::HashMap<String, String> =
        std::collections::HashMap::new();

    for line in content.lines() {
        let line = line.trim();
        if line.starts_with("#EXTINF:") {
            // Parse the EXTINF line
            // Format: #EXTINF:-1 tvg-id="..." tvg-logo="..." group-title="..." ,NAME
            pending_headers.clear();
            let tvg_id = extract_m3u_attr(line, "tvg-id");
            let logo =
                extract_m3u_attr(line, "tvg-logo").or_else(|| extract_m3u_attr(line, "tvg-image"));
            let group = extract_m3u_attr(line, "group-title");
            // Name is after the last comma
            let name = line
                .rfind(',')
                .map(|i| line[i + 1..].trim().to_string())
                .unwrap_or_default();
            let name = if name.is_empty() {
                tvg_id.clone().unwrap_or_else(|| "Unknown".to_string())
            } else {
                name
            };
            current_meta = Some((name, logo, group, tvg_id));
        } else if line.starts_with("#EXTVLCOPT:") && current_meta.is_some() {
            // Extract per-entry VLC options that map to HTTP headers
            let opt = &line["#EXTVLCOPT:".len()..];
            if let Some((key, val)) = opt.split_once('=') {
                match key.trim() {
                    "http-user-agent" => {
                        pending_headers.insert("User-Agent".to_string(), val.trim().to_string());
                    }
                    "http-referrer" => {
                        pending_headers.insert("Referer".to_string(), val.trim().to_string());
                    }
                    _ => {}
                }
            }
        } else if !line.is_empty() && !line.starts_with('#') {
            // This is a URL line
            if let Some((name, logo, group, tvg_id)) = current_meta.take() {
                let behavior_hints = if pending_headers.is_empty() {
                    None
                } else {
                    Some(serde_json::json!({ "headers": pending_headers }))
                };
                pending_headers.clear();
                let (entry_type, parsed_title, parsed_year, season, episode) =
                    detect_content_type(&name, line, group.as_deref());
                entries.push(M3uEntry {
                    name,
                    url: line.to_string(),
                    logo,
                    group,
                    tvg_id,
                    entry_type,
                    behavior_hints,
                    index: None,
                    parsed_title: Some(parsed_title),
                    parsed_year,
                    season,
                    episode,
                    matched_media_id: None,
                });
            }
        }
    }

    entries
}

/// Extract an attribute value from an #EXTINF line.
fn extract_m3u_attr(line: &str, attr: &str) -> Option<String> {
    let search = format!("{}=\"", attr);
    let start = line.find(&search)?;
    let rest = &line[start + search.len()..];
    let end = rest.find('"')?;
    let val = rest[..end].trim().to_string();
    if val.is_empty() {
        None
    } else {
        Some(val)
    }
}

// ─── DB helpers for TV channel insertion ──────────────────────────────────────

fn url_is_audio_stream(url: &str) -> bool {
    let path_lower = url::Url::parse(url)
        .ok()
        .map(|u| u.path().to_lowercase())
        .unwrap_or_else(|| url.to_lowercase());
    AUDIO_STREAM_EXTENSIONS
        .iter()
        .any(|ext| path_lower.ends_with(ext))
}

/// Derive a human-friendly source name from an M3U URL when the user supplies
/// none. Prefer the playlist filename stem (".../iptvita.m3u" → "M3U - iptvita")
/// so several playlists from the same host stay distinguishable in IPTV Sources;
/// fall back to the host, then to a generic label.
fn derive_source_name_from_url(url: &str) -> String {
    if let Ok(parsed) = url::Url::parse(url) {
        let stem = parsed
            .path_segments()
            .and_then(|mut segs| segs.rfind(|s| !s.is_empty()))
            .map(|seg| seg.rsplit_once('.').map(|(s, _)| s).unwrap_or(seg))
            .filter(|s| !s.is_empty());
        if let Some(stem) = stem {
            return format!("M3U - {stem}");
        }
        if let Some(host) = parsed.host_str() {
            return format!("M3U - {host}");
        }
    }
    "M3U Import".to_string()
}

pub async fn import_tv_channel(
    pool: &sqlx::PgPool,
    name: &str,
    url: &str,
    logo: Option<&str>,
    group: Option<&str>,
    source_name: &str,
    behavior_hints: Option<&serde_json::Value>,
) -> bool {
    // Find existing media by title+type. media.id is INT4 (i32) — reading it as i64
    // makes the sqlx decode fail (→ None), so the lookup/insert silently never
    // resolves an id and the whole import is skipped. Must be i32.
    let media_id: Option<i32> = sqlx::query_scalar(
        "SELECT id FROM media WHERE LOWER(title) = LOWER($1) AND type = $2 LIMIT 1",
    )
    .bind(name)
    .bind(MediaType::Tv)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten();

    let media_id = match media_id {
        Some(id) => id,
        None => {
            // Insert new media
            let res: Option<(i32,)> = sqlx::query_as(
                "INSERT INTO media (title, type, created_at, adult, is_blocked, is_public, is_user_created, nudity_status, total_streams, popularity) \
                 VALUES ($1, $2, NOW(), false, false, true, false, 'UNKNOWN', 0, 0.0) \
                 ON CONFLICT DO NOTHING RETURNING id",
            )
            .bind(name)
            .bind(MediaType::Tv)
            .fetch_optional(pool)
            .await
            .ok()
            .flatten();

            match res {
                Some((id,)) => id,
                None => {
                    // Conflict: fetch existing
                    match sqlx::query_scalar::<_, i32>(
                        "SELECT id FROM media WHERE LOWER(title) = LOWER($1) AND type = $2 LIMIT 1",
                    )
                    .bind(name)
                    .bind(MediaType::Tv)
                    .fetch_optional(pool)
                    .await
                    .ok()
                    .flatten()
                    {
                        Some(id) => id,
                        None => return false,
                    }
                }
            }
        }
    };

    // Link the channel to its catalog. Audio-only streams (radio) go to the
    // "radio" catalog; everything else to "live_tv". Both are seeded by migration.
    // Use the group title as the primary radio signal; fall back to URL extension.
    let group_lower = group.map(|g| g.to_lowercase()).unwrap_or_default();
    let is_radio =
        group_lower.contains("radio") || group_lower.contains("audio") || url_is_audio_stream(url);
    let catalog_name = if is_radio { "radio" } else { "live_tv" };
    crate::db::link_to_catalogs(pool, media_id, &[catalog_name]).await;

    // Persist the channel logo (tvg-logo) as a poster image. Posters live in
    // `media_image` (the only table the /poster handler reads) — there is NO
    // `media.poster` column. The 'm3u' provider is seeded by migration 0021.
    if let Some(logo_url) = logo.filter(|l| !l.is_empty()) {
        let provider_id: Option<i32> =
            sqlx::query_scalar("SELECT id FROM metadata_provider WHERE name = 'm3u' LIMIT 1")
                .fetch_optional(pool)
                .await
                .ok()
                .flatten();

        if let Some(pid) = provider_id {
            let _ = sqlx::query(
                "INSERT INTO media_image \
                    (media_id, provider_id, image_type, url, is_primary, display_order) \
                 VALUES ($1, $2, 'poster', $3, true, 0) \
                 ON CONFLICT (media_id, provider_id, image_type, url) DO NOTHING",
            )
            .bind(media_id)
            .bind(pid)
            .bind(logo_url)
            .execute(pool)
            .await;
        }
    }

    // Check for existing stream with this URL linked to this media
    let existing: Option<i32> = sqlx::query_scalar(
        "SELECT hs.stream_id FROM http_stream hs \
         JOIN stream_media_link sml ON sml.stream_id = hs.stream_id \
         WHERE hs.url = $1 AND sml.media_id = $2 \
         LIMIT 1",
    )
    .bind(url)
    .bind(media_id)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten();

    if existing.is_some() {
        return false; // already exists
    }

    let normalized = crate::db::HttpStoreInput {
        base: crate::db::StreamStoreBase {
            name: name.to_string(),
            source: source_name.to_string(),
            is_public: true,
            ..Default::default()
        },
        url: url.to_string(),
        format: None,
        behavior_hints: behavior_hints.cloned(),
        drm_key_id: None,
        drm_key: None,
        extractor_name: None,
    };

    let opts = crate::db::StoreStreamOpts::user_import(
        crate::db::MediaId(media_id),
        crate::db::MediaType::Movie,
    );

    match crate::db::store_http_stream(pool, &normalized, &opts).await {
        Ok(r) if r.was_inserted() => true,
        Ok(_) => false,
        Err(_) => false,
    }
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// POST /api/v1/import/m3u/analyze
pub async fn analyze_m3u(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    mut multipart: Multipart,
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

    if !state.config.enable_iptv_import {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "IPTV import feature is disabled on this server."})),
        )
            .into_response();
    }

    // Accept the playlist via multipart/form-data fields, matching the web client
    // (which always sends FormData): `m3u_url` (text) and/or `m3u_file` (an uploaded
    // .m3u). Previously this endpoint rejected multipart outright.
    let mut m3u_url: Option<String> = None;
    let mut m3u_content: Option<String> = None;
    while let Ok(Some(field)) = multipart.next_field().await {
        match field.name() {
            Some("m3u_url") => {
                m3u_url = field.text().await.ok().filter(|s| !s.trim().is_empty());
            }
            Some("m3u_file") | Some("content") | Some("m3u_content") => {
                m3u_content = field
                    .bytes()
                    .await
                    .ok()
                    .map(|b| String::from_utf8_lossy(&b).to_string())
                    .filter(|s| !s.trim().is_empty());
            }
            _ => {}
        }
    }

    // Fetch content from URL if provided
    let content = if let Some(content) = m3u_content {
        content
    } else if let Some(url) = &m3u_url {
        match state
            .http
            .get(url)
            .timeout(std::time::Duration::from_secs(60))
            .send()
            .await
        {
            Ok(r) if r.status().is_success() => r.text().await.unwrap_or_default(),
            Ok(r) => {
                return (
                    StatusCode::BAD_GATEWAY,
                    Json(
                        json!({"detail": format!("Failed to fetch M3U URL: HTTP {}", r.status())}),
                    ),
                )
                    .into_response();
            }
            Err(e) => {
                return (
                    StatusCode::BAD_GATEWAY,
                    Json(json!({"detail": format!("Failed to fetch M3U URL: {e}")})),
                )
                    .into_response();
            }
        }
    } else {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Provide m3u_url or raw M3U content in request body"})),
        )
            .into_response();
    };

    let entries = parse_m3u(&content);
    let total = entries.len();

    // Count by type
    let tv_count = entries.iter().filter(|e| e.entry_type == "tv").count();
    let movie_count = entries.iter().filter(|e| e.entry_type == "movie").count();
    let series_count = entries.iter().filter(|e| e.entry_type == "series").count();

    // Collect unique groups
    let mut groups: std::collections::HashMap<String, usize> = std::collections::HashMap::new();
    for e in &entries {
        if let Some(g) = &e.group {
            *groups.entry(g.clone()).or_insert(0) += 1;
        }
    }
    let mut groups_list: Vec<serde_json::Value> = groups
        .iter()
        .map(|(name, count)| json!({"name": name, "count": count}))
        .collect();
    groups_list.sort_by(|a, b| {
        b["count"]
            .as_u64()
            .unwrap_or(0)
            .cmp(&a["count"].as_u64().unwrap_or(0))
    });

    // Preview channels: first 50 entries (with metadata matches for movie/series).
    // Field shape matches the M3UAnalyzeResponse contract the web client consumes
    // (index, detected_type, genres, country, season, episode) — Python parity.
    let mut channels: Vec<serde_json::Value> = Vec::new();
    for (idx, e) in entries.iter().take(50).enumerate() {
        let parsed = crate::parser::parse_title(&e.name);
        let search_title = parsed.title.as_deref().unwrap_or(&e.name);
        let parsed_year = parsed.year;

        let mut item = json!({
            "index": idx,
            "name": e.name,
            "url": e.url,
            "logo": e.logo,
            "group": e.group,
            "genres": [],
            "country": serde_json::Value::Null,
            "tvg_id": e.tvg_id,
            "detected_type": e.entry_type,
            "season": e.season,
            "episode": e.episode,
            "parsed_title": search_title,
            "parsed_year": parsed_year,
        });

        if e.entry_type == "movie" || e.entry_type == "series" {
            let meta_type = if e.entry_type == "series" {
                "series"
            } else {
                "movie"
            };
            let matches = super::import_helpers::search_analyze_matches(
                &state,
                UserId::from_auth_id(user_id),
                search_title,
                parsed_year,
                meta_type,
            )
            .await;
            if let Some(first) = matches.first() {
                item["matched_media"] = json!({
                    "id": first.get("id").and_then(|v| v.as_str()).unwrap_or(""),
                    "title": first.get("title").and_then(|v| v.as_str()).unwrap_or(""),
                    "year": first.get("year"),
                    "poster": first.get("poster"),
                    "type": meta_type,
                });
            }
        }

        channels.push(item);
    }

    // Cache full entries in Redis
    let redis_key = format!("m3u_analyze_{}", Uuid::new_v4());
    if let Ok(json_str) = serde_json::to_string(&entries) {
        let _ = state
            .redis
            .set::<(), _, _>(
                &redis_key,
                json_str,
                Some(Expiration::EX(3600)),
                None,
                false,
            )
            .await;
    }

    Json(json!({
        "status": "success",
        "redis_key": redis_key,
        "total_count": total,
        "channels": channels,
        "summary": {
            "tv": tv_count,
            "movie": movie_count,
            "series": series_count,
            "unknown": total.saturating_sub(tv_count + movie_count + series_count),
        },
        "groups": groups_list,
    }))
    .into_response()
}

/// POST /api/v1/import/m3u
pub async fn import_m3u(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    mut multipart: Multipart,
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

    // Read params from multipart/form-data fields (the web client always sends
    // FormData). `m3u_file` is ignored here: import re-uses the parsed entries via
    // the `redis_key` from the analyze step, or re-fetches `m3u_url`.
    let mut map = serde_json::Map::new();
    while let Ok(Some(field)) = multipart.next_field().await {
        let name = match field.name() {
            Some(n) => n.to_string(),
            None => continue,
        };
        if name == "m3u_file" {
            continue;
        }
        let val = match field.text().await {
            Ok(v) => v,
            Err(_) => continue,
        };
        match name.as_str() {
            "is_public" | "save_source" => {
                map.insert(name, serde_json::Value::Bool(val == "true" || val == "1"));
            }
            _ => {
                map.insert(name, serde_json::Value::String(val));
            }
        }
    }
    let params = serde_json::Value::Object(map);

    let redis_key = params
        .get("redis_key")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string());
    let source_name = params
        .get("source_name")
        .and_then(|v| v.as_str())
        .unwrap_or("M3U Import")
        .to_string();
    let m3u_url = params
        .get("m3u_url")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string());
    if !state.config.enable_iptv_import {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "IPTV import feature is disabled on this server."})),
        )
            .into_response();
    }

    let mut is_public = params
        .get("is_public")
        .and_then(|v| v.as_bool())
        .unwrap_or(true);
    if !state.config.allow_public_iptv_sharing {
        is_public = false;
    }

    let overrides_raw = params
        .get("overrides")
        .and_then(|v| v.as_str())
        .map(str::to_string);
    let override_map = super::iptv_import::parse_override_map(overrides_raw.as_deref());
    let source_label = params
        .get("source")
        .and_then(|v| v.as_str())
        .map(str::to_string)
        .unwrap_or_else(|| source_name.clone());

    // Load entries from Redis or re-parse from URL
    let entries: Vec<M3uEntry> = if let Some(ref key) = redis_key {
        let cached: Option<String> = state.redis.get(key).await.unwrap_or(None);
        if let Some(json_str) = cached {
            serde_json::from_str(&json_str).unwrap_or_default()
        } else if let Some(ref url) = m3u_url {
            // Re-fetch
            match state
                .http
                .get(url)
                .timeout(std::time::Duration::from_secs(60))
                .send()
                .await
            {
                Ok(r) if r.status().is_success() => {
                    let text = r.text().await.unwrap_or_default();
                    parse_m3u(&text)
                }
                _ => {
                    return (
                        StatusCode::BAD_GATEWAY,
                        Json(json!({"detail": "Failed to fetch M3U URL"})),
                    )
                        .into_response();
                }
            }
        } else {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "redis_key expired and no m3u_url provided"})),
            )
                .into_response();
        }
    } else if let Some(ref url) = m3u_url {
        match state
            .http
            .get(url)
            .timeout(std::time::Duration::from_secs(60))
            .send()
            .await
        {
            Ok(r) if r.status().is_success() => {
                let text = r.text().await.unwrap_or_default();
                parse_m3u(&text)
            }
            _ => {
                return (
                    StatusCode::BAD_GATEWAY,
                    Json(json!({"detail": "Failed to fetch M3U URL"})),
                )
                    .into_response();
            }
        }
    } else {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Provide redis_key or m3u_url"})),
        )
            .into_response();
    };

    let mut entries = entries;
    for (i, entry) in entries.iter_mut().enumerate() {
        entry.index = Some(i);
    }

    let total = entries.len();
    let ctx = super::iptv_import::IptvImportCtx::from_state(&state);

    const BACKGROUND_THRESHOLD: usize = 100;
    if total > BACKGROUND_THRESHOLD {
        let job_id = Uuid::new_v4().to_string();
        let job_key = format!("import_job:{job_id}");
        super::iptv_import::update_import_job_full(
            &state.redis,
            &job_key,
            "queued",
            0,
            total,
            &super::iptv_import::IptvImportStats::default(),
            Some(user_id),
            Some("m3u"),
            None,
        )
        .await;

        let pool = state.pool.clone();
        let http = state.http.clone();
        let redis = state.redis.clone();
        let tmdb = state.config.tmdb_api_key.clone();
        let tvdb = state.config.tvdb_api_key.clone();
        let cinemeta = state.config.imdb_cinemeta_fallback_enabled;
        let poster_nsfw_enabled = state.config.poster_nsfw_enabled;
        let source_bg = source_label.clone();
        let entries_owned = entries;
        let override_map_bg = override_map;
        let save_source_bg = params
            .get("save_source")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
        let m3u_url_bg = m3u_url.clone();
        let source_name_bg = source_name.clone();

        tokio::spawn(async move {
            super::iptv_import::update_import_job_full(
                &redis,
                &job_key,
                "processing",
                0,
                total,
                &super::iptv_import::IptvImportStats::default(),
                Some(user_id),
                Some("m3u"),
                None,
            )
            .await;

            let ctx_bg = super::iptv_import::IptvImportCtx {
                pool: &pool,
                http: &http,
                tmdb_api_key: tmdb.as_deref(),
                tvdb_api_key: tvdb.as_deref(),
                cinemeta_enabled: cinemeta,
                poster_nsfw_enabled,
            };
            let stats = super::iptv_import::run_m3u_import_batch(
                &ctx_bg,
                entries_owned,
                &source_bg,
                user_id,
                is_public,
                override_map_bg,
            )
            .await;

            let mut source_id: Option<i32> = None;
            if save_source_bg {
                if let Some(ref url) = m3u_url_bg {
                    let save_name = if source_name_bg.is_empty() {
                        derive_source_name_from_url(url)
                    } else {
                        source_name_bg.clone()
                    };
                    source_id = super::iptv_import::save_m3u_iptv_source(
                        &pool, user_id, &save_name, url, is_public, &stats,
                    )
                    .await
                    .ok();
                }
            }

            let mut job_body = serde_json::json!({
                "status": "completed",
                "progress": total,
                "total": total,
                "stats": stats,
                "user_id": user_id,
                "source_type": "m3u",
            });
            if let Some(sid) = source_id {
                job_body["source_id"] = serde_json::json!(sid);
                job_body["source_saved"] = serde_json::json!(true);
            }
            let _ = redis
                .set::<(), _, _>(
                    &job_key,
                    job_body.to_string(),
                    Some(fred::types::Expiration::EX(86400)),
                    None,
                    false,
                )
                .await;
        });

        if let Some(ref key) = redis_key {
            let _: Result<(), _> = state.redis.del(key).await;
        }

        return (
            StatusCode::ACCEPTED,
            Json(json!({
                "status": "processing",
                // web client reads the job id under `details` (M3UTab handleImport)
                "details": {"job_id": job_id},
                "job_id": job_id,
                "total": total,
                "message": format!("Import of {total} items started in background."),
            })),
        )
            .into_response();
    }

    let save_source = params
        .get("save_source")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);

    let stats = super::iptv_import::run_m3u_import_batch(
        &ctx,
        entries,
        &source_label,
        user_id,
        is_public,
        override_map,
    )
    .await;

    let mut source_id: Option<i32> = None;
    if save_source {
        if let Some(ref url) = m3u_url {
            let save_name = params
                .get("source_name")
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty())
                .map(str::to_string)
                .unwrap_or_else(|| derive_source_name_from_url(url));
            source_id = super::iptv_import::save_m3u_iptv_source(
                &state.pool,
                user_id,
                &save_name,
                url,
                is_public,
                &stats,
            )
            .await
            .ok();
        }
    }

    if let Some(ref key) = redis_key {
        let _: Result<(), _> = state.redis.del(key).await;
    }

    Json(json!({
        "status": "success",
        "stats": stats,
        "total": total,
        "source_saved": source_id.is_some(),
        "source_id": source_id,
    }))
    .into_response()
}

/// GET /api/v1/import/job/{job_id}
pub async fn get_import_job_status(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(job_id): Path<String>,
    _req: Request,
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

    let key = format!("import_job:{job_id}");
    let val: Option<String> = state.redis.get(&key).await.unwrap_or(None);

    match val {
        Some(json_str) => {
            let status: serde_json::Value =
                serde_json::from_str(&json_str).unwrap_or_else(|_| json!({"status": "unknown"}));
            if let Some(owner) = status.get("user_id").and_then(|v| v.as_i64()) {
                if owner != user_id {
                    return (
                        StatusCode::FORBIDDEN,
                        Json(json!({"detail": "Job does not belong to this user"})),
                    )
                        .into_response();
                }
            }
            Json(status).into_response()
        }
        None => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": format!("Job {job_id} not found")})),
        )
            .into_response(),
    }
}

/// GET /api/v1/import/iptv-settings  (no auth required)
/// Returns whether IPTV import is enabled and whether public sharing is allowed.
pub async fn get_iptv_settings_handler(State(state): State<Arc<AppState>>) -> Response {
    Json(serde_json::json!({
        "enabled": state.config.enable_iptv_import,
        "allow_public_sharing": state.config.allow_public_iptv_sharing,
    }))
    .into_response()
}

#[cfg(test)]
mod detection_tests {
    use super::detect_content_type;

    fn type_of(name: &str, url: &str, group: Option<&str>) -> String {
        detect_content_type(name, url, group).0
    }

    #[test]
    fn live_channels_with_number_prefix_are_tv_not_series() {
        // Regression: the "[N]" channel-number prefix was parsed as an episode
        // number, flagging every live channel as a series (Tundrak/IPTV-Italia).
        let mpd = "https://cdn.example.net/live/ch-c5/c5-clr.isml/manifest.mpd";
        let hls = "https://cdn.example.net/v1/master/abc/Live.m3u8";
        assert_eq!(type_of("[5] Canale 5", mpd, None), "tv");
        assert_eq!(type_of("[7] LA7", hls, None), "tv");
        assert_eq!(type_of("[20] 20", mpd, None), "tv");
    }

    #[test]
    fn extensionless_relinker_live_url_defaults_to_tv() {
        // RAI relinker URLs have no file extension and no /live/ path; with a bare
        // channel number in the name they must still resolve to a live tv channel.
        let relinker =
            "http://mediapolis.rai.it/relinker/relinkerServlet.htm?cont=2606803&output=7";
        assert_eq!(type_of("[1] Rai 1", relinker, None), "tv");
        assert_eq!(type_of("[24] Rai Movie", relinker, None), "tv");
    }

    #[test]
    fn real_series_with_season_marker_is_series() {
        // A genuine SxxExx VOD entry keeps its series classification.
        let mp4 = "https://vod.example.net/series/show/s01e02.mp4";
        assert_eq!(type_of("Breaking Bad S01E02", mp4, None), "series");
    }

    #[test]
    fn vod_movie_extension_is_movie() {
        let mp4 = "https://vod.example.net/movies/inception.mp4";
        assert_eq!(type_of("Inception 2010", mp4, None), "movie");
    }

    #[test]
    fn audio_stream_is_tv() {
        // Radio: audio-extension URLs classify as tv (routed to the Radio catalog
        // downstream by import_tv_channel).
        let mp3 = "https://radio.example.net/stream/radio1.mp3";
        assert_eq!(type_of("[1] Radio Uno", mp3, None), "tv");
    }

    #[test]
    fn group_title_movie_keyword_wins_when_url_is_ambiguous() {
        let ambiguous = "http://host.example.net/path/12345";
        assert_eq!(
            type_of("Some Title", ambiguous, Some("VOD Movies")),
            "movie"
        );
    }
}
