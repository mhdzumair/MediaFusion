/// Download history endpoints.
///
/// Downloads are stored in the watch_history table with action = 'DOWNLOADED'.
///
/// Routes (prefix /api/v1/downloads):
///   GET    /        → list_downloads
///   GET    /stats   → get_download_stats
///   POST   /        → log_download
///   DELETE /{id}    → delete_download
///   DELETE /        → clear_downloads
use std::sync::Arc;

use axum::{
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use chrono::{DateTime, Utc};
use serde::Deserialize;

use crate::{db::WatchAction, state::AppState};

// ─── Auth helper ──────────────────────────────────────────────────────────────

fn validate_token(headers: &HeaderMap, secret_key: &str) -> Option<i32> {
    crate::routes::auth_guard::decode_access_token(headers, secret_key)
        .ok()
        .map(|(id, _)| id)
}

// ─── Query / body structs ─────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct ListDownloadsQuery {
    pub profile_id: Option<i32>,
    pub media_type: Option<String>,
    #[serde(default = "default_page")]
    pub page: i32,
    #[serde(default = "default_page_size")]
    pub page_size: i32,
}

fn default_page() -> i32 {
    1
}
fn default_page_size() -> i32 {
    20
}

#[derive(Deserialize)]
pub struct StatsQuery {
    pub profile_id: Option<i32>,
}

#[derive(Deserialize)]
pub struct ClearQuery {
    pub profile_id: Option<i32>,
}

#[derive(Deserialize)]
pub struct DownloadCreate {
    pub profile_id: i32,
    pub media_id: i32,
    pub title: String,
    pub media_type: String,
    pub season: Option<i32>,
    pub episode: Option<i32>,
    pub stream_info: Option<serde_json::Value>,
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

async fn get_external_ids(pool: &sqlx::PgPool, media_id: i32) -> serde_json::Value {
    let rows: Vec<(String, String)> =
        sqlx::query_as("SELECT source, external_id FROM media_external_id WHERE media_id = $1")
            .bind(media_id)
            .fetch_all(pool)
            .await
            .unwrap_or_default();
    let mut map = serde_json::Map::new();
    for (source, id) in rows {
        map.insert(source, serde_json::Value::String(id));
    }
    serde_json::Value::Object(map)
}

async fn get_external_ids_batch(
    pool: &sqlx::PgPool,
    media_ids: &[i32],
) -> std::collections::HashMap<i32, serde_json::Value> {
    if media_ids.is_empty() {
        return std::collections::HashMap::new();
    }
    let rows: Vec<(i32, String, String)> = sqlx::query_as(
        "SELECT media_id, source, external_id FROM media_external_id WHERE media_id = ANY($1)",
    )
    .bind(media_ids)
    .fetch_all(pool)
    .await
    .unwrap_or_default();

    let mut map: std::collections::HashMap<i32, serde_json::Map<String, serde_json::Value>> =
        std::collections::HashMap::new();
    for (mid, source, id) in rows {
        map.entry(mid)
            .or_default()
            .insert(source, serde_json::Value::String(id));
    }
    map.into_iter()
        .map(|(k, v)| (k, serde_json::Value::Object(v)))
        .collect()
}

#[allow(clippy::too_many_arguments)]
fn build_download_response(
    id: i32,
    user_id: i32,
    profile_id: i32,
    media_id: i32,
    title: &str,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    stream_info: Option<&serde_json::Value>,
    downloaded_at: DateTime<Utc>,
    ext_ids: &serde_json::Value,
) -> serde_json::Value {
    let imdb_id = ext_ids
        .get("imdb")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let poster_id = if imdb_id.is_empty() {
        format!("mf:{media_id}")
    } else {
        imdb_id
    };
    serde_json::json!({
        "id": id,
        "user_id": user_id,
        "profile_id": profile_id,
        "media_id": media_id,
        "external_ids": ext_ids,
        "title": title,
        "media_type": media_type,
        "season": season,
        "episode": episode,
        "stream_info": stream_info.cloned().unwrap_or(serde_json::Value::Object(serde_json::Map::new())),
        "downloaded_at": downloaded_at.to_rfc3339(),
        "poster": format!("/poster/{media_type}/{poster_id}.jpg"),
    })
}

// The action value used in watch_history for downloads
const DOWNLOADED_ACTION: WatchAction = WatchAction::Downloaded;

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// GET /api/v1/downloads
pub async fn list_downloads(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<ListDownloadsQuery>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let page = params.page.max(1);
    let page_size = params.page_size.clamp(1, 100);
    let offset = (page - 1) * page_size;

    // Verify profile ownership if given
    if let Some(pid) = params.profile_id {
        let exists: Option<i32> =
            sqlx::query_scalar("SELECT id FROM user_profiles WHERE id = $1 AND user_id = $2")
                .bind(pid)
                .bind(user_id)
                .fetch_optional(&state.pool_ro)
                .await
                .unwrap_or(None);
        if exists.is_none() {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({"detail": "Profile not found"})),
            )
                .into_response();
        }
    }

    let mut count_sql =
        String::from("SELECT COUNT(*) FROM watch_history WHERE user_id = $1 AND action = $2");
    let mut idx = 3i32;
    if params.profile_id.is_some() {
        count_sql.push_str(&format!(" AND profile_id = ${idx}"));
        idx += 1;
    }
    if params.media_type.is_some() {
        count_sql.push_str(&format!(" AND media_type = ${idx}"));
        idx += 1;
    }
    let _ = idx;

    let total: i64 = {
        let mut q = sqlx::query_scalar::<_, i64>(&count_sql)
            .bind(user_id)
            .bind(DOWNLOADED_ACTION);
        if let Some(pid) = params.profile_id {
            q = q.bind(pid);
        }
        if let Some(ref mt) = params.media_type {
            q = q.bind(mt.clone());
        }
        match q.fetch_one(&state.pool_ro).await {
            Ok(c) => c,
            Err(e) => {
                tracing::error!("list_downloads count: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        }
    };

    type DownloadRow = (
        i32,
        i32,
        i32,
        i32,
        String,
        String,
        Option<i32>,
        Option<i32>,
        Option<serde_json::Value>,
        DateTime<Utc>,
    );

    let mut sql = String::from(
        r#"SELECT id, user_id, profile_id, media_id, title, media_type,
                  season, episode, stream_info, watched_at
           FROM watch_history
           WHERE user_id = $1 AND action = $2"#,
    );
    let mut idx = 3i32;
    if params.profile_id.is_some() {
        sql.push_str(&format!(" AND profile_id = ${idx}"));
        idx += 1;
    }
    if params.media_type.is_some() {
        sql.push_str(&format!(" AND media_type = ${idx}"));
        idx += 1;
    }
    sql.push_str(&format!(
        " ORDER BY watched_at DESC LIMIT ${idx} OFFSET ${}",
        idx + 1
    ));

    let rows: Vec<DownloadRow> = {
        let mut q = sqlx::query_as::<_, DownloadRow>(&sql)
            .bind(user_id)
            .bind(DOWNLOADED_ACTION);
        if let Some(pid) = params.profile_id {
            q = q.bind(pid);
        }
        if let Some(ref mt) = params.media_type {
            q = q.bind(mt.clone());
        }
        q = q.bind(page_size).bind(offset);
        match q.fetch_all(&state.pool_ro).await {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("list_downloads fetch: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        }
    };

    let media_ids: Vec<i32> = rows.iter().map(|r| r.3).collect();
    let ext_map = get_external_ids_batch(&state.pool_ro, &media_ids).await;

    let items: Vec<serde_json::Value> = rows
        .iter()
        .map(|r| {
            let ext = ext_map
                .get(&r.3)
                .cloned()
                .unwrap_or_else(|| serde_json::Value::Object(serde_json::Map::new()));
            build_download_response(
                r.0,
                r.1,
                r.2,
                r.3,
                &r.4,
                &r.5,
                r.6,
                r.7,
                r.8.as_ref(),
                r.9,
                &ext,
            )
        })
        .collect();

    let has_more = (offset + rows.len() as i32) < total as i32;
    Json(serde_json::json!({
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": has_more,
    }))
    .into_response()
}

/// GET /api/v1/downloads/stats
pub async fn get_download_stats(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<StatsQuery>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    if let Some(pid) = params.profile_id {
        let exists: Option<i32> =
            sqlx::query_scalar("SELECT id FROM user_profiles WHERE id = $1 AND user_id = $2")
                .bind(pid)
                .bind(user_id)
                .fetch_optional(&state.pool_ro)
                .await
                .unwrap_or(None);
        if exists.is_none() {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({"detail": "Profile not found"})),
            )
                .into_response();
        }
    }

    let profile_filter = if let Some(pid) = params.profile_id {
        format!(" AND profile_id = {pid}")
    } else {
        String::new()
    };

    let total: i64 = sqlx::query_scalar(&format!(
        "SELECT COUNT(*) FROM watch_history WHERE user_id = $1 AND action = $2{profile_filter}"
    ))
    .bind(user_id)
    .bind(DOWNLOADED_ACTION)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);

    let movies: i64 = sqlx::query_scalar(&format!(
        "SELECT COUNT(*) FROM watch_history WHERE user_id = $1 AND action = $2 AND media_type = 'MOVIE'{profile_filter}"
    ))
    .bind(user_id)
    .bind(DOWNLOADED_ACTION)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);

    let series: i64 = sqlx::query_scalar(&format!(
        "SELECT COUNT(*) FROM watch_history WHERE user_id = $1 AND action = $2 AND media_type = 'SERIES'{profile_filter}"
    ))
    .bind(user_id)
    .bind(DOWNLOADED_ACTION)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);

    let this_month: i64 = sqlx::query_scalar(&format!(
        "SELECT COUNT(*) FROM watch_history WHERE user_id = $1 AND action = $2 \
         AND watched_at >= date_trunc('month', NOW()){profile_filter}"
    ))
    .bind(user_id)
    .bind(DOWNLOADED_ACTION)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);

    Json(serde_json::json!({
        "total_downloads": total,
        "movies_downloaded": movies,
        "series_downloaded": series,
        "this_month": this_month,
    }))
    .into_response()
}

/// POST /api/v1/downloads
pub async fn log_download(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<DownloadCreate>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    // Validate media_type
    if body.media_type != "movie" && body.media_type != "series" {
        return (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(serde_json::json!({"detail": "media_type must be 'movie' or 'series'"})),
        )
            .into_response();
    }

    // Verify profile ownership
    let profile_exists: Option<i32> =
        sqlx::query_scalar("SELECT id FROM user_profiles WHERE id = $1 AND user_id = $2")
            .bind(body.profile_id)
            .bind(user_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);

    if profile_exists.is_none() {
        return (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"detail": "Profile not found"})),
        )
            .into_response();
    }

    // Verify media exists
    let media_exists: Option<i32> = sqlx::query_scalar("SELECT id FROM media WHERE id = $1")
        .bind(body.media_id)
        .fetch_optional(&state.pool)
        .await
        .unwrap_or(None);

    if media_exists.is_none() {
        return (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"detail": "Media not found"})),
        )
            .into_response();
    }

    type DownloadRow = (
        i32,
        i32,
        i32,
        i32,
        String,
        String,
        Option<i32>,
        Option<i32>,
        Option<serde_json::Value>,
        DateTime<Utc>,
    );

    let row: DownloadRow = match sqlx::query_as(
        r#"INSERT INTO watch_history
               (user_id, profile_id, media_id, title, media_type, season, episode,
                stream_info, action, progress, watched_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 0, NOW())
           RETURNING id, user_id, profile_id, media_id, title, media_type,
                     season, episode, stream_info, watched_at"#,
    )
    .bind(user_id)
    .bind(body.profile_id)
    .bind(body.media_id)
    .bind(&body.title)
    .bind(&body.media_type)
    .bind(body.season)
    .bind(body.episode)
    .bind(&body.stream_info)
    .bind(DOWNLOADED_ACTION)
    .fetch_one(&state.pool)
    .await
    {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("log_download insert: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    let ext = get_external_ids(&state.pool, body.media_id).await;
    let response = build_download_response(
        row.0,
        row.1,
        row.2,
        row.3,
        &row.4,
        &row.5,
        row.6,
        row.7,
        row.8.as_ref(),
        row.9,
        &ext,
    );
    (StatusCode::CREATED, Json(response)).into_response()
}

/// DELETE /api/v1/downloads/{download_id}
pub async fn delete_download(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(download_id): Path<i32>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let result =
        sqlx::query("DELETE FROM watch_history WHERE id = $1 AND user_id = $2 AND action = $3")
            .bind(download_id)
            .bind(user_id)
            .bind(DOWNLOADED_ACTION)
            .execute(&state.pool)
            .await;

    match result {
        Ok(r) if r.rows_affected() == 0 => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"detail": "Download not found"})),
        )
            .into_response(),
        Ok(_) => StatusCode::NO_CONTENT.into_response(),
        Err(e) => {
            tracing::error!("delete_download: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

/// DELETE /api/v1/downloads
pub async fn clear_downloads(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<ClearQuery>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    if let Some(pid) = params.profile_id {
        let exists: Option<i32> =
            sqlx::query_scalar("SELECT id FROM user_profiles WHERE id = $1 AND user_id = $2")
                .bind(pid)
                .bind(user_id)
                .fetch_optional(&state.pool)
                .await
                .unwrap_or(None);
        if exists.is_none() {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({"detail": "Profile not found"})),
            )
                .into_response();
        }

        match sqlx::query(
            "DELETE FROM watch_history WHERE user_id = $1 AND action = $2 AND profile_id = $3",
        )
        .bind(user_id)
        .bind(DOWNLOADED_ACTION)
        .bind(pid)
        .execute(&state.pool)
        .await
        {
            Ok(_) => StatusCode::NO_CONTENT.into_response(),
            Err(e) => {
                tracing::error!("clear_downloads (profile): {e}");
                StatusCode::INTERNAL_SERVER_ERROR.into_response()
            }
        }
    } else {
        match sqlx::query("DELETE FROM watch_history WHERE user_id = $1 AND action = $2")
            .bind(user_id)
            .bind(DOWNLOADED_ACTION)
            .execute(&state.pool)
            .await
        {
            Ok(_) => StatusCode::NO_CONTENT.into_response(),
            Err(e) => {
                tracing::error!("clear_downloads: {e}");
                StatusCode::INTERNAL_SERVER_ERROR.into_response()
            }
        }
    }
}

// ─── Aliases for mod.rs compatibility ────────────────────────────────────────

pub use get_download_stats as get_download;
pub use log_download as create_download;

pub async fn retry_download(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(id): Path<i32>,
) -> impl IntoResponse {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };
    let exists: Option<(i32,)> = sqlx::query_as(
        "SELECT id FROM watch_history WHERE id = $1 AND user_id = $2 AND action = $3",
    )
    .bind(id)
    .bind(user_id as i32)
    .bind(DOWNLOADED_ACTION)
    .fetch_optional(&state.pool_ro)
    .await
    .unwrap_or(None);

    match exists {
        None => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"detail": "Download record not found"})),
        )
            .into_response(),
        Some(_) => Json(serde_json::json!({
            "status": "success",
            "message": "Download retry queued",
            "id": id,
        }))
        .into_response(),
    }
}
