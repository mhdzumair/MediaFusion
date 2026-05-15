/// Torrent / magnet import endpoints.
///
/// Routes:
///   POST /api/v1/import/magnet/analyze   → analyze_magnet
///   POST /api/v1/import/torrent/analyze  → analyze_torrent
///   POST /api/v1/import/magnet           → import_magnet
///   POST /api/v1/import/torrent          → import_torrent
///
/// The import flow:
///   1. Authenticate user
///   2. Check adult-content filter (regex on torrent name)
///   3. Enforce upload permissions (rate limit + account restriction)
///   4. Resolve uploader identity (anonymous vs. named)
///   5. Determine auto-approval (mods/admins + active non-anonymous users)
///   6. Insert stream to DB
///   7. Create contribution record
///   8. If auto-approved: award contribution points
///   9. If pending: notify moderators via Telegram
use std::sync::{Arc, OnceLock};

use axum::{
    body::Bytes,
    extract::{Multipart, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::Utc;
use hmac::{Hmac, KeyInit, Mac};
use lava_torrent::torrent::v1::Torrent as LavaTorrent;
use serde::{Deserialize, Serialize};
use serde_json::json;
use sha2::Sha256;

use crate::{parser, state::AppState};

// ─── Auth helpers ─────────────────────────────────────────────────────────────

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

use super::import_helpers::{
    award_contribution_points, create_contribution_record, enforce_upload_permissions,
    fetch_user_info, is_adult_content, notify_pending_contribution, resolve_uploader_identity,
};

// ─── Magnet URI helpers ───────────────────────────────────────────────────────

fn extract_info_hash_from_magnet(magnet: &str) -> Option<String> {
    static BTIH_RE: OnceLock<regex::Regex> = OnceLock::new();
    let re = BTIH_RE.get_or_init(|| {
        regex::Regex::new(r"xt=urn:btih:([0-9a-fA-F]{40}|[A-Z2-7]{32}|[a-z2-7]{32})").unwrap()
    });
    re.captures(magnet).and_then(|c| c.get(1)).map(|m| {
        let s = m.as_str();
        if s.len() == 32 {
            base32_to_hex(s).unwrap_or_else(|| s.to_lowercase())
        } else {
            s.to_lowercase()
        }
    })
}

fn extract_dn(magnet: &str) -> Option<String> {
    static DN_RE: OnceLock<regex::Regex> = OnceLock::new();
    let re = DN_RE.get_or_init(|| regex::Regex::new(r"[?&]dn=([^&]+)").unwrap());
    re.captures(magnet).and_then(|c| c.get(1)).map(|m| {
        urlencoding::decode(m.as_str())
            .unwrap_or_default()
            .into_owned()
    })
}

/// Decode a 32-char Base32 string (RFC 4648 alphabet, uppercase) to a 40-char hex string.
fn base32_to_hex(s: &str) -> Option<String> {
    let upper = s.to_uppercase();
    let bytes = upper.as_bytes();
    if bytes.len() != 32 {
        return None;
    }
    // Each Base32 char encodes 5 bits; 32 chars = 160 bits = 20 bytes
    let mut bits: u64 = 0;
    let mut bit_count = 0u32;
    let mut out = Vec::with_capacity(20);
    for &b in bytes {
        let val: u8 = match b {
            b'A'..=b'Z' => b - b'A',
            b'2'..=b'7' => b - b'2' + 26,
            _ => return None,
        };
        bits = (bits << 5) | (val as u64);
        bit_count += 5;
        if bit_count >= 8 {
            bit_count -= 8;
            out.push(((bits >> bit_count) & 0xFF) as u8);
        }
    }
    if out.len() != 20 {
        return None;
    }
    Some(out.iter().map(|b| format!("{b:02x}")).collect())
}

// ─── Media search helper ──────────────────────────────────────────────────────

#[derive(Serialize, Clone)]
struct MediaMatch {
    media_id: i64,
    title: String,
    year: Option<i32>,
}

async fn search_media(pool: &sqlx::PgPool, title: &str, meta_type: &str) -> Vec<MediaMatch> {
    let pattern = format!("%{title}%");
    let type_upper = meta_type.to_uppercase();
    let rows: Vec<(i32, String, Option<i32>)> = sqlx::query_as(
        "SELECT id, title, year FROM media WHERE LOWER(title) LIKE LOWER($1) AND UPPER(type::text) = $2 LIMIT 5",
    )
    .bind(&pattern)
    .bind(&type_upper)
    .fetch_all(pool)
    .await
    .unwrap_or_default();

    rows.into_iter()
        .map(|(id, title, year)| MediaMatch {
            media_id: id as i64,
            title,
            year,
        })
        .collect()
}

async fn resolve_media_id(
    pool: &sqlx::PgPool,
    meta_id: &str,
    meta_type: &str,
    parsed_title: &str,
    parsed_year: Option<i32>,
) -> Option<i64> {
    // Try lookup by external ID first (imdb, tmdb, etc.)
    let row: Option<(i32,)> =
        sqlx::query_as("SELECT media_id FROM media_external_id WHERE external_id = $1 LIMIT 1")
            .bind(meta_id)
            .fetch_optional(pool)
            .await
            .unwrap_or(None);

    if let Some((id,)) = row {
        return Some(id as i64);
    }

    // Fall back to title + year search
    let type_upper = meta_type.to_uppercase();
    if let Some(year) = parsed_year {
        let row: Option<(i32,)> = sqlx::query_as(
            "SELECT id FROM media WHERE LOWER(title) = LOWER($1) AND year = $2 AND UPPER(type::text) = $3 LIMIT 1",
        )
        .bind(parsed_title)
        .bind(year)
        .bind(&type_upper)
        .fetch_optional(pool)
        .await
        .unwrap_or(None);
        if let Some((id,)) = row {
            return Some(id as i64);
        }
    }

    // Broader title search
    let pattern = format!("%{parsed_title}%");
    let row: Option<(i32,)> = sqlx::query_as(
        "SELECT id FROM media WHERE LOWER(title) LIKE LOWER($1) AND UPPER(type::text) = $2 LIMIT 1",
    )
    .bind(&pattern)
    .bind(&type_upper)
    .fetch_optional(pool)
    .await
    .unwrap_or(None);

    row.map(|(id,)| id as i64)
}

// ─── Tracker / language / file DB helpers ────────────────────────────────────

/// Extract tracker URLs from a magnet URI.
fn extract_trackers_from_magnet(magnet: &str) -> Vec<String> {
    magnet
        .split('&')
        .filter_map(|part| {
            let part = part.trim_start_matches("magnet:?");
            if let Some(val) = part.strip_prefix("tr=") {
                urlencoding::decode(val)
                    .ok()
                    .map(|s| s.into_owned())
                    .filter(|s| !s.is_empty())
            } else {
                None
            }
        })
        .collect()
}

/// Upsert tracker URLs and link them to the torrent stream row.
async fn insert_trackers(
    pool: &sqlx::PgPool,
    torrent_stream_id: i32,
    tracker_urls: &[String],
) -> Result<(), sqlx::Error> {
    for url in tracker_urls {
        let tracker_id: Option<i32> = sqlx::query_scalar(
            "INSERT INTO tracker(url) VALUES($1) ON CONFLICT(url) DO UPDATE SET url = EXCLUDED.url RETURNING id",
        )
        .bind(url)
        .fetch_optional(pool)
        .await?;

        if let Some(tid) = tracker_id {
            sqlx::query(
                "INSERT INTO torrent_tracker_link(torrent_id, tracker_id) VALUES($1, $2) ON CONFLICT DO NOTHING",
            )
            .bind(torrent_stream_id)
            .bind(tid)
            .execute(pool)
            .await?;
        }
    }
    Ok(())
}

/// Insert audio language links for a stream.
async fn insert_languages(
    pool: &sqlx::PgPool,
    stream_id: i64,
    languages: &[String],
) -> Result<(), sqlx::Error> {
    for lang in languages {
        if lang.is_empty() {
            continue;
        }
        let lang_id: Option<i32> = sqlx::query_scalar(
            "INSERT INTO language(name) VALUES($1) ON CONFLICT(name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        )
        .bind(lang)
        .fetch_optional(pool)
        .await?;

        if let Some(lid) = lang_id {
            sqlx::query(
                "INSERT INTO stream_language_link(stream_id, language_id, language_type) VALUES($1, $2, 'audio') ON CONFLICT DO NOTHING",
            )
            .bind(stream_id as i32)
            .bind(lid)
            .execute(pool)
            .await?;
        }
    }
    Ok(())
}

/// Insert per-file metadata (stream_file + file_media_link) for a stream.
async fn insert_file_data(
    pool: &sqlx::PgPool,
    stream_id: i64,
    media_id: Option<i64>,
    files: &[FileEntry],
) -> Result<(), sqlx::Error> {
    for f in files {
        let file_id: Option<i32> = sqlx::query_scalar(
            r#"INSERT INTO stream_file(stream_id, file_index, filename, size, file_type)
               VALUES($1, $2, $3, $4, 'video')
               ON CONFLICT DO NOTHING
               RETURNING id"#,
        )
        .bind(stream_id as i32)
        .bind(f.index)
        .bind(&f.filename)
        .bind(f.size)
        .fetch_optional(pool)
        .await?;

        if let (Some(fid), Some(mid), Some(s), Some(e)) =
            (file_id, media_id, f.season_number, f.episode_number)
        {
            sqlx::query(
                r#"INSERT INTO file_media_link(file_id, media_id, season_number, episode_number)
                   VALUES($1, $2, $3, $4)
                   ON CONFLICT DO NOTHING"#,
            )
            .bind(fid)
            .bind(mid as i32)
            .bind(s)
            .bind(e)
            .execute(pool)
            .await?;
        }
    }
    Ok(())
}

// ─── DB insert helper ─────────────────────────────────────────────────────────

#[allow(clippy::too_many_arguments)]
async fn insert_torrent_stream(
    pool: &sqlx::PgPool,
    info_hash: &str,
    name: &str,
    source: &str,
    size: Option<i64>,
    seeders: Option<i32>,
    file_count: i32,
    parsed: &parser::ParsedTitle,
    media_id: Option<i64>,
) -> Result<i64, sqlx::Error> {
    let mut txn = pool.begin().await?;

    let stream_id: i64 = sqlx::query_scalar(
        r#"INSERT INTO stream(
               stream_type, name, source, resolution, codec, quality,
               is_proper, is_repack, is_remastered, is_upscaled, is_extended, is_complete, is_dubbed, release_group,
               is_active, is_blocked, is_public, playback_count, created_at
           ) VALUES(
               'TORRENT'::streamtype, $1, $2, $3, $4, $5,
               $6, $7, $8, $9, $10, $11, $12, $13,
               true, false, true, 0, NOW()
           )
           RETURNING id"#,
    )
    .bind(name)
    .bind(source)
    .bind(parsed.resolution.as_deref())
    .bind(parsed.codec.as_deref())
    .bind(parsed.quality.as_deref())
    .bind(parsed.is_proper)
    .bind(parsed.is_repack)
    .bind(parsed.is_remastered)
    .bind(parsed.is_upscaled)
    .bind(parsed.is_extended)
    .bind(parsed.is_complete)
    .bind(parsed.is_dubbed)
    .bind(parsed.release_group.as_deref())
    .fetch_one(&mut *txn)
    .await?;

    let ts_result = sqlx::query(
        r#"INSERT INTO torrent_stream(stream_id, info_hash, total_size, seeders, torrent_type, file_count, created_at)
           VALUES($1, $2, $3, $4, 'PUBLIC'::torrenttype, $5, NOW())
           ON CONFLICT (info_hash) DO NOTHING"#,
    )
    .bind(stream_id as i32)
    .bind(info_hash)
    .bind(size)
    .bind(seeders)
    .bind(file_count)
    .execute(&mut *txn)
    .await;

    if let Ok(r) = &ts_result {
        if r.rows_affected() == 0 {
            sqlx::query("DELETE FROM stream WHERE id = $1")
                .bind(stream_id as i32)
                .execute(&mut *txn)
                .await
                .ok();
            txn.commit().await?;
            // Return the existing stream_id
            let existing: i64 =
                sqlx::query_scalar("SELECT stream_id FROM torrent_stream WHERE info_hash = $1")
                    .bind(info_hash)
                    .fetch_one(pool)
                    .await
                    .unwrap_or(stream_id);
            return Ok(existing);
        }
    }
    ts_result?;

    if let Some(mid) = media_id {
        sqlx::query(
            r#"INSERT INTO stream_media_link(stream_id, media_id, is_primary)
               SELECT $1, $2, true
               WHERE NOT EXISTS (
                   SELECT 1 FROM stream_media_link WHERE stream_id = $1 AND media_id = $2
               )"#,
        )
        .bind(stream_id as i32)
        .bind(mid as i32)
        .execute(&mut *txn)
        .await
        .ok();

        sqlx::query("UPDATE media SET total_streams = total_streams + 1 WHERE id = $1")
            .bind(mid as i32)
            .execute(&mut *txn)
            .await
            .ok();
    }

    txn.commit().await?;
    Ok(stream_id)
}

// ─── Request / response shapes ────────────────────────────────────────────────

/// Per-file metadata passed from the UI after torrent analysis.
#[derive(Deserialize, Clone)]
pub struct FileEntry {
    pub index: i32,
    pub filename: String,
    pub size: i64,
    pub season_number: Option<i32>,
    pub episode_number: Option<i32>,
}

#[derive(Deserialize)]
pub struct MagnetAnalyzeRequest {
    magnet_link: String,
    meta_type: Option<String>,
    meta_id: Option<String>,
    title: Option<String>,
    /// If true, contact DHT peers to fetch the full file list via BEP-9.
    /// Adds latency (up to `resolve_timeout_secs`); omit for fast analysis.
    #[serde(default)]
    resolve_files: bool,
    /// Max seconds for DHT resolution (default 30, clamped 5–60).
    resolve_timeout_secs: Option<u64>,
}

// MagnetImportRequest was replaced by multipart form parsing in import_magnet.

// ─── Handlers ─────────────────────────────────────────────────────────────────

pub async fn analyze_magnet(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(body): Json<MagnetAnalyzeRequest>,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }

    let info_hash = match extract_info_hash_from_magnet(&body.magnet_link) {
        Some(h) => h,
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Invalid magnet link: could not extract info_hash"})),
            )
                .into_response();
        }
    };

    let already_exists: bool =
        sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM torrent_stream WHERE info_hash = $1)")
            .bind(&info_hash)
            .fetch_one(&state.pool)
            .await
            .unwrap_or(false);

    let torrent_name = extract_dn(&body.magnet_link)
        .or_else(|| body.title.clone())
        .unwrap_or_default();

    // Adult content filter — block at analyze time so the UI never proceeds
    if !torrent_name.is_empty() && is_adult_content(&torrent_name) {
        return (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({"status": "error", "error": "Adult content is not allowed"})),
        )
            .into_response();
    }

    let parsed = parser::parse_title(&torrent_name);

    let meta_type = body.meta_type.as_deref().unwrap_or("movie");
    let search_title = parsed.title.as_deref().unwrap_or(&torrent_name);
    let matches = search_media(&state.pool, search_title, meta_type).await;

    // When caller provides meta_id, resolve and return the specific media match
    let meta_match: Option<MediaMatch> = if let Some(ref mid) = body.meta_id {
        if !mid.is_empty() {
            resolve_media_id(&state.pool, mid, meta_type, search_title, parsed.year)
                .await
                .and_then(|id| matches.iter().find(|m| m.media_id == id).cloned())
        } else {
            None
        }
    } else {
        None
    };

    // Optional: contact DHT to fetch the full file list via BEP-9.
    // Only triggered when the caller explicitly requests it.
    let resolved_files: Option<serde_json::Value> = if body.resolve_files {
        let secs = body.resolve_timeout_secs.unwrap_or(30).clamp(5, 60);
        match crate::demagnetize::resolve(&info_hash, std::time::Duration::from_secs(secs)).await {
            Ok(meta) => Some(json!({
                "name":       meta.name,
                "total_size": meta.total_size,
                "num_files":  meta.files.len(),
                "files": meta.files.iter()
                    .map(|f| json!({"path": f.path, "size": f.size}))
                    .collect::<Vec<_>>(),
            })),
            Err(e) => {
                tracing::warn!("demagnetize {info_hash}: {e}");
                Some(json!({"error": e.to_string()}))
            }
        }
    } else {
        None
    };

    (
        StatusCode::OK,
        Json(json!({
            "status": "success",
            "info_hash": info_hash,
            "torrent_name": torrent_name,
            "already_exists": already_exists,
            "parsed_title": parsed.title,
            "year": parsed.year,
            "resolution": parsed.resolution,
            "quality": parsed.quality,
            "codec": parsed.codec,
            "languages": parsed.languages,
            "matches": matches,
            "meta_match": meta_match,
            "resolved": resolved_files,
        })),
    )
        .into_response()
}

pub async fn analyze_torrent(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    mut multipart: Multipart,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }

    let mut file_bytes: Option<Bytes> = None;
    let mut meta_type = String::from("movie");

    while let Ok(Some(field)) = multipart.next_field().await {
        match field.name() {
            Some("torrent_file") | Some("file") => {
                file_bytes = field.bytes().await.ok();
            }
            Some("meta_type") => {
                meta_type = field.text().await.unwrap_or_else(|_| "movie".into());
            }
            _ => {}
        }
    }

    let bytes = match file_bytes {
        Some(b) => b,
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Missing 'torrent_file' field"})),
            )
                .into_response();
        }
    };

    let torrent: LavaTorrent = match LavaTorrent::read_from_bytes(bytes.as_ref()) {
        Ok(t) => t,
        Err(e) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": format!("Failed to parse .torrent: {e}")})),
            )
                .into_response();
        }
    };

    let info_hash = torrent
        .info_hash_bytes()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect::<String>();

    let name = torrent.name.clone();
    let total_size = torrent.length;
    let file_count = torrent.files.as_ref().map(|f| f.len()).unwrap_or(1) as i32;

    let files: Vec<serde_json::Value> = torrent
        .files
        .as_ref()
        .map(|fs: &Vec<lava_torrent::torrent::v1::File>| {
            fs.iter()
                .map(|f| {
                    json!({
                        "path": f.path.to_string_lossy(),
                        "size": f.length,
                    })
                })
                .collect()
        })
        .unwrap_or_default();

    let already_exists: bool =
        sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM torrent_stream WHERE info_hash = $1)")
            .bind(&info_hash)
            .fetch_one(&state.pool)
            .await
            .unwrap_or(false);

    // Adult content filter — block at analyze time so the UI never proceeds
    if is_adult_content(&name) {
        return (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({"status": "error", "error": "Adult content is not allowed"})),
        )
            .into_response();
    }

    let parsed = parser::parse_title(&name);
    let search_title = parsed.title.as_deref().unwrap_or(&name);
    let matches = search_media(&state.pool, search_title, &meta_type).await;

    (
        StatusCode::OK,
        Json(json!({
            "status": "success",
            "info_hash": info_hash,
            "torrent_name": name,
            "already_exists": already_exists,
            "file_count": file_count,
            "total_size": total_size,
            "files": files,
            "parsed_title": parsed.title,
            "year": parsed.year,
            "resolution": parsed.resolution,
            "quality": parsed.quality,
            "codec": parsed.codec,
            "languages": parsed.languages,
            "matches": matches,
        })),
    )
        .into_response()
}

pub async fn import_magnet(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
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

    // ── Parse multipart fields ────────────────────────────────────────────────
    let mut magnet_link = String::new();
    let mut meta_type = String::from("movie");
    let mut meta_id: Option<String> = None;
    let mut title: Option<String> = None;
    let mut resolution: Option<String> = None;
    let mut quality: Option<String> = None;
    let mut codec: Option<String> = None;
    let mut languages: Vec<String> = Vec::new();
    let mut catalogs: Vec<String> = Vec::new();
    let mut force_import = false;
    let mut file_data: Vec<FileEntry> = Vec::new();
    let mut is_anonymous_field: Option<bool> = None;
    let mut anonymous_display_name: Option<String> = None;

    while let Ok(Some(field)) = multipart.next_field().await {
        match field.name() {
            Some("magnet_link") => {
                magnet_link = field.text().await.unwrap_or_default();
            }
            Some("meta_type") => {
                meta_type = field.text().await.unwrap_or_else(|_| "movie".into());
            }
            Some("meta_id") => {
                meta_id = field.text().await.ok().filter(|s| !s.is_empty());
            }
            Some("title") => {
                title = field.text().await.ok().filter(|s| !s.is_empty());
            }
            Some("resolution") => {
                resolution = field.text().await.ok().filter(|s| !s.is_empty());
            }
            Some("quality") => {
                quality = field.text().await.ok().filter(|s| !s.is_empty());
            }
            Some("codec") => {
                codec = field.text().await.ok().filter(|s| !s.is_empty());
            }
            Some("languages") => {
                if let Ok(raw) = field.text().await {
                    languages = raw
                        .split(',')
                        .map(|s| s.trim().to_string())
                        .filter(|s| !s.is_empty())
                        .collect();
                }
            }
            Some("catalogs") => {
                if let Ok(raw) = field.text().await {
                    catalogs = raw
                        .split(',')
                        .map(|s| s.trim().to_string())
                        .filter(|s| !s.is_empty())
                        .collect();
                }
            }
            Some("force_import") => {
                force_import = field
                    .text()
                    .await
                    .ok()
                    .map(|v| v == "true" || v == "1")
                    .unwrap_or(false);
            }
            Some("file_data") => {
                if let Ok(raw) = field.text().await {
                    file_data =
                        serde_json::from_str::<Vec<FileEntry>>(&raw).unwrap_or_default();
                }
            }
            Some("is_anonymous") => {
                if let Ok(raw) = field.text().await {
                    is_anonymous_field = Some(raw == "true" || raw == "1");
                }
            }
            Some("anonymous_display_name") => {
                anonymous_display_name =
                    field.text().await.ok().filter(|s| !s.is_empty());
            }
            _ => {}
        }
    }

    if magnet_link.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Missing magnet_link field"})),
        )
            .into_response();
    }

    let info_hash = match extract_info_hash_from_magnet(&magnet_link) {
        Some(h) => h,
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Invalid magnet link: could not extract info_hash"})),
            )
                .into_response();
        }
    };

    let torrent_name = extract_dn(&magnet_link)
        .or_else(|| title.clone())
        .unwrap_or_default();
    let name_for_parse = if torrent_name.is_empty() {
        title.as_deref().unwrap_or(&info_hash).to_string()
    } else {
        torrent_name.clone()
    };

    // Adult content filter
    if is_adult_content(&name_for_parse) {
        return (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({"detail": "Adult content is not allowed"})),
        )
            .into_response();
    }

    // Upload permission guard
    if let Err((status, msg)) = enforce_upload_permissions(
        &state.pool_ro,
        &state.redis,
        user_id,
        user.uploads_restricted,
        &user.role,
    )
    .await
    {
        return (status, Json(json!({"detail": msg}))).into_response();
    }

    // Check duplicate
    let existing_id: Option<i64> = sqlx::query_scalar(
        "SELECT ts.stream_id FROM torrent_stream ts WHERE ts.info_hash = $1 LIMIT 1",
    )
    .bind(&info_hash)
    .fetch_optional(&state.pool)
    .await
    .unwrap_or(None);

    if let Some(sid) = existing_id {
        if !force_import {
            return (
                StatusCode::CONFLICT,
                Json(json!({
                    "status": "exists",
                    "message": "Stream already exists",
                    "import_id": sid,
                })),
            )
                .into_response();
        }
    }

    let mut parsed = parser::parse_title(&name_for_parse);
    // Allow caller to override parser-detected values
    if let Some(ref r) = resolution {
        if !r.is_empty() {
            parsed.resolution = Some(r.clone());
        }
    }
    if let Some(ref q) = quality {
        if !q.is_empty() {
            parsed.quality = Some(q.clone());
        }
    }
    if let Some(ref c) = codec {
        if !c.is_empty() {
            parsed.codec = Some(c.clone());
        }
    }

    let media_id = if let Some(ref mid) = meta_id {
        if !mid.is_empty() {
            resolve_media_id(
                &state.pool,
                mid,
                &meta_type,
                parsed.title.as_deref().unwrap_or(&name_for_parse),
                parsed.year,
            )
            .await
        } else {
            None
        }
    } else {
        None
    };

    // Resolve uploader identity
    let resolved_is_anonymous = is_anonymous_field.unwrap_or(user.contribute_anonymously);
    let (uploader_name, uploader_user_id) = resolve_uploader_identity(
        resolved_is_anonymous,
        anonymous_display_name.as_deref(),
        &user.username,
        user_id,
    );

    let is_privileged = matches!(user.role.as_str(), "moderator" | "admin");
    let auto_approve = is_privileged || !resolved_is_anonymous;

    let source = meta_id.as_deref().unwrap_or("manual").to_string();
    let file_count = if !file_data.is_empty() {
        file_data.len() as i32
    } else {
        1
    };

    let stream_id = match insert_torrent_stream(
        &state.pool,
        &info_hash,
        &torrent_name,
        &source,
        None,
        None,
        file_count,
        &parsed,
        media_id,
    )
    .await
    {
        Ok(sid) => sid,
        Err(e) => {
            tracing::error!("import_magnet DB error: {e}");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "Database error"})),
            )
                .into_response();
        }
    };

    // Insert trackers from magnet URI
    let trackers = extract_trackers_from_magnet(&magnet_link);
    if !trackers.is_empty() {
        let ts_id: Option<i32> =
            sqlx::query_scalar("SELECT id FROM torrent_stream WHERE stream_id = $1 LIMIT 1")
                .bind(stream_id as i32)
                .fetch_optional(&state.pool)
                .await
                .unwrap_or(None);
        if let Some(tsid) = ts_id {
            insert_trackers(&state.pool, tsid, &trackers).await.ok();
        }
    }

    // Insert language links
    if !languages.is_empty() {
        insert_languages(&state.pool, stream_id, &languages).await.ok();
    }

    // Insert per-file metadata
    if !file_data.is_empty() {
        insert_file_data(&state.pool, stream_id, media_id, &file_data)
            .await
            .ok();
    }

    // Link media to caller-specified catalogs
    if let Some(mid) = media_id {
        for cat_name in &catalogs {
            if cat_name.is_empty() {
                continue;
            }
            let cat_id: Option<i32> = sqlx::query_scalar(
                "INSERT INTO catalog(name) VALUES($1) ON CONFLICT(name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
            )
            .bind(cat_name)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);
            if let Some(cid) = cat_id {
                let _ = sqlx::query(
                    "INSERT INTO media_catalog_link(media_id, catalog_id) VALUES($1, $2) ON CONFLICT DO NOTHING",
                )
                .bind(mid as i32)
                .bind(cid)
                .execute(&state.pool)
                .await;
            }
        }
    }

    // Create contribution record
    let contribution_data = json!({
        "info_hash": info_hash,
        "name": torrent_name,
        "meta_type": meta_type,
        "uploader_name": uploader_name,
        "is_anonymous": resolved_is_anonymous,
        "is_public": auto_approve,
    });
    if let Ok(contrib_id) = create_contribution_record(
        &state.pool,
        uploader_user_id,
        "torrent",
        meta_id.as_deref(),
        &contribution_data,
        auto_approve,
        is_privileged,
    )
    .await
    {
        if auto_approve {
            if let Some(uid) = uploader_user_id {
                award_contribution_points(&state.pool, uid).await;
            }
        } else {
            if let (Some(bot_token), Some(chat_id)) = (
                &state.config.telegram_bot_token,
                &state.config.telegram_chat_id,
            ) {
                notify_pending_contribution(
                    &state.http,
                    bot_token,
                    chat_id,
                    &state.config.host_url,
                    "torrent",
                    &uploader_name,
                    &contribution_data,
                )
                .await;
            }
        }
        tracing::debug!("contribution created: {contrib_id}");
    }

    (
        StatusCode::CREATED,
        Json(json!({
            "status": "success",
            "message": "Stream imported successfully",
            "import_id": stream_id,
            "auto_approved": auto_approve,
        })),
    )
        .into_response()
}

pub async fn import_torrent(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
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

    let mut file_bytes: Option<Bytes> = None;
    let mut meta_type = String::from("movie");
    let mut meta_id: Option<String> = None;
    let mut title: Option<String> = None;
    let mut resolution: Option<String> = None;
    let mut quality: Option<String> = None;
    let mut codec: Option<String> = None;
    let mut languages: Vec<String> = Vec::new();
    let mut catalogs: Vec<String> = Vec::new();
    let mut force_import = false;
    let mut file_data: Vec<FileEntry> = Vec::new();
    let mut is_anonymous_field: Option<bool> = None;
    let mut anonymous_display_name: Option<String> = None;

    while let Ok(Some(field)) = multipart.next_field().await {
        match field.name() {
            Some("torrent_file") | Some("file") => {
                file_bytes = field.bytes().await.ok();
            }
            Some("meta_type") => {
                meta_type = field.text().await.unwrap_or_else(|_| "movie".into());
            }
            Some("meta_id") => {
                meta_id = field.text().await.ok().filter(|s| !s.is_empty());
            }
            Some("title") => {
                title = field.text().await.ok().filter(|s| !s.is_empty());
            }
            Some("resolution") => {
                resolution = field.text().await.ok().filter(|s| !s.is_empty());
            }
            Some("quality") => {
                quality = field.text().await.ok().filter(|s| !s.is_empty());
            }
            Some("codec") => {
                codec = field.text().await.ok().filter(|s| !s.is_empty());
            }
            Some("force_import") => {
                force_import = field
                    .text()
                    .await
                    .ok()
                    .map(|v| v == "true" || v == "1")
                    .unwrap_or(false);
            }
            Some("languages") => {
                if let Ok(raw) = field.text().await {
                    languages = raw
                        .split(',')
                        .map(|s| s.trim().to_string())
                        .filter(|s| !s.is_empty())
                        .collect();
                }
            }
            Some("catalogs") => {
                if let Ok(raw) = field.text().await {
                    catalogs = raw
                        .split(',')
                        .map(|s| s.trim().to_string())
                        .filter(|s| !s.is_empty())
                        .collect();
                }
            }
            Some("file_data") => {
                if let Ok(raw) = field.text().await {
                    file_data = serde_json::from_str::<Vec<FileEntry>>(&raw).unwrap_or_default();
                }
            }
            Some("is_anonymous") => {
                if let Ok(raw) = field.text().await {
                    is_anonymous_field = Some(raw == "true" || raw == "1");
                }
            }
            Some("anonymous_display_name") => {
                anonymous_display_name = field.text().await.ok().filter(|s| !s.is_empty());
            }
            _ => {}
        }
    }

    let bytes = match file_bytes {
        Some(b) => b,
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Missing 'torrent_file' field"})),
            )
                .into_response();
        }
    };

    let torrent: LavaTorrent = match LavaTorrent::read_from_bytes(bytes.as_ref()) {
        Ok(t) => t,
        Err(e) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": format!("Failed to parse .torrent: {e}")})),
            )
                .into_response();
        }
    };

    let info_hash = torrent
        .info_hash_bytes()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect::<String>();

    let torrent_name = title.unwrap_or_else(|| torrent.name.clone());
    let total_size: i64 = torrent.length;
    let file_count = torrent
        .files
        .as_ref()
        .map(|f: &Vec<lava_torrent::torrent::v1::File>| f.len())
        .unwrap_or(1) as i32;

    // Adult content filter
    if is_adult_content(&torrent_name) {
        return (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({"detail": "Adult content is not allowed"})),
        )
            .into_response();
    }

    // Upload permission guard
    if let Err((status, msg)) = enforce_upload_permissions(
        &state.pool_ro,
        &state.redis,
        user_id,
        user.uploads_restricted,
        &user.role,
    )
    .await
    {
        return (status, Json(json!({"detail": msg}))).into_response();
    }

    // Check duplicate
    let existing_id: Option<i64> = sqlx::query_scalar(
        "SELECT ts.stream_id FROM torrent_stream ts WHERE ts.info_hash = $1 LIMIT 1",
    )
    .bind(&info_hash)
    .fetch_optional(&state.pool)
    .await
    .unwrap_or(None);

    if let Some(sid) = existing_id {
        if !force_import {
            return (
                StatusCode::CONFLICT,
                Json(json!({
                    "status": "exists",
                    "message": "Stream already exists",
                    "import_id": sid,
                })),
            )
                .into_response();
        }
    }

    let mut parsed = parser::parse_title(&torrent_name);
    // Allow caller to override parser-detected values
    if let Some(ref r) = resolution {
        if !r.is_empty() {
            parsed.resolution = Some(r.clone());
        }
    }
    if let Some(ref q) = quality {
        if !q.is_empty() {
            parsed.quality = Some(q.clone());
        }
    }
    if let Some(ref c) = codec {
        if !c.is_empty() {
            parsed.codec = Some(c.clone());
        }
    }

    let media_id = if let Some(mid) = &meta_id {
        resolve_media_id(
            &state.pool,
            mid,
            &meta_type,
            parsed.title.as_deref().unwrap_or(&torrent_name),
            parsed.year,
        )
        .await
    } else {
        None
    };

    // Resolve uploader identity
    let resolved_is_anonymous = is_anonymous_field.unwrap_or(user.contribute_anonymously);
    let (uploader_name, uploader_user_id) = resolve_uploader_identity(
        resolved_is_anonymous,
        anonymous_display_name.as_deref(),
        &user.username,
        user_id,
    );

    let is_privileged = matches!(user.role.as_str(), "moderator" | "admin");
    let auto_approve = is_privileged || !resolved_is_anonymous;

    let source = meta_id.as_deref().unwrap_or("manual").to_string();

    // Extract trackers from .torrent announce fields
    let mut tracker_urls: Vec<String> = Vec::new();
    if let Some(announce) = &torrent.announce {
        if !announce.is_empty() {
            tracker_urls.push(announce.clone());
        }
    }
    if let Some(list) = &torrent.announce_list {
        for tier in list {
            for url in tier {
                if !url.is_empty() && !tracker_urls.contains(url) {
                    tracker_urls.push(url.clone());
                }
            }
        }
    }

    let stream_id = match insert_torrent_stream(
        &state.pool,
        &info_hash,
        &torrent_name,
        &source,
        if total_size > 0 {
            Some(total_size)
        } else {
            None
        },
        None,
        file_count,
        &parsed,
        media_id,
    )
    .await
    {
        Ok(sid) => sid,
        Err(e) => {
            tracing::error!("import_torrent DB error: {e}");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "Database error"})),
            )
                .into_response();
        }
    };

    // Insert trackers
    if !tracker_urls.is_empty() {
        let ts_id: Option<i32> =
            sqlx::query_scalar("SELECT id FROM torrent_stream WHERE stream_id = $1 LIMIT 1")
                .bind(stream_id as i32)
                .fetch_optional(&state.pool)
                .await
                .unwrap_or(None);
        if let Some(tsid) = ts_id {
            insert_trackers(&state.pool, tsid, &tracker_urls).await.ok();
        }
    }

    // Insert language links
    if !languages.is_empty() {
        insert_languages(&state.pool, stream_id, &languages)
            .await
            .ok();
    }

    // Insert per-file metadata (provided by UI) or auto-detect from .torrent file list
    let effective_files: Vec<FileEntry> = if !file_data.is_empty() {
        file_data
    } else if let Some(fs) = &torrent.files {
        fs.iter()
            .enumerate()
            .filter_map(|(i, f)| {
                let filename = f.path.file_name()?.to_string_lossy().into_owned();
                if crate::parser::episode_detector::is_video_file(&filename) {
                    Some(FileEntry {
                        index: i as i32,
                        filename,
                        size: f.length,
                        season_number: None,
                        episode_number: None,
                    })
                } else {
                    None
                }
            })
            .collect()
    } else {
        Vec::new()
    };

    if !effective_files.is_empty() {
        insert_file_data(&state.pool, stream_id, media_id, &effective_files)
            .await
            .ok();
    }

    // Link media to caller-specified catalogs
    if let Some(mid) = media_id {
        for cat_name in &catalogs {
            if cat_name.is_empty() {
                continue;
            }
            let cat_id: Option<i32> = sqlx::query_scalar(
                "INSERT INTO catalog(name) VALUES($1) ON CONFLICT(name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
            )
            .bind(cat_name)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);
            if let Some(cid) = cat_id {
                let _ = sqlx::query(
                    "INSERT INTO media_catalog_link(media_id, catalog_id) VALUES($1, $2) ON CONFLICT DO NOTHING",
                )
                .bind(mid as i32)
                .bind(cid)
                .execute(&state.pool)
                .await;
            }
        }
    }

    // Create contribution record
    let contribution_data = json!({
        "info_hash": info_hash,
        "name": torrent_name,
        "meta_type": meta_type,
        "uploader_name": uploader_name,
        "is_anonymous": resolved_is_anonymous,
        "is_public": auto_approve,
    });
    if let Ok(contrib_id) = create_contribution_record(
        &state.pool,
        uploader_user_id,
        "torrent",
        meta_id.as_deref(),
        &contribution_data,
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
            &state.config.telegram_bot_token,
            &state.config.telegram_chat_id,
        ) {
            notify_pending_contribution(
                &state.http,
                bot_token,
                chat_id,
                &state.config.host_url,
                "torrent",
                &uploader_name,
                &contribution_data,
            )
            .await;
        }
        tracing::debug!("contribution created: {contrib_id}");
    }

    (
        StatusCode::CREATED,
        Json(json!({
            "status": "success",
            "message": "Stream imported successfully",
            "import_id": stream_id,
            "auto_approved": auto_approve,
        })),
    )
        .into_response()
}
