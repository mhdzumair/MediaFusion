/// NZB import endpoints.
///
/// Routes:
///   POST /api/v1/import/nzb/analyze/file → analyze_nzb_file
///   POST /api/v1/import/nzb/analyze/url  → analyze_nzb_url
///   POST /api/v1/import/nzb              → import_nzb
///   POST /api/v1/import/nzb/url          → import_nzb_url
///   GET  /api/v1/import/nzb/{guid}/download → download_nzb
use std::sync::Arc;

use axum::{
    body::Bytes,
    extract::{Multipart, Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Redirect, Response},
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::Utc;
use hmac::{Hmac, KeyInit, Mac};
use quick_xml::{events::Event, Reader};
use serde::Deserialize;
use serde_json::json;
use sha2::{Digest, Sha256};

use super::import_helpers::{
    award_contribution_points, create_contribution_record, enforce_upload_permissions,
    fetch_user_info, is_adult_content, notify_pending_contribution, resolve_uploader_identity,
    should_auto_approve_import,
};
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

// ─── NZB parsing ──────────────────────────────────────────────────────────────

#[derive(Debug, Default)]
struct NzbFile {
    subject: String,
    size: i64,
}

#[derive(Debug)]
struct NzbInfo {
    files: Vec<NzbFile>,
    total_size: i64,
    group: Option<String>,
    title: String,
    nzb_guid: String,
}

fn parse_nzb(data: &[u8]) -> Result<NzbInfo, String> {
    let mut reader = Reader::from_reader(data);
    reader.config_mut().trim_text(true);

    let mut files: Vec<NzbFile> = Vec::new();
    let mut current_file: Option<NzbFile> = None;
    let mut current_segment_bytes: i64 = 0;
    let mut group: Option<String> = None;
    let mut in_segment = false;
    let mut in_group = false;

    let mut buf = Vec::new();
    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(ref e)) => match e.name().as_ref() {
                b"file" => {
                    let subject = e
                        .attributes()
                        .filter_map(|a| a.ok())
                        .find(|a| a.key.as_ref() == b"subject")
                        .map(|a| String::from_utf8_lossy(&a.value).into_owned())
                        .unwrap_or_default();
                    current_file = Some(NzbFile { subject, size: 0 });
                    current_segment_bytes = 0;
                }
                b"segment" => {
                    in_segment = true;
                    let seg_bytes: i64 = e
                        .attributes()
                        .filter_map(|a| a.ok())
                        .find(|a| a.key.as_ref() == b"bytes")
                        .and_then(|a| String::from_utf8_lossy(&a.value).parse().ok())
                        .unwrap_or(0);
                    current_segment_bytes += seg_bytes;
                }
                b"group" => {
                    in_group = true;
                }
                _ => {}
            },
            Ok(Event::End(ref e)) => match e.name().as_ref() {
                b"file" => {
                    if let Some(mut f) = current_file.take() {
                        f.size = current_segment_bytes;
                        files.push(f);
                        current_segment_bytes = 0;
                    }
                    in_group = false;
                }
                b"segment" => {
                    in_segment = false;
                }
                b"group" => {
                    in_group = false;
                }
                _ => {}
            },
            Ok(Event::Text(ref e)) => {
                let text = String::from_utf8_lossy(e.as_ref()).into_owned();
                if in_group && group.is_none() {
                    group = Some(text);
                }
                let _ = in_segment; // suppress warning
            }
            Ok(Event::Eof) => break,
            Err(e) => return Err(format!("XML parse error: {e}")),
            _ => {}
        }
        buf.clear();
    }

    if files.is_empty() {
        return Err("No <file> elements found in NZB".into());
    }

    // Derive title from first file subject
    let title = extract_nzb_title(&files[0].subject);

    // Generate deterministic GUID: SHA-256 of sorted subjects, first 40 chars
    let mut subjects: Vec<&str> = files.iter().map(|f| f.subject.as_str()).collect();
    subjects.sort();
    let mut hasher = Sha256::new();
    for s in &subjects {
        hasher.update(s.as_bytes());
        hasher.update(b"\n");
    }
    let hash = hasher.finalize();
    let nzb_guid: String =
        hash.iter().map(|b| format!("{b:02x}")).collect::<String>()[..40].to_string();

    let total_size: i64 = files.iter().map(|f| f.size).sum();

    Ok(NzbInfo {
        files,
        total_size,
        group,
        title,
        nzb_guid,
    })
}

/// Extract a release title from an NZB file subject line.
/// Subjects look like: "Title.720p.BluRay.mkv - [1/15] yEnc"
fn extract_nzb_title(subject: &str) -> String {
    // Strip everything from " - [" or ".nzb" onward
    let s = if let Some(pos) = subject.find(" - [") {
        &subject[..pos]
    } else if let Some(pos) = subject.find(".nzb") {
        &subject[..pos]
    } else {
        subject
    };
    s.trim().to_string()
}

// ─── DB insert helper ─────────────────────────────────────────────────────────

#[allow(clippy::too_many_arguments)]
async fn insert_usenet_stream(
    pool: &sqlx::PgPool,
    nzb_guid: &str,
    nzb_url: Option<&str>,
    name: &str,
    source: &str,
    size: Option<i64>,
    indexer: Option<&str>,
    group_name: Option<&str>,
    parsed: &parser::ParsedTitle,
    media_id: Option<i64>,
    uploader: &str,
    uploader_user_id: Option<i64>,
    is_public: bool,
) -> Result<i64, sqlx::Error> {
    let mut txn = pool.begin().await?;

    let stream_id: i64 = sqlx::query_scalar(
        r#"INSERT INTO stream(
               stream_type, name, source, uploader, uploader_user_id,
               resolution, codec, quality,
               is_proper, is_repack, is_extended, is_complete, is_dubbed, release_group,
               is_active, is_blocked, is_public, playback_count, created_at
           ) VALUES(
               'USENET'::streamtype, $1, $2, $3, $4,
               $5, $6, $7,
               $8, $9, $10, $11, $12, $13,
               true, false, $14, 0, NOW()
           )
           RETURNING id"#,
    )
    .bind(name)
    .bind(source)
    .bind(uploader)
    .bind(uploader_user_id)
    .bind(parsed.resolution.as_deref())
    .bind(parsed.codec.as_deref())
    .bind(parsed.quality.as_deref())
    .bind(parsed.is_proper)
    .bind(parsed.is_repack)
    .bind(parsed.is_extended)
    .bind(parsed.is_complete)
    .bind(parsed.is_dubbed)
    .bind(parsed.release_group.as_deref())
    .bind(is_public)
    .fetch_one(&mut *txn)
    .await?;

    let us_result = sqlx::query(
        r#"INSERT INTO usenet_stream(
               stream_id, nzb_guid, nzb_url, size, indexer, group_name, is_passworded
           ) VALUES($1, $2, $3, $4, $5, $6, false)
           ON CONFLICT (nzb_guid) DO NOTHING"#,
    )
    .bind(stream_id as i32)
    .bind(nzb_guid)
    .bind(nzb_url)
    .bind(size)
    .bind(indexer)
    .bind(group_name)
    .execute(&mut *txn)
    .await;

    if let Ok(r) = &us_result {
        if r.rows_affected() == 0 {
            sqlx::query("DELETE FROM stream WHERE id = $1")
                .bind(stream_id as i32)
                .execute(&mut *txn)
                .await
                .ok();
            txn.commit().await?;
            let existing: i64 =
                sqlx::query_scalar("SELECT stream_id FROM usenet_stream WHERE nzb_guid = $1")
                    .bind(nzb_guid)
                    .fetch_one(pool)
                    .await
                    .unwrap_or(stream_id);
            if let Some(mid) = media_id {
                let _ =
                    super::import_helpers::link_stream_to_media(pool, existing as i32, mid as i32)
                        .await;
            }
            return Ok(existing);
        }
    }
    us_result?;

    if let Some(mid) = media_id {
        sqlx::query(
            r#"INSERT INTO stream_media_link(stream_id, media_id, is_primary, is_verified, created_at)
               SELECT $1, $2, true, false, NOW()
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

fn nzb_info_to_response(
    info: &NzbInfo,
    already_exists: bool,
    matches: Vec<serde_json::Value>,
    parsed: &parser::ParsedTitle,
) -> serde_json::Value {
    let files_json: Vec<serde_json::Value> = info
        .files
        .iter()
        .map(|f| json!({"filename": f.subject, "size": f.size}))
        .collect();

    json!({
        "nzb_guid": info.nzb_guid,
        "nzb_title": info.title,
        "total_size": info.total_size,
        "file_count": info.files.len(),
        "files": files_json,
        "group_name": info.group,
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
    })
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

pub async fn analyze_nzb_file(
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

    analyze_nzb_bytes(&state, &bytes, &meta_type).await
}

#[derive(Deserialize)]
pub struct NzbUrlBody {
    nzb_url: String,
    meta_type: Option<String>,
}

pub async fn analyze_nzb_url(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(body): Json<NzbUrlBody>,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }

    let resp = match state
        .http
        .get(&body.nzb_url)
        .timeout(std::time::Duration::from_secs(30))
        .send()
        .await
    {
        Ok(r) => r,
        Err(e) => {
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({"detail": format!("Failed to fetch NZB URL: {e}")})),
            )
                .into_response();
        }
    };

    let bytes = match resp.bytes().await {
        Ok(b) => b,
        Err(e) => {
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({"detail": format!("Failed to read NZB response: {e}")})),
            )
                .into_response();
        }
    };

    let meta_type = body.meta_type.as_deref().unwrap_or("movie").to_string();
    analyze_nzb_bytes(&state, &bytes, &meta_type).await
}

async fn analyze_nzb_bytes(state: &Arc<AppState>, bytes: &Bytes, meta_type: &str) -> Response {
    let info = match parse_nzb(bytes.as_ref()) {
        Ok(i) => i,
        Err(e) => {
            return (StatusCode::BAD_REQUEST, Json(json!({"detail": e}))).into_response();
        }
    };

    let already_exists: bool =
        sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM usenet_stream WHERE nzb_guid = $1)")
            .bind(&info.nzb_guid)
            .fetch_one(&state.pool)
            .await
            .unwrap_or(false);

    let parsed = parser::parse_title(&info.title);
    let search_title = parsed.title.as_deref().unwrap_or(&info.title);
    let matches =
        super::import_helpers::search_analyze_matches(state, search_title, parsed.year, meta_type)
            .await;

    (
        StatusCode::OK,
        Json(nzb_info_to_response(
            &info,
            already_exists,
            matches,
            &parsed,
        )),
    )
        .into_response()
}

pub async fn analyze_nzb_url_for_bot(
    state: &AppState,
    nzb_url: &str,
    meta_type: &str,
) -> serde_json::Value {
    let bytes = match state
        .http
        .get(nzb_url)
        .timeout(std::time::Duration::from_secs(30))
        .send()
        .await
    {
        Ok(r) if r.status().is_success() => match r.bytes().await {
            Ok(b) => b,
            Err(e) => {
                return json!({"success": false, "error": format!("Failed to read NZB: {e}")});
            }
        },
        Ok(r) => {
            return json!({
                "success": false,
                "error": format!("Failed to fetch NZB URL (HTTP {}).", r.status())
            });
        }
        Err(e) => {
            return json!({"success": false, "error": format!("Failed to fetch NZB URL: {e}")});
        }
    };

    let info = match parse_nzb(bytes.as_ref()) {
        Ok(i) => i,
        Err(e) => return json!({"success": false, "error": e}),
    };
    let parsed = parser::parse_title(&info.title);
    let search_title = parsed.title.as_deref().unwrap_or(&info.title);
    let matches =
        super::import_helpers::search_analyze_matches(state, search_title, parsed.year, meta_type)
            .await;
    let mut resp = nzb_info_to_response(&info, false, matches, &parsed);
    resp["success"] = json!(true);
    resp["parsed_title"] = json!(parsed.title);
    resp
}

pub async fn import_nzb(
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

    let mut file_bytes: Option<Bytes> = None;
    let mut meta_type = String::from("movie");
    let mut meta_id: Option<String> = None;
    let mut title: Option<String> = None;
    let mut indexer: Option<String> = None;
    let mut force_import = false;
    let mut is_anonymous_field: Option<bool> = None;
    let mut anonymous_display_name: Option<String> = None;
    let mut poster: Option<String> = None;
    let mut background: Option<String> = None;
    let mut release_date: Option<String> = None;

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
            Some("poster") => {
                poster = field.text().await.ok().filter(|s| !s.is_empty());
            }
            Some("background") => {
                background = field.text().await.ok().filter(|s| !s.is_empty());
            }
            Some("created_at") | Some("release_date") => {
                release_date = field.text().await.ok().filter(|s| !s.is_empty());
            }
            Some("indexer") => {
                indexer = field.text().await.ok().filter(|s| !s.is_empty());
            }
            Some("force_import") => {
                force_import = field
                    .text()
                    .await
                    .ok()
                    .map(|v| v == "true" || v == "1")
                    .unwrap_or(false);
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
                Json(json!({"detail": "Missing 'file' field"})),
            )
                .into_response();
        }
    };

    let info = match parse_nzb(&bytes) {
        Ok(i) => i,
        Err(e) => {
            return (StatusCode::BAD_REQUEST, Json(json!({"detail": e}))).into_response();
        }
    };

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

    do_nzb_import(
        &state,
        &info,
        None,
        &meta_type,
        meta_id.as_deref(),
        title.as_deref(),
        poster.as_deref(),
        background.as_deref(),
        release_date.as_deref(),
        indexer.as_deref(),
        force_import,
        uploader_name,
        uploader_user_id,
        is_privileged,
        auto_approve,
        resolved_is_anonymous,
    )
    .await
}

#[derive(Deserialize)]
pub struct ImportNzbUrlBody {
    nzb_url: String,
    meta_type: Option<String>,
    meta_id: Option<String>,
    title: Option<String>,
    poster: Option<String>,
    background: Option<String>,
    release_date: Option<String>,
    indexer: Option<String>,
    force_import: Option<bool>,
    is_anonymous: Option<bool>,
    anonymous_display_name: Option<String>,
}

pub async fn import_nzb_url(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(body): Json<ImportNzbUrlBody>,
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

    let resp = match state
        .http
        .get(&body.nzb_url)
        .timeout(std::time::Duration::from_secs(30))
        .send()
        .await
    {
        Ok(r) => r,
        Err(e) => {
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({"detail": format!("Failed to fetch NZB URL: {e}")})),
            )
                .into_response();
        }
    };

    let bytes = match resp.bytes().await {
        Ok(b) => b,
        Err(e) => {
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({"detail": format!("Failed to read NZB response: {e}")})),
            )
                .into_response();
        }
    };

    let info = match parse_nzb(&bytes) {
        Ok(i) => i,
        Err(e) => {
            return (StatusCode::BAD_REQUEST, Json(json!({"detail": e}))).into_response();
        }
    };

    let meta_type = body.meta_type.as_deref().unwrap_or("movie");
    let force_import = body.force_import.unwrap_or(false);

    let resolved_is_anonymous = body.is_anonymous.unwrap_or(user.contribute_anonymously);
    let (uploader_name, uploader_user_id) = resolve_uploader_identity(
        resolved_is_anonymous,
        body.anonymous_display_name.as_deref(),
        &user.username,
        user_id,
    );
    let is_privileged = matches!(user.role.as_str(), "moderator" | "admin");
    let auto_approve =
        should_auto_approve_import(is_privileged, user.is_active, resolved_is_anonymous);

    do_nzb_import(
        &state,
        &info,
        Some(body.nzb_url.as_str()),
        meta_type,
        body.meta_id.as_deref(),
        body.title.as_deref(),
        body.poster.as_deref(),
        body.background.as_deref(),
        body.release_date.as_deref(),
        body.indexer.as_deref(),
        force_import,
        uploader_name,
        uploader_user_id,
        is_privileged,
        auto_approve,
        resolved_is_anonymous,
    )
    .await
}

#[allow(clippy::too_many_arguments)]
async fn do_nzb_import(
    state: &Arc<AppState>,
    info: &NzbInfo,
    nzb_url: Option<&str>,
    meta_type: &str,
    meta_id: Option<&str>,
    title_override: Option<&str>,
    poster: Option<&str>,
    background: Option<&str>,
    release_date: Option<&str>,
    indexer: Option<&str>,
    force_import: bool,
    uploader_name: String,
    uploader_user_id: Option<i64>,
    is_privileged: bool,
    auto_approve: bool,
    resolved_is_anonymous: bool,
) -> Response {
    let name = title_override.unwrap_or(&info.title);

    // Adult content check
    if is_adult_content(name) {
        return (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({"detail": "Adult content is not allowed."})),
        )
            .into_response();
    }

    // Check duplicate
    let existing_id: Option<i64> =
        sqlx::query_scalar("SELECT stream_id FROM usenet_stream WHERE nzb_guid = $1 LIMIT 1")
            .bind(&info.nzb_guid)
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

    let parsed = parser::parse_title(name);

    let effective_meta_id = meta_id
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| super::import_helpers::synthetic_import_meta_id("nzb", &info.nzb_guid));

    let media_id = super::import_helpers::resolve_media_for_import(
        &state.pool,
        &state.http,
        state.config.tmdb_api_key.as_deref(),
        state.config.tvdb_api_key.as_deref(),
        &effective_meta_id,
        meta_type,
        crate::scrapers::media_resolve::ImportMediaOverrides {
            title: title_override.or(parsed.title.as_deref()),
            poster,
            background,
            release_date,
            year: parsed.year,
        },
        None,
    )
    .await
    .map(i64::from);

    let source = indexer.unwrap_or("manual");
    let size = if info.total_size > 0 {
        Some(info.total_size)
    } else {
        None
    };

    let stream_id = match insert_usenet_stream(
        &state.pool,
        &info.nzb_guid,
        nzb_url,
        name,
        source,
        size,
        indexer,
        info.group.as_deref(),
        &parsed,
        media_id,
        &uploader_name,
        uploader_user_id,
        auto_approve,
    )
    .await
    {
        Ok(id) => id,
        Err(e) => {
            tracing::error!("nzb import DB error: {e}");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "Database error"})),
            )
                .into_response();
        }
    };

    let file_data: Vec<serde_json::Value> = info
        .files
        .iter()
        .enumerate()
        .map(|(idx, f)| {
            json!({
                "index": idx,
                "filename": f.subject,
                "size": f.size,
            })
        })
        .collect();

    let data = serde_json::json!({
        "name": name,
        "title": name,
        "nzb_guid": info.nzb_guid,
        "nzb_url": nzb_url,
        "meta_type": meta_type,
        "meta_id": effective_meta_id,
        "total_size": info.total_size,
        "file_count": info.files.len().max(1),
        "file_data": file_data,
        "indexer": indexer.unwrap_or("manual"),
        "group_name": info.group,
        "resolution": parsed.resolution,
        "codec": parsed.codec,
        "quality": parsed.quality,
        "year": parsed.year,
        "poster": poster,
        "background": background,
        "release_date": release_date,
        "languages": parsed.languages.clone(),
        "uploader_name": uploader_name,
        "is_anonymous": resolved_is_anonymous,
        "is_public": auto_approve,
    });

    let mut contrib_id: Option<String> = None;
    if let Ok(cid) = create_contribution_record(
        &state.pool,
        uploader_user_id,
        "nzb",
        Some(&info.nzb_guid),
        &data,
        auto_approve,
        is_privileged,
    )
    .await
    {
        if auto_approve {
            if let Some(uid) = uploader_user_id {
                award_contribution_points(&state.pool, uid, "nzb").await;
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
                "nzb",
                &uploader_name,
                &data,
            )
            .await;
        }
        contrib_id = Some(cid);
    }

    let (status, message) = if auto_approve {
        ("success", "NZB imported successfully!".to_string())
    } else {
        (
            "pending",
            super::import_helpers::pending_import_message("NZB"),
        )
    };

    (
        StatusCode::CREATED,
        Json(json!({
            "status": status,
            "message": message,
            "import_id": stream_id,
            "contribution_id": contrib_id,
            "auto_approved": auto_approve,
        })),
    )
        .into_response()
}

// ─── Signed NZB download ──────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct DownloadQuery {
    expires: i64,
    sig: String,
}

pub async fn download_nzb(
    State(state): State<Arc<AppState>>,
    Path(guid): Path<String>,
    Query(params): Query<DownloadQuery>,
) -> Response {
    // Verify expiry
    let now = Utc::now().timestamp();
    if params.expires < now {
        return (StatusCode::GONE, Json(json!({"detail": "Link expired"}))).into_response();
    }

    // Verify HMAC-SHA256 signature
    let message = format!("{}:{}", guid, params.expires);
    let mut mac = match Hmac::<Sha256>::new_from_slice(state.config.secret_key_raw.as_bytes()) {
        Ok(m) => m,
        Err(_) => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "Server error"})),
            )
                .into_response();
        }
    };
    mac.update(message.as_bytes());
    let expected: String = mac
        .finalize()
        .into_bytes()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();

    if expected != params.sig {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Invalid signature"})),
        )
            .into_response();
    }

    // Fetch nzb_url
    let nzb_url: Option<String> =
        sqlx::query_scalar("SELECT nzb_url FROM usenet_stream WHERE nzb_guid = $1 LIMIT 1")
            .bind(&guid)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None)
            .flatten();

    match nzb_url {
        Some(url) => Redirect::temporary(&url).into_response(),
        None => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "NZB not found"})),
        )
            .into_response(),
    }
}
