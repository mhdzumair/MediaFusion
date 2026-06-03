/// Stream Linking endpoints — manage stream-to-media relationships.
///
/// Routes (prefix /api/v1/stream-links):
///   POST   /                                             → create_stream_link           (moderator)
///   POST   /bulk                                         → create_bulk_stream_links      (moderator)
///   DELETE /{link_id}                                    → delete_stream_link            (moderator)
///   GET    /stream/{stream_id}                           → get_media_for_stream
///   GET    /media/{media_id}                             → get_streams_for_media
///   GET    /search                                       → search_unlinked_streams       (moderator)
///   PUT    /files                                        → update_file_links             (moderator)
///   GET    /files/{stream_id}                            → get_stream_file_links         (auth)
///   GET    /stream/{stream_id}/files                     → get_stream_files_for_annotation (auth)
///   GET    /needs-annotation                             → get_streams_needing_annotation (moderator)
///   POST   /needs-annotation/{stream_id}/media/{media_id}/dismiss → dismiss_annotation_request (moderator)
use std::sync::Arc;

use axum::{
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::{DateTime, Utc};
use hmac::{Hmac, KeyInit, Mac};
use serde::Deserialize;
use serde_json::json;
use sha2::Sha256;

use crate::state::AppState;

// ─── Auth helpers ─────────────────────────────────────────────────────────────

fn validate_token(headers: &HeaderMap, secret_key: &str) -> Option<i32> {
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

// ─── Request / Response structs ───────────────────────────────────────────────

#[derive(Deserialize)]
pub struct StreamLinkCreate {
    pub stream_id: i32,
    pub media_id: i32,
    pub file_index: Option<i32>,
    pub season: Option<i32>,
    pub episode: Option<i32>,
}

#[derive(Deserialize)]
pub struct BulkLinkCreate {
    pub links: Vec<StreamLinkCreate>,
}

#[derive(Deserialize)]
pub struct FileLinkUpdate {
    pub file_id: i32,
    pub season_number: Option<i32>,
    pub episode_number: Option<i32>,
    pub episode_end: Option<i32>,
}

#[derive(Deserialize)]
pub struct BulkFileLinkUpdate {
    pub stream_id: i32,
    pub media_id: i32,
    pub updates: Vec<FileLinkUpdate>,
}

#[derive(Deserialize)]
pub struct SearchQuery {
    pub query: String,
    #[serde(default = "default_limit")]
    pub limit: i64,
}

fn default_limit() -> i64 {
    20
}

#[derive(Deserialize)]
pub struct StreamFilesQuery {
    pub media_id: i32,
}

#[derive(Deserialize)]
pub struct AnnotationQuery {
    #[serde(default = "default_page")]
    pub page: i64,
    #[serde(default = "default_per_page")]
    pub per_page: i64,
    pub search: Option<String>,
}

fn default_page() -> i64 {
    1
}
fn default_per_page() -> i64 {
    20
}

#[derive(Deserialize)]
pub struct DismissRequest {
    pub reason: Option<String>,
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// POST /api/v1/stream-links
pub async fn create_stream_link(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<StreamLinkCreate>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let role = crate::db::get_user_role(&state.pool, user_id).await;
    if !role.is_some_and(crate::db::is_mod_or_admin) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "Moderator role required"})),
        )
            .into_response();
    }

    let stream_exists: bool =
        sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM stream WHERE id = $1)")
            .bind(body.stream_id)
            .fetch_one(&state.pool)
            .await
            .unwrap_or(false);

    if !stream_exists {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Stream not found"})),
        )
            .into_response();
    }

    let media_exists: bool = sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM media WHERE id = $1)")
        .bind(body.media_id)
        .fetch_one(&state.pool)
        .await
        .unwrap_or(false);

    if !media_exists {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Media not found"})),
        )
            .into_response();
    }

    // Check if link already exists
    let existing: bool = sqlx::query_scalar(
        "SELECT EXISTS(SELECT 1 FROM stream_media_link WHERE stream_id = $1 AND media_id = $2 AND file_index IS NOT DISTINCT FROM $3)",
    )
    .bind(body.stream_id)
    .bind(body.media_id)
    .bind(body.file_index)
    .fetch_one(&state.pool)
    .await
    .unwrap_or(false);

    if existing {
        return (
            StatusCode::CONFLICT,
            Json(json!({"detail": "This link already exists"})),
        )
            .into_response();
    }

    let link_row: (i32, i32, i32, Option<i32>, DateTime<Utc>) = match sqlx::query_as(
        "INSERT INTO stream_media_link (stream_id, media_id, file_index, created_at) VALUES ($1, $2, $3, NOW()) RETURNING id, stream_id, media_id, file_index, created_at",
    )
    .bind(body.stream_id)
    .bind(body.media_id)
    .bind(body.file_index)
    .fetch_one(&state.pool)
    .await
    {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("create_stream_link: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    (
        StatusCode::CREATED,
        Json(json!({
            "id": link_row.0,
            "stream_id": link_row.1,
            "media_id": link_row.2,
            "file_index": link_row.3,
            "season": null,
            "episode": null,
            "linked_at": link_row.4.to_rfc3339(),
        })),
    )
        .into_response()
}

/// POST /api/v1/stream-links/bulk
pub async fn create_bulk_stream_links(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<BulkLinkCreate>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let role = crate::db::get_user_role(&state.pool, user_id).await;
    if !role.is_some_and(crate::db::is_mod_or_admin) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "Moderator role required"})),
        )
            .into_response();
    }

    if body.links.is_empty() || body.links.len() > 50 {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "links must have between 1 and 50 entries"})),
        )
            .into_response();
    }

    let mut created = 0i64;
    let mut failed = 0i64;
    let mut errors: Vec<String> = Vec::new();

    for link_req in &body.links {
        // Verify stream
        let stream_exists: bool =
            sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM stream WHERE id = $1)")
                .bind(link_req.stream_id)
                .fetch_one(&state.pool)
                .await
                .unwrap_or(false);

        if !stream_exists {
            errors.push(format!("Stream {} not found", link_req.stream_id));
            failed += 1;
            continue;
        }

        // Verify media
        let media_exists: bool =
            sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM media WHERE id = $1)")
                .bind(link_req.media_id)
                .fetch_one(&state.pool)
                .await
                .unwrap_or(false);

        if !media_exists {
            errors.push(format!("Media {} not found", link_req.media_id));
            failed += 1;
            continue;
        }

        // Check duplicate
        let existing: bool = sqlx::query_scalar(
            "SELECT EXISTS(SELECT 1 FROM stream_media_link WHERE stream_id = $1 AND media_id = $2 AND file_index IS NOT DISTINCT FROM $3)",
        )
        .bind(link_req.stream_id)
        .bind(link_req.media_id)
        .bind(link_req.file_index)
        .fetch_one(&state.pool)
        .await
        .unwrap_or(false);

        if existing {
            errors.push(format!(
                "Link already exists: stream {} -> media {}",
                link_req.stream_id, link_req.media_id
            ));
            failed += 1;
            continue;
        }

        match sqlx::query(
            "INSERT INTO stream_media_link (stream_id, media_id, file_index, created_at) VALUES ($1, $2, $3, NOW())",
        )
        .bind(link_req.stream_id)
        .bind(link_req.media_id)
        .bind(link_req.file_index)
        .execute(&state.pool)
        .await
        {
            Ok(_) => created += 1,
            Err(e) => {
                errors.push(format!("Error linking stream {} -> media {}: {}", link_req.stream_id, link_req.media_id, e));
                failed += 1;
            }
        }
    }

    Json(json!({
        "created": created,
        "failed": failed,
        "errors": &errors[..errors.len().min(10)],
    }))
    .into_response()
}

/// DELETE /api/v1/stream-links/{link_id}
pub async fn delete_stream_link(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(link_id): Path<i32>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let role = crate::db::get_user_role(&state.pool, user_id).await;
    if !role.is_some_and(crate::db::is_mod_or_admin) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "Moderator role required"})),
        )
            .into_response();
    }

    let link: Option<(i32, i32)> =
        sqlx::query_as("SELECT stream_id, media_id FROM stream_media_link WHERE id = $1")
            .bind(link_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);

    if link.is_none() {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Link not found"})),
        )
            .into_response();
    }

    match sqlx::query("DELETE FROM stream_media_link WHERE id = $1")
        .bind(link_id)
        .execute(&state.pool)
        .await
    {
        Ok(_) => StatusCode::NO_CONTENT.into_response(),
        Err(e) => {
            tracing::error!("delete_stream_link: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

/// GET /api/v1/stream-links/stream/{stream_id}
pub async fn get_media_for_stream(
    State(state): State<Arc<AppState>>,
    Path(stream_id): Path<i32>,
) -> Response {
    let links: Vec<(i32, i32, Option<i32>)> = sqlx::query_as(
        "SELECT id, media_id, file_index FROM stream_media_link WHERE stream_id = $1",
    )
    .bind(stream_id)
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    let mut media_entries = Vec::new();
    for (link_id, media_id, file_index) in links {
        let media_row: Option<(String, Option<i32>, crate::db::MediaType)> =
            sqlx::query_as("SELECT title, year, type FROM media WHERE id = $1")
                .bind(media_id)
                .fetch_optional(&state.pool_ro)
                .await
                .unwrap_or(None);

        if let Some((title, year, mtype)) = media_row {
            // Get canonical external ID (prefer imdb)
            let ext_id: Option<String> = sqlx::query_scalar(
                "SELECT external_id FROM media_external_id WHERE media_id = $1 AND provider = 'imdb' LIMIT 1",
            )
            .bind(media_id)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None);

            media_entries.push(json!({
                "link_id": link_id,
                "media_id": media_id,
                "external_id": ext_id,
                "title": title,
                "year": year,
                "type": mtype.as_wire(),
                "file_index": file_index,
            }));
        }
    }

    Json(json!({
        "stream_id": stream_id,
        "media_entries": media_entries,
    }))
    .into_response()
}

/// GET /api/v1/stream-links/media/{media_id}
pub async fn get_streams_for_media(
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i32>,
) -> Response {
    let links: Vec<(i32, i32, Option<i32>)> = sqlx::query_as(
        "SELECT id, stream_id, file_index FROM stream_media_link WHERE media_id = $1",
    )
    .bind(media_id)
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    let mut streams = Vec::new();
    for (link_id, stream_id, file_index) in links {
        let stream_row: Option<(Option<String>, crate::db::StreamType, Option<String>)> =
            sqlx::query_as("SELECT name, stream_type, resolution FROM stream WHERE id = $1")
                .bind(stream_id)
                .fetch_optional(&state.pool_ro)
                .await
                .unwrap_or(None);

        if let Some((name, stype, resolution)) = stream_row {
            let size: Option<i64> =
                sqlx::query_scalar("SELECT total_size FROM torrent_stream WHERE stream_id = $1")
                    .bind(stream_id)
                    .fetch_optional(&state.pool_ro)
                    .await
                    .unwrap_or(None);

            streams.push(json!({
                "link_id": link_id,
                "stream_id": stream_id,
                "name": name,
                "type": stype.as_wire().to_lowercase(),
                "size": size,
                "resolution": resolution,
                "file_index": file_index,
                "season": null,
                "episode": null,
            }));
        }
    }

    Json(json!({
        "media_id": media_id,
        "streams": streams,
    }))
    .into_response()
}

/// GET /api/v1/stream-links/search  (moderator)
pub async fn search_unlinked_streams(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<SearchQuery>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let role = crate::db::get_user_role(&state.pool_ro, user_id).await;
    if !role.is_some_and(crate::db::is_mod_or_admin) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "Moderator role required"})),
        )
            .into_response();
    }

    let limit = params.limit.clamp(1, 100);
    let pattern = format!("%{}%", params.query);

    let stream_ids: Vec<(i32,)> =
        sqlx::query_as("SELECT id FROM stream WHERE name ILIKE $1 LIMIT $2")
            .bind(&pattern)
            .bind(limit)
            .fetch_all(&state.pool_ro)
            .await
            .unwrap_or_default();

    let mut results = Vec::new();
    for (stream_id,) in stream_ids {
        let stream_row: Option<(Option<String>, crate::db::StreamType, Option<String>)> =
            sqlx::query_as("SELECT name, stream_type, resolution FROM stream WHERE id = $1")
                .bind(stream_id)
                .fetch_optional(&state.pool_ro)
                .await
                .unwrap_or(None);

        if let Some((name, stype, _res)) = stream_row {
            let links: Vec<(i32, Option<i32>)> = sqlx::query_as(
                "SELECT media_id, file_index FROM stream_media_link WHERE stream_id = $1",
            )
            .bind(stream_id)
            .fetch_all(&state.pool_ro)
            .await
            .unwrap_or_default();

            let size: Option<i64> =
                sqlx::query_scalar("SELECT total_size FROM torrent_stream WHERE stream_id = $1")
                    .bind(stream_id)
                    .fetch_optional(&state.pool_ro)
                    .await
                    .unwrap_or(None);

            let link_count = links.len();
            let links_json: Vec<serde_json::Value> = links
                .into_iter()
                .map(|(mid, fi)| json!({"media_id": mid, "file_index": fi}))
                .collect();

            results.push(json!({
                "stream_id": stream_id,
                "name": name,
                "type": stype.as_wire().to_lowercase(),
                "size": size,
                "link_count": link_count,
                "links": links_json,
            }));
        }
    }

    Json(json!({"results": results, "total": results.len()})).into_response()
}

/// PUT /api/v1/stream-links/files  (moderator)
pub async fn update_file_links(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<BulkFileLinkUpdate>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let role = crate::db::get_user_role(&state.pool, user_id).await;
    if !role.is_some_and(crate::db::is_mod_or_admin) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "Moderator role required"})),
        )
            .into_response();
    }

    let stream_exists: bool =
        sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM stream WHERE id = $1)")
            .bind(body.stream_id)
            .fetch_one(&state.pool)
            .await
            .unwrap_or(false);

    if !stream_exists {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Stream not found"})),
        )
            .into_response();
    }

    let media_type: Option<crate::db::MediaType> =
        sqlx::query_scalar("SELECT type FROM media WHERE id = $1")
            .bind(body.media_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);

    match media_type {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Media not found"})),
            )
                .into_response()
        }
        Some(crate::db::MediaType::Series) => {}
        Some(_) => {
            return (StatusCode::BAD_REQUEST, Json(json!({"detail": "File annotation updates are only supported for series media"}))).into_response();
        }
    }

    let mut updated = 0i64;
    let mut failed = 0i64;
    let mut errors: Vec<String> = Vec::new();

    for update in &body.updates {
        // Verify file belongs to this stream
        let file_exists: bool = sqlx::query_scalar(
            "SELECT EXISTS(SELECT 1 FROM stream_file WHERE id = $1 AND stream_id = $2)",
        )
        .bind(update.file_id)
        .bind(body.stream_id)
        .fetch_one(&state.pool)
        .await
        .unwrap_or(false);

        if !file_exists {
            errors.push(format!("File {} not found in this stream", update.file_id));
            failed += 1;
            continue;
        }

        // Check if link exists
        let existing_link: Option<i32> = sqlx::query_scalar(
            "SELECT id FROM file_media_link WHERE file_id = $1 AND media_id = $2",
        )
        .bind(update.file_id)
        .bind(body.media_id)
        .fetch_optional(&state.pool)
        .await
        .unwrap_or(None);

        let result = if let Some(link_id) = existing_link {
            sqlx::query(
                "UPDATE file_media_link SET season_number = $1, episode_number = $2, episode_end = $3, updated_at = NOW() WHERE id = $4",
            )
            .bind(update.season_number)
            .bind(update.episode_number)
            .bind(update.episode_end)
            .bind(link_id)
            .execute(&state.pool)
            .await
        } else {
            sqlx::query(
                "INSERT INTO file_media_link (file_id, media_id, season_number, episode_number, episode_end, created_at, is_primary, confidence, link_source) VALUES ($1, $2, $3, $4, $5, NOW(), true, 1.0, 'MANUAL')",
            )
            .bind(update.file_id)
            .bind(body.media_id)
            .bind(update.season_number)
            .bind(update.episode_number)
            .bind(update.episode_end)
            .execute(&state.pool)
            .await
        };

        match result {
            Ok(_) => updated += 1,
            Err(e) => {
                errors.push(format!("Failed to update file {}: {}", update.file_id, e));
                failed += 1;
            }
        }
    }

    Json(json!({
        "updated": updated,
        "failed": failed,
        "errors": errors,
    }))
    .into_response()
}

/// GET /api/v1/stream-links/files/{stream_id}  (auth)
#[allow(clippy::type_complexity)]
pub async fn get_stream_file_links(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(stream_id): Path<i32>,
    Query(params): Query<StreamFilesQuery>,
) -> Response {
    let _user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let stream_exists: bool =
        sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM stream WHERE id = $1)")
            .bind(stream_id)
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(false);

    if !stream_exists {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Stream not found"})),
        )
            .into_response();
    }

    // Get all files for this stream
    let file_rows: Vec<(i32, Option<String>, Option<i32>, Option<i64>)> = sqlx::query_as(
        "SELECT id, filename, file_index, size FROM stream_file WHERE stream_id = $1 ORDER BY filename ASC",
    )
    .bind(stream_id)
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    let mut files = Vec::new();
    for (file_id, filename, file_index, size) in file_rows {
        let link: Option<(Option<i32>, Option<i32>, Option<i32>)> = sqlx::query_as(
            "SELECT season_number, episode_number, episode_end FROM file_media_link WHERE file_id = $1 AND media_id = $2 LIMIT 1",
        )
        .bind(file_id)
        .bind(params.media_id)
        .fetch_optional(&state.pool_ro)
        .await
        .unwrap_or(None);

        // Check if this file is linked to any media at all
        let has_links: bool =
            sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM file_media_link WHERE file_id = $1)")
                .bind(file_id)
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(false);

        // Skip files linked to other media
        if has_links && link.is_none() {
            continue;
        }

        let display_name = filename
            .clone()
            .unwrap_or_else(|| format!("File {}", file_index.unwrap_or(file_id)));

        files.push(json!({
            "file_id": file_id,
            "file_name": display_name,
            "file_index": file_index,
            "size": size,
            "season_number": link.as_ref().and_then(|l| l.0),
            "episode_number": link.as_ref().and_then(|l| l.1),
            "episode_end": link.as_ref().and_then(|l| l.2),
        }));
    }

    Json(json!({
        "stream_id": stream_id,
        "media_id": params.media_id,
        "files": files,
        "total": files.len(),
    }))
    .into_response()
}

/// GET /api/v1/stream-links/stream/{stream_id}/files  (auth)
#[allow(clippy::type_complexity)]
pub async fn get_stream_files_for_annotation(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(stream_id): Path<String>,
) -> Response {
    let _user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let sid: i32 = match stream_id.parse() {
        Ok(id) => id,
        Err(_) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Invalid stream ID"})),
            )
                .into_response()
        }
    };

    let stream_exists: bool =
        sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM stream WHERE id = $1)")
            .bind(sid)
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(false);

    if !stream_exists {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Stream not found"})),
        )
            .into_response();
    }

    let file_rows: Vec<(i32, Option<String>, Option<i32>, Option<i64>)> = sqlx::query_as(
        "SELECT id, filename, file_index, size FROM stream_file WHERE stream_id = $1 ORDER BY filename ASC",
    )
    .bind(sid)
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    if file_rows.is_empty() {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "No files found for this stream"})),
        )
            .into_response();
    }

    let mut files = Vec::new();
    for (file_id, filename, file_index, size) in file_rows {
        // Get first media link if any
        let link: Option<(Option<i32>, Option<i32>, Option<i32>)> = sqlx::query_as(
            "SELECT season_number, episode_number, episode_end FROM file_media_link WHERE file_id = $1 ORDER BY id ASC LIMIT 1",
        )
        .bind(file_id)
        .fetch_optional(&state.pool_ro)
        .await
        .unwrap_or(None);

        let display_name =
            filename.unwrap_or_else(|| format!("File {}", file_index.unwrap_or(file_id)));

        files.push(json!({
            "file_id": file_id,
            "file_name": display_name,
            "size": size,
            "season_number": link.as_ref().and_then(|l| l.0),
            "episode_number": link.as_ref().and_then(|l| l.1),
            "episode_end": link.as_ref().and_then(|l| l.2),
        }));
    }

    Json(json!(files)).into_response()
}

/// GET /api/v1/stream-links/needs-annotation  (moderator)
#[allow(clippy::type_complexity)]
pub async fn get_streams_needing_annotation(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<AnnotationQuery>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let role = crate::db::get_user_role(&state.pool_ro, user_id).await;
    if !role.is_some_and(crate::db::is_mod_or_admin) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "Moderator role required"})),
        )
            .into_response();
    }

    let page = params.page.max(1);
    let per_page = params.per_page.clamp(1, 100);
    let offset = (page - 1) * per_page;

    // $3: optional ILIKE pattern — NULL disables the filter (avoids SQL injection via format!).
    let search_pattern: Option<String> = params.search.map(|s| format!("%{}%", s));

    // Start from stream_media_link (one row per stream-media pair — no DISTINCT needed).
    // EXISTS/NOT EXISTS terminate early and use existing indexes:
    //   idx_stream_file_stream, ix_file_media_link_file_id, unique(stream_id,media_id) on dismissal.
    //
    // When a search pattern is present the OR is split into a UNION so each branch
    // can use its own GIN trigram index (idx_stream_name_trgm / idx_media_title_trgm)
    // instead of forcing a cross-join seq-scan over the 5 GB stream table.
    type Row = (
        i32,
        Option<String>,
        Option<String>,
        Option<i64>,
        Option<String>,
        DateTime<Utc>,
        Option<String>,
        i32,
        String,
        Option<i32>,
        crate::db::MediaType,
        Option<String>,
        i64,
    );

    const BASE_FROM: &str = r#"
            FROM stream_media_link sml
            INNER JOIN stream s ON s.id = sml.stream_id
                AND s.is_active = true
                AND s.is_blocked = false
            INNER JOIN media m ON m.id = sml.media_id
                AND m.type = 'SERIES'
            LEFT JOIN torrent_stream ts ON ts.stream_id = s.id
            LEFT JOIN LATERAL (
                SELECT mi.url FROM media_image mi
                WHERE mi.media_id = m.id AND mi.image_type = 'poster'
                ORDER BY mi.is_primary DESC, mi.display_order ASC LIMIT 1
            ) img ON true
            WHERE EXISTS (
                SELECT 1 FROM stream_file sf
                WHERE sf.stream_id = sml.stream_id
                  AND NOT EXISTS (
                      SELECT 1 FROM file_media_link fml WHERE fml.file_id = sf.id
                  )
            )
            AND NOT EXISTS (
                SELECT 1 FROM annotation_request_dismissal ard
                WHERE ard.stream_id = sml.stream_id AND ard.media_id = sml.media_id
            )"#;

    const INNER_COLS: &str =
        "s.id, s.name, s.source, ts.total_size, s.resolution, s.created_at, \
         ts.info_hash, m.id AS media_id, m.title, m.year, m.type AS media_type, img.url";

    let rows: Vec<Row> = if let Some(ref pat) = search_pattern {
        sqlx::query_as::<_, Row>(&format!(
            r#"SELECT id, name, source, total_size, resolution, created_at, info_hash,
                      media_id, title, year, media_type, url,
                      COUNT(*) OVER() AS total_count
               FROM (
                   (SELECT {INNER_COLS}{BASE_FROM} AND s.name  ILIKE $3)
                   UNION
                   (SELECT {INNER_COLS}{BASE_FROM} AND m.title ILIKE $3)
               ) sub
               ORDER BY created_at DESC
               LIMIT $1 OFFSET $2"#
        ))
        .bind(per_page)
        .bind(offset)
        .bind(pat)
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default()
    } else {
        sqlx::query_as::<_, Row>(&format!(
            r#"SELECT
                    s.id, s.name, s.source, ts.total_size, s.resolution, s.created_at,
                    ts.info_hash, m.id, m.title, m.year, m.type, img.url,
                    COUNT(*) OVER() AS total_count
               {BASE_FROM}
               ORDER BY s.created_at DESC
               LIMIT $1 OFFSET $2"#
        ))
        .bind(per_page)
        .bind(offset)
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default()
    };

    if rows.is_empty() {
        return Json(json!({
            "items": [],
            "total": 0,
            "page": page,
            "per_page": per_page,
            "pages": 1,
        }))
        .into_response();
    }

    let total = rows.first().map(|r| r.12).unwrap_or(0);
    let pages = if total > 0 {
        (total + per_page - 1) / per_page
    } else {
        1
    };

    let items: Vec<serde_json::Value> = rows
        .into_iter()
        .map(
            |(
                sid,
                sname,
                source,
                size,
                resolution,
                created_at,
                info_hash,
                media_id,
                media_title,
                media_year,
                media_type,
                media_poster,
                _,
            )| {
                json!({
                    "stream_id": sid,
                    "stream_name": sname,
                    "source": source,
                    "size": size,
                    "resolution": resolution,
                    "info_hash": info_hash,
                    "file_count": null,
                    "unmapped_count": null,
                    "created_at": created_at.to_rfc3339(),
                    "media_id": media_id,
                    "media_title": media_title,
                    "media_year": media_year,
                    "media_type": media_type.as_wire(),
                    "media_external_id": null,
                    "media_poster": media_poster,
                })
            },
        )
        .collect();

    Json(json!({
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }))
    .into_response()
}

/// POST /api/v1/stream-links/needs-annotation/{stream_id}/media/{media_id}/dismiss  (moderator)
pub async fn dismiss_annotation_request(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path((stream_id, media_id)): Path<(i32, i32)>,
    Json(body): Json<DismissRequest>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let role = crate::db::get_user_role(&state.pool, user_id).await;
    if !role.is_some_and(crate::db::is_mod_or_admin) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "Moderator role required"})),
        )
            .into_response();
    }

    let stream_exists: bool =
        sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM stream WHERE id = $1)")
            .bind(stream_id)
            .fetch_one(&state.pool)
            .await
            .unwrap_or(false);

    if !stream_exists {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Stream not found"})),
        )
            .into_response();
    }

    let media_exists: bool = sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM media WHERE id = $1)")
        .bind(media_id)
        .fetch_one(&state.pool)
        .await
        .unwrap_or(false);

    if !media_exists {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Media not found"})),
        )
            .into_response();
    }

    let reason = body
        .reason
        .as_ref()
        .map(|r| r.trim().to_string())
        .filter(|r| !r.is_empty());

    let dismissed_at: DateTime<Utc> = match sqlx::query_scalar(
        r#"INSERT INTO annotation_request_dismissal
               (stream_id, media_id, dismissed_by, dismiss_reason, dismissed_at)
           VALUES ($1, $2, $3, $4, NOW())
           ON CONFLICT (stream_id, media_id)
           DO UPDATE SET dismissed_by = $3, dismiss_reason = $4, dismissed_at = NOW()
           RETURNING dismissed_at"#,
    )
    .bind(stream_id)
    .bind(media_id)
    .bind(user_id.to_string())
    .bind(&reason)
    .fetch_one(&state.pool)
    .await
    {
        Ok(d) => d,
        Err(e) => {
            tracing::error!("dismiss_annotation_request: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    Json(json!({
        "status": "success",
        "stream_id": stream_id,
        "media_id": media_id,
        "dismissed_at": dismissed_at.to_rfc3339(),
    }))
    .into_response()
}
