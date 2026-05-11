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
#[allow(clippy::type_complexity)]
pub fn parse_m3u(content: &str) -> Vec<M3uEntry> {
    let mut entries = Vec::new();
    let mut current_meta: Option<(String, Option<String>, Option<String>, Option<String>)> = None;
    // (name, logo, group, tvg_id)

    for line in content.lines() {
        let line = line.trim();
        if line.starts_with("#EXTINF:") {
            // Parse the EXTINF line
            // Format: #EXTINF:-1 tvg-id="..." tvg-logo="..." group-title="..." ,NAME
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
        } else if !line.is_empty() && !line.starts_with('#') {
            // This is a URL line
            if let Some((name, logo, group, tvg_id)) = current_meta.take() {
                let entry_type = classify_entry(&group).to_string();
                entries.push(M3uEntry {
                    name,
                    url: line.to_string(),
                    logo,
                    group,
                    tvg_id,
                    entry_type,
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
) -> bool {
    // Find existing media by title+type
    let media_id: Option<i64> = sqlx::query_scalar(
        "SELECT id FROM media WHERE LOWER(title) = LOWER($1) AND type = 'TV'::mediatype LIMIT 1",
    )
    .bind(name)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten();

    let media_id = match media_id {
        Some(id) => id,
        None => {
            // Insert new media
            let res: Option<(i64,)> = sqlx::query_as(
                "INSERT INTO media (title, type, created_at, adult, is_blocked, total_streams, popularity) \
                 VALUES ($1, 'TV'::mediatype, NOW(), false, false, 0, 0.0) \
                 ON CONFLICT DO NOTHING RETURNING id",
            )
            .bind(name)
            .fetch_optional(pool)
            .await
            .ok()
            .flatten();

            match res {
                Some((id,)) => id,
                None => {
                    // Conflict: fetch existing
                    match sqlx::query_scalar::<_, i64>(
                        "SELECT id FROM media WHERE LOWER(title) = LOWER($1) AND type = 'TV'::mediatype LIMIT 1",
                    )
                    .bind(name)
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

    // Insert stream row
    let stream_row: Option<(i32,)> = sqlx::query_as(
        "INSERT INTO stream (stream_type, name, source, is_active, is_blocked, is_public, playback_count, created_at, updated_at) \
         VALUES ('HTTP'::streamtype, $1, $2, true, false, true, 0, NOW(), NOW()) RETURNING id",
    )
    .bind(name)
    .bind(source_name)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten();

    let stream_id = match stream_row {
        Some((id,)) => id,
        None => return false,
    };

    // Insert http_stream row
    let hs = sqlx::query(
        "INSERT INTO http_stream (stream_id, url, stream_behavior) \
         VALUES ($1, $2, 'DIRECT'::streambehavior) ON CONFLICT (stream_id) DO NOTHING",
    )
    .bind(stream_id)
    .bind(url)
    .execute(pool)
    .await;

    if hs.is_err() {
        let _ = sqlx::query("DELETE FROM stream WHERE id = $1")
            .bind(stream_id)
            .execute(pool)
            .await;
        return false;
    }

    // Link stream to media
    let _ = sqlx::query(
        "INSERT INTO stream_media_link (stream_id, media_id, is_primary) \
         SELECT $1, $2, true WHERE NOT EXISTS \
         (SELECT 1 FROM stream_media_link WHERE stream_id = $1 AND media_id = $2)",
    )
    .bind(stream_id)
    .bind(media_id as i32)
    .execute(pool)
    .await;

    true
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

    // Preview: first 50 entries
    let preview: Vec<serde_json::Value> = entries
        .iter()
        .take(50)
        .map(|e| {
            json!({
                "name": e.name,
                "url": e.url,
                "logo": e.logo,
                "group": e.group,
                "tvg_id": e.tvg_id,
                "type": e.entry_type,
            })
        })
        .collect();

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
    let import_live = params
        .get("import_live")
        .and_then(|v| v.as_bool())
        .unwrap_or(true);

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

    let tv_entries: Vec<&M3uEntry> = if import_live {
        entries.iter().filter(|e| e.entry_type == "tv").collect()
    } else {
        vec![]
    };

    let total = tv_entries.len();

    // For >100 items, process in background and return 202
    if total > 100 {
        let job_id = Uuid::new_v4().to_string();
        let job_key = format!("import_job:{job_id}");
        let job_status = json!({
            "status": "processing",
            "progress": 0,
            "total": total,
        });
        let _ = state
            .redis
            .set::<(), _, _>(
                &job_key,
                job_status.to_string(),
                Some(Expiration::EX(86400)),
                None,
                false,
            )
            .await;

        let pool = state.pool.clone();
        let redis = state.redis.clone();
        let source = source_name.clone();
        let entries_owned: Vec<M3uEntry> = tv_entries.into_iter().cloned().collect();
        let _ = user_id;
        tokio::spawn(async move {
            let mut imported = 0usize;
            let mut skipped = 0usize;
            for (i, entry) in entries_owned.iter().enumerate() {
                if import_tv_channel(
                    &pool,
                    &entry.name,
                    &entry.url,
                    entry.logo.as_deref(),
                    &source,
                )
                .await
                {
                    imported += 1;
                } else {
                    skipped += 1;
                }
                if (i + 1) % 10 == 0 {
                    let progress = json!({
                        "status": "processing",
                        "progress": i + 1,
                        "total": entries_owned.len(),
                    });
                    let _ = redis
                        .set::<(), _, _>(
                            &job_key,
                            progress.to_string(),
                            Some(Expiration::EX(86400)),
                            None,
                            false,
                        )
                        .await;
                }
            }
            let done = json!({
                "status": "completed",
                "progress": entries_owned.len(),
                "total": entries_owned.len(),
                "stats": { "imported": imported, "skipped": skipped },
            });
            let _ = redis
                .set::<(), _, _>(
                    &job_key,
                    done.to_string(),
                    Some(Expiration::EX(86400)),
                    None,
                    false,
                )
                .await;
        });

        return (
            StatusCode::ACCEPTED,
            Json(json!({
                "status": "processing",
                "job_id": job_id,
                "total": total,
                "message": format!("Import started for {total} TV channels"),
            })),
        )
            .into_response();
    }

    // Small batch: process synchronously
    let mut imported = 0usize;
    let mut skipped = 0usize;
    for entry in &tv_entries {
        if import_tv_channel(
            &state.pool,
            &entry.name,
            &entry.url,
            entry.logo.as_deref(),
            &source_name,
        )
        .await
        {
            imported += 1;
        } else {
            skipped += 1;
        }
    }

    // Clean up Redis key
    if let Some(ref key) = redis_key {
        let _: Result<(), _> = state.redis.del(key).await;
    }

    Json(json!({
        "status": "success",
        "imported": imported,
        "skipped": skipped,
        "total": total,
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
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }

    let key = format!("import_job:{job_id}");
    let val: Option<String> = state.redis.get(&key).await.unwrap_or(None);

    match val {
        Some(json_str) => {
            let status: serde_json::Value =
                serde_json::from_str(&json_str).unwrap_or_else(|_| json!({"status": "unknown"}));
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
