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
use serde::Deserialize;
use serde_json::json;
use sha2::Sha256;

use crate::{db::UserId, parser, state::AppState};

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
    should_auto_approve_import,
};

// ─── Magnet URI helpers ───────────────────────────────────────────────────────

pub fn extract_info_hash_from_magnet(magnet: &str) -> Option<String> {
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
        // Replace + with space before percent-decoding (magnet dn uses + for spaces)
        let plus_decoded = m.as_str().replace('+', "%20");
        urlencoding::decode(&plus_decoded)
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

// ─── Request / response shapes ────────────────────────────────────────────────

/// Per-file metadata passed from the UI after torrent analysis.
#[derive(Deserialize, Clone)]
pub struct FileEntry {
    pub index: i32,
    pub filename: String,
    pub size: i64,
    pub season_number: Option<i32>,
    pub episode_number: Option<i32>,
    #[serde(default)]
    pub meta_id: Option<String>,
    #[serde(default)]
    pub meta_type: Option<String>,
    #[serde(default)]
    pub meta_title: Option<String>,
    #[serde(default)]
    pub sports_category: Option<String>,
    #[serde(default)]
    pub episode_title: Option<String>,
}

fn enrich_sports_file_entries(files: &mut [FileEntry]) {
    for (idx, f) in files.iter_mut().enumerate() {
        if f.episode_number.is_none() {
            f.episode_number = Some((idx as i32) + 1);
        }
    }
}

/// Apply a caller-supplied episode-name regex to a filename (Python `episode_name_parser` parity).
///
/// Prefer named captures `episode_name`, `title`, `name`, `event`; fall back to
/// the first unnamed group, then the full match.  Dots and underscores are
/// replaced with spaces and the result is trimmed.
fn apply_episode_name_parser(pattern: &str, filename: &str) -> Option<String> {
    let re = regex::Regex::new(pattern).ok()?;
    let base = std::path::Path::new(filename)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or(filename);
    let caps = re.captures(base)?;

    // Prefer well-known named groups.
    let raw = ["episode_name", "title", "name", "event"]
        .iter()
        .find_map(|&grp| caps.name(grp).map(|m| m.as_str()))
        .or_else(|| caps.get(1).map(|m| m.as_str()))
        .or_else(|| caps.get(0).map(|m| m.as_str()))?;

    let cleaned: String = raw
        .chars()
        .map(|c| if matches!(c, '.' | '_') { ' ' } else { c })
        .collect::<String>()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");

    if cleaned.is_empty() {
        None
    } else {
        Some(cleaned)
    }
}

fn enrich_series_file_entries(
    files: &mut [FileEntry],
    torrent_name: &str,
    episode_name_parser: Option<&str>,
) {
    let default_season = parser::parse_title(torrent_name)
        .seasons
        .first()
        .copied()
        .unwrap_or(1);

    for f in files.iter_mut() {
        if f.season_number.is_some() && f.episode_number.is_some() {
            continue;
        }

        let season_hint = f.season_number.unwrap_or(default_season);
        if let Some(ep) = parser::episode_detector::detect_episode(&f.filename, season_hint)
            .or_else(|| parser::episode_detector::detect_episode(torrent_name, season_hint))
        {
            f.season_number = Some(ep.season);
            f.episode_number = Some(ep.episode);
        } else {
            let parsed = parser::parse_title(&f.filename);
            if f.season_number.is_none() {
                f.season_number = parsed.seasons.first().copied();
            }
            if f.episode_number.is_none() {
                f.episode_number = parsed.episodes.first().copied();
            }
        }

        if f.episode_title.as_ref().is_none_or(|t| t.trim().is_empty()) {
            // 1. User-supplied regex (Python episode_name_parser parity).
            let extracted =
                episode_name_parser.and_then(|p| apply_episode_name_parser(p, &f.filename));
            // 2. PTT's episode_title field (text between SxxExx and first
            //    release token).  PTT's `title` field is NOT used here — for
            //    SxxExx filenames it returns the show name, not the episode name.
            let extracted = extracted.or_else(|| parser::parse_title(&f.filename).episode_title);
            if let Some(title) = extracted {
                f.episode_title = Some(title);
            }
        }
    }
}

fn file_entries_as_json(files: &[FileEntry]) -> Vec<serde_json::Value> {
    files
        .iter()
        .map(|f| {
            json!({
                "index": f.index,
                "filename": f.filename,
                "size": f.size,
                "season_number": f.season_number,
                "episode_number": f.episode_number,
                "meta_id": f.meta_id,
                "meta_type": f.meta_type,
                "meta_title": f.meta_title.as_deref().or(f.episode_title.as_deref()),
                "episode_title": f.episode_title,
                "sports_category": f.sports_category,
            })
        })
        .collect()
}

#[allow(clippy::too_many_arguments)]
fn build_torrent_contribution_data(
    info_hash: &str,
    torrent_name: &str,
    meta_type: &str,
    meta_id: Option<&str>,
    title: Option<&str>,
    total_size: Option<i64>,
    file_count: i32,
    file_data: &[FileEntry],
    parsed: &parser::ParsedTitle,
    poster: Option<&str>,
    background: Option<&str>,
    release_date: Option<&str>,
    is_anonymous: bool,
    anonymous_display_name: Option<&str>,
    is_public: bool,
    stream_id: i32,
    languages: &[String],
    trackers: &[String],
    catalogs: &[String],
    sports_category: Option<&str>,
) -> serde_json::Value {
    let file_json = file_entries_as_json(file_data);
    json!({
        "info_hash": info_hash,
        "name": torrent_name,
        "title": title.unwrap_or(torrent_name),
        "meta_type": meta_type,
        "meta_id": meta_id,
        "total_size": total_size.unwrap_or(0),
        "file_count": file_count,
        "file_data": file_json,
        "resolution": parsed.resolution,
        "codec": parsed.codec,
        "quality": parsed.quality,
        "release_group": parsed.release_group,
        "is_remastered": parsed.is_remastered,
        "is_upscaled": parsed.is_upscaled,
        "is_proper": parsed.is_proper,
        "is_repack": parsed.is_repack,
        "is_extended": parsed.is_extended,
        "is_complete": parsed.is_complete,
        "is_dubbed": parsed.is_dubbed,
        "is_subbed": parsed.is_subbed,
        "year": parsed.year,
        "poster": poster,
        "background": background,
        "created_at": release_date,
        "is_anonymous": is_anonymous,
        "anonymous_display_name": anonymous_display_name,
        "is_public": is_public,
        "stream_id": stream_id,
        "languages": languages,
        "audio_formats": parsed.audio,
        "hdr_formats": parsed.hdr,
        "channels": parsed.channels,
        "trackers": trackers,
        "catalogs": catalogs,
        "sports_category": sports_category,
    })
}

async fn torrent_already_exists_response(
    state: &AppState,
    stream_id: i32,
    info_hash: &str,
    meta_id: Option<&str>,
    meta_type: &str,
    title: Option<&str>,
    poster: Option<&str>,
    background: Option<&str>,
    release_date: Option<&str>,
    year: Option<i32>,
) -> Response {
    let link_count: i64 =
        sqlx::query_scalar("SELECT COUNT(*)::bigint FROM stream_media_link WHERE stream_id = $1")
            .bind(stream_id)
            .fetch_one(&state.pool)
            .await
            .unwrap_or(0);

    let mut relinked = false;
    if link_count == 0 {
        relinked = super::import_helpers::try_link_orphan_torrent_stream(
            &state.pool,
            &state.http,
            state.config.tmdb_api_key.as_deref(),
            state.config.tvdb_api_key.as_deref(),
            stream_id,
            meta_id,
            meta_type,
            crate::scrapers::media_resolve::ImportMediaOverrides {
                title,
                poster,
                background,
                release_date,
                year,
            },
            None,
        )
        .await
        .is_some();
    }

    let attachments =
        super::import_helpers::stream_media_attachment_details(&state.pool, stream_id, 2).await;
    let attached_count = attachments
        .get("count")
        .and_then(|v| v.as_i64())
        .unwrap_or(0);
    let attached_media = attachments
        .get("items")
        .cloned()
        .unwrap_or_else(|| json!([]));

    if relinked && attached_count > 0 {
        let message =
            super::import_helpers::build_existing_torrent_warning_message(info_hash, &attachments);
        return (
            StatusCode::OK,
            Json(json!({
                "status": "success",
                "message": format!(
                    "Torrent already existed but was linked to your library item. {message}"
                ),
                "import_id": stream_id.to_string(),
                "details": {
                    "reason": "already_exists",
                    "action": "linked",
                    "info_hash": info_hash,
                    "existing_stream_id": stream_id,
                    "attached_media_count": attached_count,
                    "attached_media": attached_media,
                }
            })),
        )
            .into_response();
    }

    let message =
        super::import_helpers::build_existing_torrent_warning_message(info_hash, &attachments);

    (
        StatusCode::OK,
        Json(json!({
            "status": "warning",
            "message": message,
            "details": {
                "reason": "already_exists",
                "action": "skipped",
                "info_hash": info_hash,
                "existing_stream_id": stream_id,
                "attached_media_count": attached_count,
                "attached_media": attached_media,
            }
        })),
    )
        .into_response()
}

async fn record_torrent_contribution(
    state: &AppState,
    uploader_user_id: Option<i64>,
    meta_id: Option<&str>,
    contribution_data: &serde_json::Value,
    auto_approve: bool,
    is_privileged: bool,
    uploader_name: &str,
) -> Option<String> {
    match create_contribution_record(
        &state.pool,
        uploader_user_id,
        "torrent",
        meta_id,
        contribution_data,
        auto_approve,
        is_privileged,
    )
    .await
    {
        Ok(contrib_id) => {
            if auto_approve {
                if let Some(uid) = uploader_user_id {
                    award_contribution_points(&state.pool, uid, "torrent").await;
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
                    uploader_name,
                    contribution_data,
                )
                .await;
            }
            tracing::debug!("contribution created: {contrib_id}");
            return Some(contrib_id);
        }
        Err(e) => {
            tracing::error!("failed to create torrent contribution record: {e}");
        }
    }
    None
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

fn analyze_file_entry(path: String, size: i64, index: i32) -> serde_json::Value {
    let filename = std::path::Path::new(&path)
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_else(|| path.clone());
    json!({
        "path": path,
        "filename": filename,
        "size": size,
        "index": index,
    })
}

fn analyze_files_from_torrent(torrent: &LavaTorrent) -> Vec<serde_json::Value> {
    let mut files: Vec<serde_json::Value> = torrent
        .files
        .as_ref()
        .map(|fs| {
            fs.iter()
                .enumerate()
                .map(|(i, f)| {
                    analyze_file_entry(f.path.to_string_lossy().into_owned(), f.length, i as i32)
                })
                .collect()
        })
        .unwrap_or_default();

    if files.is_empty() && !torrent.name.is_empty() {
        files.push(analyze_file_entry(torrent.name.clone(), torrent.length, 0));
    }

    files
}

fn analyze_files_from_dht(meta: &crate::demagnetize::TorrentMeta) -> Vec<serde_json::Value> {
    meta.files
        .iter()
        .enumerate()
        .map(|(i, f)| analyze_file_entry(f.path.clone(), f.size, i as i32))
        .collect()
}

fn analyze_dht_summary(meta: &crate::demagnetize::TorrentMeta) -> serde_json::Value {
    json!({
        "name": meta.name,
        "total_size": meta.total_size,
        "num_files": meta.files.len(),
        "files": meta
            .files
            .iter()
            .map(|f| json!({"path": f.path, "size": f.size}))
            .collect::<Vec<_>>(),
    })
}

async fn resolve_dht_metadata(
    info_hash: &str,
    resolve_files: bool,
    resolve_timeout_secs: Option<u64>,
) -> Option<Result<crate::demagnetize::TorrentMeta, crate::demagnetize::Error>> {
    if !resolve_files {
        return None;
    }
    let secs = resolve_timeout_secs.unwrap_or(30).clamp(5, 60);
    Some(crate::demagnetize::resolve(info_hash, std::time::Duration::from_secs(secs)).await)
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

pub async fn analyze_magnet(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(body): Json<MagnetAnalyzeRequest>,
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

    let parsed = if parser::is_sports_title(&torrent_name) {
        parser::parse_sports_title(&torrent_name)
    } else {
        parser::parse_title(&torrent_name)
    };

    let meta_type = body.meta_type.as_deref().unwrap_or("movie");
    let search_title = parsed.title.as_deref().unwrap_or(&torrent_name);

    // Run metadata search and optional DHT resolution in parallel — DHT can take
    // up to resolve_timeout_secs while search hits DB + external providers.
    let search_started = std::time::Instant::now();
    let search_future = async {
        let matches = super::import_helpers::search_analyze_matches(
            &state,
            UserId::from_auth_id(user_id),
            search_title,
            parsed.year,
            meta_type,
        )
        .await;
        let meta_match = super::import_helpers::resolve_import_meta_match(
            &state.pool,
            body.meta_id.as_deref(),
            meta_type,
            search_title,
            parsed.year,
            &matches,
        )
        .await;
        (matches, meta_match)
    };
    let dht_future =
        resolve_dht_metadata(&info_hash, body.resolve_files, body.resolve_timeout_secs);

    let ((matches, meta_match), dht_result) = tokio::join!(search_future, dht_future);
    tracing::debug!(
        info_hash = %info_hash,
        search_elapsed_ms = search_started.elapsed().as_millis(),
        match_count = matches.len(),
        meta_match = meta_match.is_some(),
        dht_requested = body.resolve_files,
        "magnet analyze: metadata search complete"
    );

    // Optional: contact DHT to fetch the full file list via BEP-9.
    let mut files: Vec<serde_json::Value> = Vec::new();
    let mut file_count: Option<i32> = None;
    let mut total_size: Option<i64> = None;
    let resolved_files: Option<serde_json::Value> = if let Some(dht_result) = dht_result {
        match dht_result {
            Ok(meta) => {
                files = analyze_files_from_dht(&meta);
                file_count = Some(files.len() as i32);
                total_size = Some(meta.total_size);
                Some(analyze_dht_summary(&meta))
            }
            Err(e) => {
                tracing::warn!(info_hash = %info_hash, error = %e, "magnet analyze: demagnetize failed");
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
            "files": files,
            "file_count": file_count,
            "total_size": total_size,
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

    let mut file_bytes: Option<Bytes> = None;
    let mut meta_type = String::from("movie");
    let mut meta_id: Option<String> = None;
    let mut title: Option<String> = None;
    let mut resolve_files = false;
    let mut resolve_timeout_secs: Option<u64> = None;

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
            Some("resolve_files") => {
                resolve_files = field
                    .text()
                    .await
                    .ok()
                    .map(|v| v == "true" || v == "1")
                    .unwrap_or(false);
            }
            Some("resolve_timeout_secs") => {
                if let Ok(raw) = field.text().await {
                    resolve_timeout_secs = raw.parse::<u64>().ok();
                }
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

    let name = title.clone().unwrap_or_else(|| torrent.name.clone());
    let mut files = analyze_files_from_torrent(&torrent);
    let mut total_size = torrent.length;
    let mut file_count = files.len().max(1) as i32;
    let mut resolved_files: Option<serde_json::Value> = None;

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

    let parsed = if parser::is_sports_title(&name) {
        parser::parse_sports_title(&name)
    } else {
        parser::parse_title(&name)
    };
    let search_title = parsed.title.as_deref().unwrap_or(&name);
    let matches = super::import_helpers::search_analyze_matches(
        &state,
        UserId::from_auth_id(user_id),
        search_title,
        parsed.year,
        &meta_type,
    )
    .await;

    let meta_match = super::import_helpers::resolve_import_meta_match(
        &state.pool,
        meta_id.as_deref(),
        &meta_type,
        search_title,
        parsed.year,
        &matches,
    )
    .await;

    if files.is_empty() {
        if let Some(dht_result) =
            resolve_dht_metadata(&info_hash, resolve_files, resolve_timeout_secs).await
        {
            match dht_result {
                Ok(meta) => {
                    files = analyze_files_from_dht(&meta);
                    file_count = files.len().max(1) as i32;
                    total_size = meta.total_size;
                    resolved_files = Some(analyze_dht_summary(&meta));
                }
                Err(e) => {
                    tracing::warn!("demagnetize {info_hash}: {e}");
                    resolved_files = Some(json!({"error": e.to_string()}));
                }
            }
        }
    }

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
            "meta_match": meta_match,
            "resolved": resolved_files,
        })),
    )
        .into_response()
}

// ─── Bot / shared analyze helpers ─────────────────────────────────────────────

pub async fn analyze_magnet_for_bot(
    state: &AppState,
    magnet_link: &str,
    meta_type: &str,
) -> serde_json::Value {
    let info_hash = match extract_info_hash_from_magnet(magnet_link) {
        Some(h) => h,
        None => {
            return json!({"success": false, "error": "Invalid magnet link format."});
        }
    };

    let torrent_name = extract_dn(magnet_link).unwrap_or_default();
    if !torrent_name.is_empty() && is_adult_content(&torrent_name) {
        return json!({"success": false, "error": "Adult content is not allowed"});
    }

    let parsed = if parser::is_sports_title(&torrent_name) {
        parser::parse_sports_title(&torrent_name)
    } else {
        parser::parse_title(&torrent_name)
    };
    let search_title = parsed.title.as_deref().unwrap_or(&torrent_name);
    let matches = super::import_helpers::search_analyze_matches(
        state,
        None,
        search_title,
        parsed.year,
        meta_type,
    )
    .await;

    let mut result = json!({
        "success": true,
        "info_hash": info_hash,
        "torrent_name": torrent_name,
        "parsed_title": parsed.title,
        "year": parsed.year,
        "resolution": parsed.resolution,
        "quality": parsed.quality,
        "codec": parsed.codec,
        "matches": matches,
    });

    if let Ok(meta) =
        crate::demagnetize::resolve(&info_hash, std::time::Duration::from_secs(30)).await
    {
        let files: Vec<serde_json::Value> = meta
            .files
            .iter()
            .map(|f| json!({"path": f.path, "size": f.size, "filename": f.path}))
            .collect();
        result["total_size"] = json!(meta.total_size);
        result["files"] = json!(files);
        result["file_count"] = json!(files.len());
        if result
            .get("torrent_name")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .is_empty()
        {
            result["torrent_name"] = json!(meta.name);
        }
    }

    result
}

pub async fn analyze_torrent_bytes(
    state: &AppState,
    bytes: &[u8],
    meta_type: &str,
) -> Result<serde_json::Value, String> {
    let torrent: LavaTorrent = LavaTorrent::read_from_bytes(bytes)
        .map_err(|e| format!("Failed to parse .torrent: {e}"))?;

    let info_hash = torrent
        .info_hash_bytes()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect::<String>();
    let name = torrent.name.clone();

    if is_adult_content(&name) {
        return Err("Adult content is not allowed".to_string());
    }

    let parsed = if parser::is_sports_title(&name) {
        parser::parse_sports_title(&name)
    } else {
        parser::parse_title(&name)
    };
    let search_title = parsed.title.as_deref().unwrap_or(&name);
    let matches = super::import_helpers::search_analyze_matches(
        state,
        None,
        search_title,
        parsed.year,
        meta_type,
    )
    .await;

    let files: Vec<serde_json::Value> = analyze_files_from_torrent(&torrent);

    Ok(json!({
        "success": true,
        "info_hash": info_hash,
        "torrent_name": name,
        "total_size": torrent.length,
        "file_count": files.len().max(1),
        "files": files,
        "parsed_title": parsed.title,
        "year": parsed.year,
        "resolution": parsed.resolution,
        "quality": parsed.quality,
        "codec": parsed.codec,
        "matches": matches,
    }))
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
    let mut total_size: Option<i64> = None;
    let mut file_data: Vec<FileEntry> = Vec::new();
    let mut is_anonymous_field: Option<bool> = None;
    let mut anonymous_display_name: Option<String> = None;
    let mut poster: Option<String> = None;
    let mut background: Option<String> = None;
    let mut release_date: Option<String> = None;
    let mut form_audio: Vec<String> = Vec::new();
    let mut form_hdr: Vec<String> = Vec::new();
    let mut episode_name_parser: Option<String> = None;

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
            Some("audio") => {
                if let Ok(raw) = field.text().await {
                    form_audio = raw
                        .split(',')
                        .map(|s| s.trim().to_string())
                        .filter(|s| !s.is_empty())
                        .collect();
                }
            }
            Some("hdr") => {
                if let Ok(raw) = field.text().await {
                    form_hdr = raw
                        .split(',')
                        .map(|s| s.trim().to_string())
                        .filter(|s| !s.is_empty())
                        .collect();
                }
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
            Some("total_size") => {
                if let Ok(raw) = field.text().await {
                    total_size = raw.parse::<i64>().ok();
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
            Some("poster") => {
                poster = field.text().await.ok().filter(|s| !s.is_empty());
            }
            Some("background") => {
                background = field.text().await.ok().filter(|s| !s.is_empty());
            }
            Some("created_at") | Some("release_date") => {
                release_date = field.text().await.ok().filter(|s| !s.is_empty());
            }
            Some("episode_name_parser") => {
                episode_name_parser = field.text().await.ok().filter(|s| !s.is_empty());
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
    let existing_id: Option<i32> = sqlx::query_scalar(
        "SELECT ts.stream_id FROM torrent_stream ts WHERE ts.info_hash = $1 LIMIT 1",
    )
    .bind(&info_hash)
    .fetch_optional(&state.pool)
    .await
    .unwrap_or(None);

    if let Some(sid) = existing_id {
        if !force_import {
            return torrent_already_exists_response(
                &state,
                sid,
                &info_hash,
                meta_id.as_deref(),
                &meta_type,
                title.as_deref(),
                poster.as_deref(),
                background.as_deref(),
                release_date.as_deref(),
                None,
            )
            .await;
        }
    }

    let mut parsed = if parser::is_sports_title(&name_for_parse) {
        parser::parse_sports_title(&name_for_parse)
    } else {
        parser::parse_title(&name_for_parse)
    };
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
    for a in form_audio {
        if !parsed.audio.iter().any(|x| x == &a) {
            parsed.audio.push(a);
        }
    }
    for h in form_hdr {
        if !parsed.hdr.iter().any(|x| x == &h) {
            parsed.hdr.push(h);
        }
    }

    if meta_type == "sports" && !file_data.is_empty() {
        enrich_sports_file_entries(&mut file_data);
    } else if meta_type == "series" && !file_data.is_empty() {
        enrich_series_file_entries(
            &mut file_data,
            &torrent_name,
            episode_name_parser.as_deref(),
        );
    }

    let primary_meta_id = meta_id
        .as_deref()
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| format!("user_{}", &info_hash[..8.min(info_hash.len())]));
    let primary_title = title.as_deref().unwrap_or(&torrent_name);
    let file_rows = file_entries_as_json(&file_data);
    let sports_category = if meta_type == "sports" {
        parser::detect_sports_category(&name_for_parse).map(str::to_string)
    } else {
        None
    };
    let prefetch = super::import_helpers::prefetch_torrent_import_metadata(
        &state.http,
        state.config.tmdb_api_key.as_deref(),
        state.config.tvdb_api_key.as_deref(),
        &meta_type,
        &primary_meta_id,
        primary_title,
        sports_category.as_deref(),
        &file_rows,
    )
    .await;

    let media_id = if meta_id.as_ref().is_some_and(|s| !s.is_empty()) {
        super::import_helpers::resolve_media_for_import(
            &state.pool,
            &state.http,
            state.config.tmdb_api_key.as_deref(),
            state.config.tvdb_api_key.as_deref(),
            meta_id.as_deref().unwrap(),
            &meta_type,
            crate::scrapers::media_resolve::ImportMediaOverrides {
                title: title.as_deref(),
                poster: poster.as_deref(),
                background: background.as_deref(),
                release_date: release_date.as_deref(),
                year: parsed.year,
            },
            Some(&prefetch),
            state.config.poster_nsfw_enabled,
        )
        .await
        .map(i64::from)
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
    let auto_approve =
        should_auto_approve_import(is_privileged, user.is_active, resolved_is_anonymous);

    let mut effective_catalogs = catalogs.clone();
    if meta_type == "sports" {
        let sc = sports_category.as_deref().unwrap_or("other_sports");
        if !effective_catalogs.iter().any(|c| c == sc) {
            effective_catalogs.insert(0, sc.to_string());
        }
    }
    let effective_languages: Vec<String> = if languages.is_empty() {
        parsed.languages.clone()
    } else {
        languages.clone()
    };

    let file_count = if !file_data.is_empty() {
        file_data.len() as i32
    } else {
        1
    };
    let is_public = super::import_helpers::stream_is_public_on_submit(auto_approve, true);
    let magnet_trackers = extract_trackers_from_magnet(&magnet_link);

    let stream_id = match super::import_helpers::persist_torrent_import(
        &state.pool,
        &state.http,
        super::import_helpers::TorrentImportPersist {
            info_hash: &info_hash,
            name: &torrent_name,
            source: super::import_helpers::CONTRIBUTION_STREAM_SOURCE,
            total_size,
            seeders: None,
            file_count,
            parsed: &parsed,
            media_id,
            meta_type: &meta_type,
            is_public,
            file_rows: &file_rows,
            languages: &effective_languages,
            catalogs: &effective_catalogs,
            trackers: &magnet_trackers,
            sports_category: sports_category.as_deref(),
            fallback_title: title.as_deref().unwrap_or(&torrent_name),
            tmdb_api_key: state.config.tmdb_api_key.as_deref(),
            tvdb_api_key: state.config.tvdb_api_key.as_deref(),
            prefetch: &prefetch,
            torrent_type: crate::db::TorrentType::Public,
            torrent_file: None,
            uploader: Some(&uploader_name),
            uploader_user_id: uploader_user_id.and_then(|id| i32::try_from(id).ok()),
        },
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

    let contribution_data = build_torrent_contribution_data(
        &info_hash,
        &torrent_name,
        &meta_type,
        meta_id.as_deref(),
        title.as_deref(),
        total_size,
        file_count,
        &file_data,
        &parsed,
        poster.as_deref(),
        background.as_deref(),
        release_date.as_deref(),
        resolved_is_anonymous,
        anonymous_display_name.as_deref(),
        auto_approve,
        stream_id,
        &effective_languages,
        &magnet_trackers,
        &effective_catalogs,
        sports_category.as_deref(),
    );
    let contribution_id = record_torrent_contribution(
        &state,
        uploader_user_id,
        meta_id.as_deref(),
        &contribution_data,
        auto_approve,
        is_privileged,
        &uploader_name,
    )
    .await;

    let message = if auto_approve {
        "Torrent imported successfully!".to_string()
    } else {
        super::import_helpers::pending_import_message("Magnet link")
    };

    (
        StatusCode::OK,
        Json(json!({
            "status": "success",
            "message": message,
            "import_id": contribution_id.unwrap_or_else(|| stream_id.to_string()),
            "details": {
                "info_hash": info_hash,
                "title": title.as_deref().unwrap_or(&torrent_name),
                "stream_id": stream_id,
                "auto_approved": auto_approve,
            }
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
    let mut poster: Option<String> = None;
    let mut background: Option<String> = None;
    let mut release_date: Option<String> = None;
    let mut episode_name_parser: Option<String> = None;

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
            Some("poster") => {
                poster = field.text().await.ok().filter(|s| !s.is_empty());
            }
            Some("background") => {
                background = field.text().await.ok().filter(|s| !s.is_empty());
            }
            Some("created_at") | Some("release_date") => {
                release_date = field.text().await.ok().filter(|s| !s.is_empty());
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
            Some("episode_name_parser") => {
                episode_name_parser = field.text().await.ok().filter(|s| !s.is_empty());
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

    let torrent_name = title.clone().unwrap_or_else(|| torrent.name.clone());
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
    let existing_id: Option<i32> = sqlx::query_scalar(
        "SELECT ts.stream_id FROM torrent_stream ts WHERE ts.info_hash = $1 LIMIT 1",
    )
    .bind(&info_hash)
    .fetch_optional(&state.pool)
    .await
    .unwrap_or(None);

    if let Some(sid) = existing_id {
        if !force_import {
            return torrent_already_exists_response(
                &state,
                sid,
                &info_hash,
                meta_id.as_deref(),
                &meta_type,
                title.as_deref(),
                poster.as_deref(),
                background.as_deref(),
                release_date.as_deref(),
                None,
            )
            .await;
        }
    }

    let mut parsed = if parser::is_sports_title(&torrent_name) {
        parser::parse_sports_title(&torrent_name)
    } else {
        parser::parse_title(&torrent_name)
    };
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

    let mut effective_files: Vec<FileEntry> = if !file_data.is_empty() {
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
                        meta_id: None,
                        meta_type: None,
                        meta_title: None,
                        sports_category: None,
                        episode_title: None,
                    })
                } else {
                    None
                }
            })
            .collect()
    } else {
        Vec::new()
    };
    if meta_type == "sports" && !effective_files.is_empty() {
        enrich_sports_file_entries(&mut effective_files);
    } else if meta_type == "series" && !effective_files.is_empty() {
        enrich_series_file_entries(
            &mut effective_files,
            &torrent_name,
            episode_name_parser.as_deref(),
        );
    }

    let primary_meta_id = meta_id
        .as_deref()
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| format!("user_{}", &info_hash[..8.min(info_hash.len())]));
    let primary_title = title.as_deref().unwrap_or(&torrent_name);
    let file_rows = file_entries_as_json(&effective_files);
    let sports_category = if meta_type == "sports" {
        parser::detect_sports_category(&torrent_name).map(str::to_string)
    } else {
        None
    };
    let prefetch = super::import_helpers::prefetch_torrent_import_metadata(
        &state.http,
        state.config.tmdb_api_key.as_deref(),
        state.config.tvdb_api_key.as_deref(),
        &meta_type,
        &primary_meta_id,
        primary_title,
        sports_category.as_deref(),
        &file_rows,
    )
    .await;

    let media_id = if meta_id.as_ref().is_some_and(|s| !s.is_empty()) {
        super::import_helpers::resolve_media_for_import(
            &state.pool,
            &state.http,
            state.config.tmdb_api_key.as_deref(),
            state.config.tvdb_api_key.as_deref(),
            meta_id.as_deref().unwrap(),
            &meta_type,
            crate::scrapers::media_resolve::ImportMediaOverrides {
                title: title.as_deref(),
                poster: poster.as_deref(),
                background: background.as_deref(),
                release_date: release_date.as_deref(),
                year: parsed.year,
            },
            Some(&prefetch),
            state.config.poster_nsfw_enabled,
        )
        .await
        .map(i64::from)
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
    let auto_approve =
        should_auto_approve_import(is_privileged, user.is_active, resolved_is_anonymous);

    let mut effective_catalogs = catalogs.clone();
    if meta_type == "sports" {
        let sc = sports_category.as_deref().unwrap_or("other_sports");
        if !effective_catalogs.iter().any(|c| c == sc) {
            effective_catalogs.insert(0, sc.to_string());
        }
    }
    let effective_languages: Vec<String> = if languages.is_empty() {
        parsed.languages.clone()
    } else {
        languages.clone()
    };

    let source = super::import_helpers::CONTRIBUTION_STREAM_SOURCE;

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

    let is_public = super::import_helpers::stream_is_public_on_submit(auto_approve, true);
    let stream_id = match super::import_helpers::persist_torrent_import(
        &state.pool,
        &state.http,
        super::import_helpers::TorrentImportPersist {
            info_hash: &info_hash,
            name: &torrent_name,
            source,
            total_size: if total_size > 0 {
                Some(total_size)
            } else {
                None
            },
            seeders: None,
            file_count,
            parsed: &parsed,
            media_id,
            meta_type: &meta_type,
            is_public,
            file_rows: &file_rows,
            languages: &effective_languages,
            catalogs: &effective_catalogs,
            trackers: &tracker_urls,
            sports_category: sports_category.as_deref(),
            fallback_title: title.as_deref().unwrap_or(&torrent_name),
            tmdb_api_key: state.config.tmdb_api_key.as_deref(),
            tvdb_api_key: state.config.tvdb_api_key.as_deref(),
            prefetch: &prefetch,
            torrent_type: crate::db::TorrentType::Public,
            torrent_file: Some(bytes.as_ref()),
            uploader: Some(&uploader_name),
            uploader_user_id: uploader_user_id.and_then(|id| i32::try_from(id).ok()),
        },
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

    let contribution_data = build_torrent_contribution_data(
        &info_hash,
        &torrent_name,
        &meta_type,
        meta_id.as_deref(),
        title.as_deref(),
        if total_size > 0 {
            Some(total_size)
        } else {
            None
        },
        file_count,
        &effective_files,
        &parsed,
        poster.as_deref(),
        background.as_deref(),
        release_date.as_deref(),
        resolved_is_anonymous,
        anonymous_display_name.as_deref(),
        auto_approve,
        stream_id,
        &effective_languages,
        &tracker_urls,
        &effective_catalogs,
        sports_category.as_deref(),
    );
    let contribution_id = record_torrent_contribution(
        &state,
        uploader_user_id,
        meta_id.as_deref(),
        &contribution_data,
        auto_approve,
        is_privileged,
        &uploader_name,
    )
    .await;

    let message = if auto_approve {
        "Torrent imported successfully!".to_string()
    } else {
        super::import_helpers::pending_import_message("Torrent")
    };

    (
        StatusCode::OK,
        Json(json!({
            "status": "success",
            "message": message,
            "import_id": contribution_id.unwrap_or_else(|| stream_id.to_string()),
            "details": {
                "info_hash": info_hash,
                "title": title.as_deref().unwrap_or(&torrent_name),
                "stream_id": stream_id,
                "auto_approved": auto_approve,
            }
        })),
    )
        .into_response()
}
