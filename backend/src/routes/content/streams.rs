/// Content stream management endpoints.
///
/// Routes (prefix /api/v1/streams):
///   GET    /mine                    → list_my_streams
///   PATCH  /{stream_id}             → update_my_stream  (owner)
///   POST   /{stream_id}/block       → block_my_stream   (owner, one-way)
///   DELETE /{stream_id}             → delete_stream     (owner or moderator)
use std::sync::Arc;

use axum::{
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use serde::Deserialize;
use serde_json::json;

use crate::{
    routes::auth_guard::{self, AuthFailure},
    state::AppState,
};

use super::stream_rows::{
    my_stream_row_to_json, MyStreamRow, STREAM_BASE_COLS, STREAM_LINK_AGG_COLS,
};
use super::stream_suggestions::apply_stream_field_change;

// ─── Auth helpers ─────────────────────────────────────────────────────────────

fn validate_token(headers: &HeaderMap, secret_key: &str) -> Option<i32> {
    auth_guard::decode_access_token(headers, secret_key)
        .ok()
        .map(|(id, _)| id)
}

// ─── Request structs ──────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct ListMyStreamsQuery {
    #[serde(default = "default_page")]
    pub page: i64,
    #[serde(default = "default_page_size")]
    pub page_size: i64,
    pub status: Option<String>,
    pub search: Option<String>,
    pub stream_type: Option<String>,
}

fn default_page() -> i64 {
    1
}

fn default_page_size() -> i64 {
    20
}

#[derive(Deserialize)]
pub struct UpdateMyStreamRequest {
    pub name: Option<String>,
    pub resolution: Option<String>,
    pub quality: Option<String>,
    pub codec: Option<String>,
    pub bit_depth: Option<String>,
    pub source: Option<String>,
    pub languages: Option<Vec<String>>,
    pub audio_formats: Option<Vec<String>>,
    pub hdr_formats: Option<Vec<String>>,
}

// ─── Ownership helpers ────────────────────────────────────────────────────────

/// Returns `Ok(None)` when the stream does not exist, `Ok(Some(uploader))` when it does.
async fn stream_uploader_user_id(
    pool: &sqlx::PgPool,
    stream_id: i32,
) -> Result<Option<Option<i32>>, ()> {
    sqlx::query_scalar("SELECT uploader_user_id FROM stream WHERE id = $1")
        .bind(stream_id)
        .fetch_optional(pool)
        .await
        .map_err(|e| {
            tracing::error!("DB error fetching uploader for stream {stream_id}: {e}");
        })
}

async fn require_stream_owner(
    pool: &sqlx::PgPool,
    stream_id: i32,
    user_id: i32,
) -> Result<(), Response> {
    let uploader = match stream_uploader_user_id(pool, stream_id).await {
        Ok(v) => v,
        Err(()) => {
            return Err((
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "Database error"})),
            )
                .into_response());
        }
    };

    match uploader {
        None => Err((
            StatusCode::NOT_FOUND,
            Json(json!({"error": "Stream not found"})),
        )
            .into_response()),
        Some(None) => Err((
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "You do not own this stream"})),
        )
            .into_response()),
        Some(Some(owner_id)) if owner_id == user_id => Ok(()),
        Some(Some(_)) => Err((
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "You do not own this stream"})),
        )
            .into_response()),
    }
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// GET /api/v1/streams/mine
pub async fn list_my_streams(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Query(params): Query<ListMyStreamsQuery>,
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

    let page = params.page.max(1);
    let page_size = params.page_size.clamp(1, 100);
    let offset = (page - 1) * page_size;

    let status_filter = params.status.as_deref().map(|s| s.to_lowercase());
    let search = params
        .search
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(|s| format!("%{s}%"));
    let stream_type_filter = params
        .stream_type
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_lowercase);

    let mut filters = String::from("WHERE s.uploader_user_id = $1");
    let mut bind_values: Vec<String> = Vec::new();
    let mut next_idx = 2i32;

    match status_filter.as_deref() {
        Some("active") => filters.push_str(" AND s.is_blocked = false AND s.is_active = true"),
        Some("blocked") => filters.push_str(" AND s.is_blocked = true"),
        Some("inactive") => filters.push_str(" AND s.is_active = false AND s.is_blocked = false"),
        _ => {}
    }

    if let Some(ref st) = stream_type_filter {
        filters.push_str(&format!(" AND lower(s.stream_type::text) = ${next_idx}"));
        bind_values.push(st.clone());
        next_idx += 1;
    }

    if search.is_some() {
        filters.push_str(&format!(
            " AND (s.name ILIKE ${next_idx} OR COALESCE(m.title, '') ILIKE ${next_idx} \
             OR COALESCE(ts.info_hash, '') ILIKE ${next_idx} OR COALESCE(s.uploader, '') ILIKE ${next_idx})"
        ));
        next_idx += 1;
    }

    let from_joins = format!(
        r#"FROM stream s
           LEFT JOIN LATERAL (
               SELECT sml2.media_id, sml2.file_size
               FROM stream_media_link sml2
               WHERE sml2.stream_id = s.id
               ORDER BY sml2.is_primary DESC, sml2.id ASC
               LIMIT 1
           ) sml ON true
           LEFT JOIN media m ON m.id = sml.media_id
           LEFT JOIN torrent_stream ts ON ts.stream_id = s.id
           LEFT JOIN youtube_stream ys ON ys.stream_id = s.id
           {filters}"#
    );

    let count_sql = format!("SELECT COUNT(*) {from_joins}");
    let mut count_query = sqlx::query_scalar::<_, i64>(&count_sql).bind(user_id);
    for v in &bind_values {
        count_query = count_query.bind(v.clone());
    }
    if let Some(ref pattern) = search {
        count_query = count_query.bind(pattern.clone());
    }

    let total: i64 = match count_query.fetch_one(&state.pool_ro).await {
        Ok(v) => v,
        Err(e) => {
            tracing::error!("list_my_streams count failed for user {user_id}: {e}");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "Database error"})),
            )
                .into_response();
        }
    };

    let list_sql = format!(
        r#"SELECT
            {STREAM_BASE_COLS},
            (SELECT sf.filename FROM stream_file sf WHERE sf.stream_id = s.id LIMIT 1) AS filename,
            COALESCE(ts.total_size, sml.file_size) AS file_size,
            ts.info_hash,
            ys.video_id AS yt_id,
            {STREAM_LINK_AGG_COLS},
            s.is_blocked,
            s.is_active,
            s.is_public,
            sml.media_id,
            m.title AS media_title,
            m.type::text AS media_type,
            (SELECT url FROM media_image mi
             WHERE mi.media_id = m.id AND mi.image_type = 'poster' AND mi.is_primary = true
             LIMIT 1) AS media_poster_url,
            (SELECT mei.external_id FROM media_external_id mei
             WHERE mei.media_id = m.id AND mei.provider = 'imdb'
             LIMIT 1) AS media_imdb_id,
            (SELECT COUNT(*)::bigint FROM stream_file sf WHERE sf.stream_id = s.id) AS file_count,
            s.created_at
           {from_joins}
           ORDER BY s.created_at DESC
           LIMIT ${next_idx} OFFSET ${}"#,
        next_idx + 1
    );

    let mut list_query = sqlx::query_as::<_, MyStreamRow>(&list_sql).bind(user_id);
    for v in &bind_values {
        list_query = list_query.bind(v.clone());
    }
    if let Some(ref pattern) = search {
        list_query = list_query.bind(pattern.clone());
    }
    list_query = list_query.bind(page_size).bind(offset);

    let rows: Vec<MyStreamRow> = match list_query.fetch_all(&state.pool_ro).await {
        Ok(v) => v,
        Err(e) => {
            tracing::error!("list_my_streams query failed for user {user_id}: {e}");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "Database error"})),
            )
                .into_response();
        }
    };

    let items: Vec<serde_json::Value> = rows.iter().map(my_stream_row_to_json).collect();
    let has_more = offset + (rows.len() as i64) < total;

    Json(json!({
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": has_more,
    }))
    .into_response()
}

/// PATCH /api/v1/streams/{stream_id}
pub async fn update_my_stream(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(stream_id): Path<i32>,
    Json(body): Json<UpdateMyStreamRequest>,
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

    if let Err(resp) = require_stream_owner(&state.pool, stream_id, user_id).await {
        return resp;
    }

    let mut updates: Vec<(&str, String)> = Vec::new();

    if let Some(v) = body.name {
        updates.push(("name", v));
    }
    if let Some(v) = body.resolution {
        updates.push(("resolution", v));
    }
    if let Some(v) = body.quality {
        updates.push(("quality", v));
    }
    if let Some(v) = body.codec {
        updates.push(("codec", v));
    }
    if let Some(v) = body.bit_depth {
        updates.push(("bit_depth", v));
    }
    if let Some(v) = body.source {
        updates.push(("source", v));
    }
    if let Some(v) = body.languages {
        updates.push((
            "languages",
            serde_json::to_string(&v).unwrap_or_else(|_| "[]".into()),
        ));
    }
    if let Some(v) = body.audio_formats {
        updates.push((
            "audio_formats",
            serde_json::to_string(&v).unwrap_or_else(|_| "[]".into()),
        ));
    }
    if let Some(v) = body.hdr_formats {
        updates.push((
            "hdr_formats",
            serde_json::to_string(&v).unwrap_or_else(|_| "[]".into()),
        ));
    }

    if updates.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "No fields to update"})),
        )
            .into_response();
    }

    for (field, value) in &updates {
        apply_stream_field_change(
            &state.pool,
            stream_id,
            "field_correction",
            Some(field),
            Some(value.as_str()),
        )
        .await;
    }

    (
        StatusCode::OK,
        Json(json!({
            "stream_id": stream_id,
            "message": "Stream updated",
            "updated_fields": updates.iter().map(|(f, _)| *f).collect::<Vec<_>>(),
        })),
    )
        .into_response()
}

/// POST /api/v1/streams/{stream_id}/block
pub async fn block_my_stream(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(stream_id): Path<i32>,
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

    if let Err(resp) = require_stream_owner(&state.pool, stream_id, user_id).await {
        return resp;
    }

    if let Err(e) = sqlx::query(
        "UPDATE stream SET is_blocked = true, is_active = false WHERE id = $1 AND uploader_user_id = $2",
    )
    .bind(stream_id)
    .bind(user_id)
    .execute(&state.pool)
    .await
    {
        tracing::error!("block_my_stream failed for stream {stream_id}: {e}");
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "Database error"})),
        )
            .into_response();
    }

    (
        StatusCode::OK,
        Json(json!({
            "stream_id": stream_id,
            "is_blocked": true,
            "message": "Stream blocked. Only a moderator can restore it.",
        })),
    )
        .into_response()
}

pub async fn delete_stream(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(stream_id): Path<i32>,
) -> Response {
    let user_id = match auth_guard::decode_access_token(&headers, &state.config.secret_key_raw) {
        Ok((id, role)) => (id, role),
        Err(AuthFailure::Unauthorized) => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
        Err(AuthFailure::Forbidden) => {
            return (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response();
        }
    };

    let (user_id, role) = user_id;
    let is_privileged = matches!(role.as_str(), "moderator" | "admin");

    let uploader = match stream_uploader_user_id(&state.pool, stream_id).await {
        Ok(v) => v,
        Err(()) => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "Database error"})),
            )
                .into_response();
        }
    };

    let uploader_user_id = match uploader {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"error": "Stream not found"})),
            )
                .into_response();
        }
        Some(uid) => uid,
    };

    let is_owner = uploader_user_id == Some(user_id);
    if !is_privileged && !is_owner {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "You do not have permission to delete this stream"})),
        )
            .into_response();
    }

    let stream_id_i32 = stream_id;

    // Check stream exists and get stream_type
    let stream_type: Option<crate::db::StreamType> =
        match sqlx::query_scalar("SELECT stream_type FROM stream WHERE id = $1")
            .bind(stream_id_i32)
            .fetch_optional(&state.pool)
            .await
        {
            Ok(v) => v,
            Err(e) => {
                tracing::error!("DB error checking stream {stream_id}: {e}");
                return (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({"error": "Database error"})),
                )
                    .into_response();
            }
        };

    let stream_type = match stream_type {
        Some(t) => t,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"error": "Stream not found"})),
            )
                .into_response();
        }
    };

    // Begin transaction
    let mut txn = match state.pool.begin().await {
        Ok(t) => t,
        Err(e) => {
            tracing::error!("DB error starting transaction: {e}");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "Database error"})),
            )
                .into_response();
        }
    };

    // Fetch linked media IDs, then decrement total_streams
    let media_ids: Vec<i32> =
        match sqlx::query_scalar("SELECT media_id FROM stream_media_link WHERE stream_id = $1")
            .bind(stream_id_i32)
            .fetch_all(&mut *txn)
            .await
        {
            Ok(v) => v,
            Err(e) => {
                tracing::error!("DB error fetching media links for stream {stream_id}: {e}");
                return (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({"error": "Database error"})),
                )
                    .into_response();
            }
        };

    for media_id in &media_ids {
        if let Err(e) = sqlx::query(
            "UPDATE media SET total_streams = GREATEST(total_streams - 1, 0) WHERE id = $1",
        )
        .bind(media_id)
        .execute(&mut *txn)
        .await
        {
            tracing::warn!("Failed to decrement total_streams for media {media_id}: {e}");
        }
    }

    // Delete stream_media_link rows
    if let Err(e) = sqlx::query("DELETE FROM stream_media_link WHERE stream_id = $1")
        .bind(stream_id_i32)
        .execute(&mut *txn)
        .await
    {
        tracing::error!("DB error deleting stream_media_link for stream {stream_id}: {e}");
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "Database error"})),
        )
            .into_response();
    }

    // Delete playback_tracking rows
    if let Err(e) = sqlx::query("DELETE FROM playback_tracking WHERE stream_id = $1")
        .bind(stream_id_i32)
        .execute(&mut *txn)
        .await
    {
        tracing::warn!("Failed to delete playback_tracking for stream {stream_id}: {e}");
    }

    // Delete type-specific row
    let type_table = match stream_type {
        crate::db::StreamType::Torrent => Some("torrent_stream"),
        crate::db::StreamType::Http => Some("http_stream"),
        crate::db::StreamType::Youtube => Some("youtube_stream"),
        crate::db::StreamType::Usenet => Some("usenet_stream"),
        crate::db::StreamType::Telegram => Some("telegram_stream"),
        crate::db::StreamType::ExternalLink => Some("external_link_stream"),
        crate::db::StreamType::Acestream => Some("acestream_stream"),
    };

    if let Some(table) = type_table {
        let sql = format!("DELETE FROM {table} WHERE stream_id = $1");
        if let Err(e) = sqlx::query(&sql)
            .bind(stream_id_i32)
            .execute(&mut *txn)
            .await
        {
            tracing::warn!("Failed to delete from {table} for stream {stream_id}: {e}");
        }
    }

    // Delete stream_votes
    if let Err(e) = sqlx::query("DELETE FROM stream_votes WHERE stream_id = $1")
        .bind(stream_id_i32)
        .execute(&mut *txn)
        .await
    {
        tracing::warn!("Failed to delete stream_votes for stream {stream_id}: {e}");
    }

    // Delete from stream table
    if let Err(e) = sqlx::query("DELETE FROM stream WHERE id = $1")
        .bind(stream_id_i32)
        .execute(&mut *txn)
        .await
    {
        tracing::error!("DB error deleting stream {stream_id}: {e}");
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "Database error"})),
        )
            .into_response();
    }

    // Commit transaction
    if let Err(e) = txn.commit().await {
        tracing::error!("DB error committing transaction for stream {stream_id}: {e}");
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "Database error"})),
        )
            .into_response();
    }

    (
        StatusCode::OK,
        Json(json!({"message": format!("{} stream deleted successfully", stream_type.as_wire().to_lowercase())})),
    )
        .into_response()
}
