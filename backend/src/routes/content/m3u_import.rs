/// M3U playlist parse and import endpoints.
///
/// Routes (prefix /api/v1/import):
///   POST /m3u/analyze       → analyze_m3u
///   POST /m3u               → import_m3u
///   GET  /job/{job_id}      → get_import_job_status
///   GET  /iptv-settings     → get_iptv_settings
use std::sync::Arc;

use axum::{
    extract::{Path, Request, State},
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

use crate::db::MediaType;
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

fn classify_entry(group: &Option<String>) -> &'static str {
    let g = match group.as_deref() {
        Some(g) => g.to_lowercase(),
        None => return "tv",
    };
    if g.contains("vod") || g.contains("movie") || g.contains("film") {
        "movie"
    } else if g.contains("series") || g.contains("show") || g.contains("episode") {
        "series"
    } else {
        // live, sports, news, etc. → tv
        "tv"
    }
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
                let entry_type = classify_entry(&group).to_string();
                let behavior_hints = if pending_headers.is_empty() {
                    None
                } else {
                    Some(serde_json::json!({ "headers": pending_headers }))
                };
                pending_headers.clear();
                let (parsed_title, parsed_year, season, episode) =
                    super::iptv_import::parse_iptv_title_info(&name);
                let entry_type =
                    super::iptv_import::refine_entry_type(&entry_type, season, episode).to_string();
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

/// Find or create a TV media entry, then insert/link HTTP stream.
/// Returns true if a new stream was inserted.
pub async fn import_tv_channel(
    pool: &sqlx::PgPool,
    name: &str,
    url: &str,
    logo: Option<&str>,
    source_name: &str,
    behavior_hints: Option<&serde_json::Value>,
) -> bool {
    // Find existing media by title+type
    let media_id: Option<i64> = sqlx::query_scalar(
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
            let res: Option<(i64,)> = sqlx::query_as(
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
                    match sqlx::query_scalar::<_, i64>(
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

    // Update poster if logo is provided and media has no poster
    if let Some(poster) = logo {
        let _ = sqlx::query(
            "UPDATE media SET poster = $1 WHERE id = $2 AND (poster IS NULL OR poster = '')",
        )
        .bind(poster)
        .bind(media_id)
        .execute(pool)
        .await;
    }

    // Check for existing stream with this URL linked to this media
    let existing: Option<i32> = sqlx::query_scalar(
        "SELECT hs.stream_id FROM http_stream hs \
         JOIN stream_media_link sml ON sml.stream_id = hs.stream_id \
         WHERE hs.url = $1 AND sml.media_id = $2 \
         LIMIT 1",
    )
    .bind(url)
    .bind(media_id as i32)
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
        crate::db::MediaId(media_id as i32),
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
    req: Request,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }

    if !state.config.enable_iptv_import {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "IPTV import feature is disabled on this server."})),
        )
            .into_response();
    }

    // Determine content-type; accept form or raw text
    let content_type = headers
        .get("content-type")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_lowercase();

    let (m3u_url, m3u_content) = if content_type.contains("application/x-www-form-urlencoded") {
        // Read body as form-encoded
        let body_bytes = match axum::body::to_bytes(req.into_body(), 10 * 1024 * 1024).await {
            Ok(b) => b,
            Err(_) => {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(json!({"detail": "Failed to read body"})),
                )
                    .into_response();
            }
        };
        let body_str = String::from_utf8_lossy(&body_bytes).to_string();
        // Parse form manually
        let mut url_val: Option<String> = None;
        let mut content_val: Option<String> = None;
        for pair in body_str.split('&') {
            if let Some((k, v)) = pair.split_once('=') {
                let key = urlencoding::decode(k).unwrap_or_default().to_string();
                let val = urlencoding::decode(v).unwrap_or_default().to_string();
                match key.as_str() {
                    "m3u_url" => url_val = Some(val),
                    "content" | "m3u_content" => content_val = Some(val),
                    _ => {}
                }
            }
        }
        (url_val, content_val)
    } else if content_type.contains("multipart/form-data") {
        // For multipart we'd need full multipart parsing; return error for now
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Use application/x-www-form-urlencoded or raw text body"})),
        )
            .into_response();
    } else {
        // Treat body as raw M3U text
        let body_bytes = match axum::body::to_bytes(req.into_body(), 50 * 1024 * 1024).await {
            Ok(b) => b,
            Err(_) => {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(json!({"detail": "Failed to read body"})),
                )
                    .into_response();
            }
        };
        let text = String::from_utf8_lossy(&body_bytes).to_string();
        if text.trim_start().starts_with("#EXTM3U") || text.trim_start().starts_with("#EXTINF") {
            (None, Some(text))
        } else {
            // Try JSON body with m3u_url field
            let json_val: serde_json::Value = serde_json::from_str(&text).unwrap_or_default();
            let url = json_val
                .get("m3u_url")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string());
            (url, None)
        }
    };

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

    // Preview: first 50 entries (with metadata matches for movie/series, Python parity)
    let mut preview: Vec<serde_json::Value> = Vec::new();
    for e in entries.iter().take(50) {
        let parsed = crate::parser::parse_title(&e.name);
        let search_title = parsed.title.as_deref().unwrap_or(&e.name);
        let parsed_year = parsed.year;

        let mut item = json!({
            "name": e.name,
            "url": e.url,
            "logo": e.logo,
            "group": e.group,
            "tvg_id": e.tvg_id,
            "type": e.entry_type,
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

        preview.push(item);
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
        "redis_key": redis_key,
        "total": total,
        "tv_count": tv_count,
        "movie_count": movie_count,
        "series_count": series_count,
        "groups": groups_list,
        "preview": preview,
        "m3u_url": m3u_url,
    }))
    .into_response()
}

/// POST /api/v1/import/m3u
pub async fn import_m3u(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    req: Request,
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

    // Parse body as JSON or form
    let content_type = headers
        .get("content-type")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_lowercase();

    let body_bytes = match axum::body::to_bytes(req.into_body(), 10 * 1024 * 1024).await {
        Ok(b) => b,
        Err(_) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Failed to read body"})),
            )
                .into_response();
        }
    };

    let params: serde_json::Value = if content_type.contains("application/json") {
        serde_json::from_slice(&body_bytes).unwrap_or_default()
    } else {
        // Form-encoded
        let body_str = String::from_utf8_lossy(&body_bytes).to_string();
        let mut map = serde_json::Map::new();
        for pair in body_str.split('&') {
            if let Some((k, v)) = pair.split_once('=') {
                let key = urlencoding::decode(k).unwrap_or_default().to_string();
                let val = urlencoding::decode(v).unwrap_or_default().to_string();
                map.insert(key, serde_json::Value::String(val));
            }
        }
        serde_json::Value::Object(map)
    };

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
                        url::Url::parse(url)
                            .ok()
                            .and_then(|u| u.host_str().map(|h| format!("M3U - {h}")))
                            .unwrap_or_else(|| "M3U Import".to_string())
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
                .unwrap_or_else(|| {
                    url::Url::parse(url)
                        .ok()
                        .and_then(|u| u.host_str().map(|h| format!("M3U - {h}")))
                        .unwrap_or_else(|| "M3U Import".to_string())
                });
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
