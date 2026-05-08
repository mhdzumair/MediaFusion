/// Torrent / magnet import endpoints.
///
/// Routes:
///   POST /api/v1/import/magnet/analyze   → analyze_magnet
///   POST /api/v1/import/torrent/analyze  → analyze_torrent
///   POST /api/v1/import/magnet           → import_magnet
///   POST /api/v1/import/torrent          → import_torrent

use std::sync::{Arc, OnceLock};

use axum::{
    body::Bytes,
    extract::{Multipart, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use lava_torrent::torrent::v1::Torrent as LavaTorrent;
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::Utc;
use hmac::{Hmac, Mac};
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

// ─── Magnet URI helpers ───────────────────────────────────────────────────────

fn extract_info_hash_from_magnet(magnet: &str) -> Option<String> {
    static BTIH_RE: OnceLock<regex::Regex> = OnceLock::new();
    let re = BTIH_RE.get_or_init(|| {
        regex::Regex::new(r"xt=urn:btih:([0-9a-fA-F]{40}|[A-Z2-7]{32}|[a-z2-7]{32})")
            .unwrap()
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
    re.captures(magnet)
        .and_then(|c| c.get(1))
        .map(|m| urlencoding::decode(m.as_str()).unwrap_or_default().into_owned())
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

#[derive(Serialize)]
struct MediaMatch {
    media_id: i64,
    title: String,
    year: Option<i32>,
}

async fn search_media(
    pool: &sqlx::PgPool,
    title: &str,
    meta_type: &str,
) -> Vec<MediaMatch> {
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
    let row: Option<(i32,)> = sqlx::query_as(
        "SELECT media_id FROM media_external_id WHERE external_id = $1 LIMIT 1",
    )
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

// ─── DB insert helper ─────────────────────────────────────────────────────────

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
               is_proper, is_repack, is_extended, is_complete, is_dubbed, release_group,
               is_active, is_blocked, is_public, playback_count, created_at
           ) VALUES(
               'TORRENT'::streamtype, $1, $2, $3, $4, $5,
               $6, $7, $8, $9, $10, $11,
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
            let existing: i64 = sqlx::query_scalar(
                "SELECT stream_id FROM torrent_stream WHERE info_hash = $1",
            )
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

#[derive(Deserialize)]
pub struct MagnetAnalyzeRequest {
    magnet_link: String,
    meta_type: Option<String>,
    #[allow(dead_code)]
    meta_id: Option<String>,
    #[allow(dead_code)]
    title: Option<String>,
}

#[derive(Deserialize)]
pub struct MagnetImportRequest {
    magnet_link: String,
    meta_type: Option<String>,
    meta_id: Option<String>,
    title: Option<String>,
    #[allow(dead_code)]
    catalogs: Option<Vec<String>>,
    #[allow(dead_code)]
    languages: Option<Vec<String>>,
    #[allow(dead_code)]
    resolution: Option<String>,
    #[allow(dead_code)]
    quality: Option<String>,
    #[allow(dead_code)]
    codec: Option<String>,
    force_import: Option<bool>,
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

pub async fn analyze_magnet(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(body): Json<MagnetAnalyzeRequest>,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::UNAUTHORIZED, Json(json!({"detail": "Unauthorized"}))).into_response();
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

    let already_exists: bool = sqlx::query_scalar(
        "SELECT EXISTS(SELECT 1 FROM torrent_stream WHERE info_hash = $1)",
    )
    .bind(&info_hash)
    .fetch_one(&state.pool)
    .await
    .unwrap_or(false);

    let torrent_name = extract_dn(&body.magnet_link).unwrap_or_default();
    let parsed = parser::parse_title(&torrent_name);

    let meta_type = body.meta_type.as_deref().unwrap_or("movie");
    let search_title = parsed.title.as_deref().unwrap_or(&torrent_name);
    let matches = search_media(&state.pool, search_title, meta_type).await;

    (
        StatusCode::OK,
        Json(json!({
            "info_hash": info_hash,
            "torrent_name": torrent_name,
            "already_exists": already_exists,
            "parsed_title": {
                "title": parsed.title,
                "year": parsed.year,
                "resolution": parsed.resolution,
                "quality": parsed.quality,
                "codec": parsed.codec,
                "seasons": parsed.seasons,
                "episodes": parsed.episodes,
                "is_proper": parsed.is_proper,
                "is_repack": parsed.is_repack,
                "languages": parsed.languages,
            },
            "matches": matches,
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
        return (StatusCode::UNAUTHORIZED, Json(json!({"detail": "Unauthorized"}))).into_response();
    }

    let mut file_bytes: Option<Bytes> = None;
    let mut meta_type = String::from("movie");

    while let Ok(Some(field)) = multipart.next_field().await {
        match field.name() {
            Some("file") => {
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
                Json(json!({"detail": "Missing 'file' field"})),
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

    let already_exists: bool = sqlx::query_scalar(
        "SELECT EXISTS(SELECT 1 FROM torrent_stream WHERE info_hash = $1)",
    )
    .bind(&info_hash)
    .fetch_one(&state.pool)
    .await
    .unwrap_or(false);

    let parsed = parser::parse_title(&name);
    let search_title = parsed.title.as_deref().unwrap_or(&name);
    let matches = search_media(&state.pool, search_title, &meta_type).await;

    (
        StatusCode::OK,
        Json(json!({
            "info_hash": info_hash,
            "torrent_name": name,
            "already_exists": already_exists,
            "file_count": file_count,
            "total_size": total_size,
            "files": files,
            "parsed_title": {
                "title": parsed.title,
                "year": parsed.year,
                "resolution": parsed.resolution,
                "quality": parsed.quality,
                "codec": parsed.codec,
                "seasons": parsed.seasons,
                "episodes": parsed.episodes,
                "is_proper": parsed.is_proper,
                "is_repack": parsed.is_repack,
                "languages": parsed.languages,
            },
            "matches": matches,
        })),
    )
        .into_response()
}

pub async fn import_magnet(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(body): Json<MagnetImportRequest>,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::UNAUTHORIZED, Json(json!({"detail": "Unauthorized"}))).into_response();
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

    let force_import = body.force_import.unwrap_or(false);

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

    let torrent_name = extract_dn(&body.magnet_link)
        .or_else(|| body.title.clone())
        .unwrap_or_default();
    let name_for_parse = if torrent_name.is_empty() {
        body.title.as_deref().unwrap_or(&info_hash).to_string()
    } else {
        torrent_name.clone()
    };
    let parsed = parser::parse_title(&name_for_parse);

    let meta_type = body.meta_type.as_deref().unwrap_or("movie");

    let media_id = if let Some(mid) = &body.meta_id {
        if !mid.is_empty() {
            resolve_media_id(
                &state.pool,
                mid,
                meta_type,
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

    let source = body
        .meta_id
        .as_deref()
        .unwrap_or("manual")
        .to_string();

    match insert_torrent_stream(
        &state.pool,
        &info_hash,
        &torrent_name,
        &source,
        None,
        None,
        1,
        &parsed,
        media_id,
    )
    .await
    {
        Ok(stream_id) => (
            StatusCode::CREATED,
            Json(json!({
                "status": "success",
                "message": "Stream imported successfully",
                "import_id": stream_id,
            })),
        )
            .into_response(),
        Err(e) => {
            tracing::error!("import_magnet DB error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "Database error"})),
            )
                .into_response()
        }
    }
}

pub async fn import_torrent(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    mut multipart: Multipart,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::UNAUTHORIZED, Json(json!({"detail": "Unauthorized"}))).into_response();
    }

    let mut file_bytes: Option<Bytes> = None;
    let mut meta_type = String::from("movie");
    let mut meta_id: Option<String> = None;
    let mut title: Option<String> = None;
    let mut force_import = false;

    while let Ok(Some(field)) = multipart.next_field().await {
        match field.name() {
            Some("file") => {
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
            Some("force_import") => {
                force_import = field
                    .text()
                    .await
                    .ok()
                    .map(|v| v == "true" || v == "1")
                    .unwrap_or(false);
            }
            _ => {}
        }
    }

    let bytes = match file_bytes {
        Some(b) => b,
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Missing 'file' field"})),
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

    let parsed = parser::parse_title(&torrent_name);

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

    let source = meta_id.as_deref().unwrap_or("manual").to_string();

    match insert_torrent_stream(
        &state.pool,
        &info_hash,
        &torrent_name,
        &source,
        if total_size > 0 { Some(total_size) } else { None },
        None,
        file_count,
        &parsed,
        media_id,
    )
    .await
    {
        Ok(stream_id) => (
            StatusCode::CREATED,
            Json(json!({
                "status": "success",
                "message": "Stream imported successfully",
                "import_id": stream_id,
            })),
        )
            .into_response(),
        Err(e) => {
            tracing::error!("import_torrent DB error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "Database error"})),
            )
                .into_response()
        }
    }
}
