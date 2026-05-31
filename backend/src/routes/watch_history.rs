/// Watch history endpoints.
///
/// Routes (prefix /api/v1/watch-history):
///   GET    /                     → list_watch_history
///   GET    /continue-watching    → continue_watching
///   POST   /                     → create_watch_history
///   PATCH  /{id}                 → update_progress
///   DELETE /{id}                 → delete_entry
///   DELETE /                     → clear_history
///   POST   /track                → track_action
use std::collections::HashMap;
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
use sha2::Sha256;

use crate::{db::{HistorySource, WatchAction}, state::AppState};

// ─── Auth helper ─────────────────────────────────────────────────────────────

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

// ─── Query structs ────────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct ListQuery {
    pub profile_id: Option<i64>,
    pub media_type: Option<String>,
    pub action: Option<String>,
    #[serde(default = "default_page")]
    pub page: i64,
    #[serde(default = "default_page_size")]
    pub page_size: i64,
}

fn default_page() -> i64 {
    1
}
fn default_page_size() -> i64 {
    20
}

#[derive(Deserialize)]
pub struct ContinueWatchingQuery {
    pub profile_id: Option<i64>,
    #[serde(default = "default_cw_limit")]
    pub limit: i64,
}

fn default_cw_limit() -> i64 {
    10
}

#[derive(Deserialize)]
pub struct CreateEntry {
    pub profile_id: i64,
    pub media_id: i64,
    pub title: String,
    pub media_type: String,
    pub season: Option<i32>,
    pub episode: Option<i32>,
    pub duration: Option<i64>,
    #[serde(default)]
    pub progress: i64,
}

#[derive(Deserialize)]
pub struct UpdateProgress {
    pub progress: i64,
    pub duration: Option<i64>,
}

#[derive(Deserialize)]
pub struct ClearQuery {
    pub profile_id: Option<i64>,
}

#[derive(Deserialize)]
pub struct TrackAction {
    pub media_id: i64,
    pub title: String,
    pub catalog_type: String,
    pub season: Option<i32>,
    pub episode: Option<i32>,
    pub action: String,
    pub stream_info: Option<serde_json::Value>,
}

// ─── DB row ──────────────────────────────────────────────────────────────────

struct WatchRow {
    id: i32,
    user_id: i32,
    profile_id: i32,
    media_id: i32,
    title: String,
    media_type: String,
    season: Option<i32>,
    episode: Option<i32>,
    duration: Option<i32>,
    progress: i32,
    watched_at: DateTime<Utc>,
    action: Option<String>,
    source: Option<String>,
    stream_info: Option<serde_json::Value>,
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

async fn get_external_ids(pool: &sqlx::PgPool, media_id: i32) -> serde_json::Value {
    let rows: Vec<(String, String)> =
        sqlx::query_as("SELECT provider, external_id FROM media_external_id WHERE media_id = $1")
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

/// Batch-fetch external IDs for a slice of media_ids in a single query.
/// Returns a map from media_id → {provider: external_id, ...}.
async fn get_external_ids_batch(
    pool: &sqlx::PgPool,
    media_ids: &[i32],
) -> HashMap<i32, serde_json::Value> {
    if media_ids.is_empty() {
        return HashMap::new();
    }
    let rows: Vec<(i32, String, String)> = sqlx::query_as(
        "SELECT media_id, provider, external_id FROM media_external_id WHERE media_id = ANY($1)",
    )
    .bind(media_ids)
    .fetch_all(pool)
    .await
    .unwrap_or_default();
    let mut by_media: HashMap<i32, serde_json::Map<String, serde_json::Value>> = HashMap::new();
    for (media_id, source, id) in rows {
        by_media
            .entry(media_id)
            .or_default()
            .insert(source, serde_json::Value::String(id));
    }
    by_media
        .into_iter()
        .map(|(k, v)| (k, serde_json::Value::Object(v)))
        .collect()
}

fn build_watch_response(row: &WatchRow, ext: &serde_json::Value) -> serde_json::Value {
    let imdb_id = ext
        .get("imdb")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let poster_id = if imdb_id.is_empty() {
        format!("mf{}", row.media_id)
    } else {
        imdb_id
    };
    serde_json::json!({
        "id": row.id,
        "user_id": row.user_id,
        "profile_id": row.profile_id,
        "media_id": row.media_id,
        "external_ids": ext,
        "title": row.title,
        "media_type": row.media_type,
        "season": row.season,
        "episode": row.episode,
        "duration": row.duration,
        "progress": row.progress,
        "watched_at": row.watched_at.to_rfc3339(),
        "poster": format!("/poster/{}/{}.jpg", row.media_type, poster_id),
        "action": row.action.as_deref().unwrap_or("WATCHED"),
        "source": row.source.as_deref().unwrap_or("mediafusion"),
        "stream_info": row.stream_info,
    })
}

async fn row_to_response(pool: &sqlx::PgPool, row: &WatchRow) -> serde_json::Value {
    let ext = get_external_ids(pool, row.media_id).await;
    let imdb_id = ext
        .get("imdb")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let poster_id = if imdb_id.is_empty() {
        format!("mf{}", row.media_id)
    } else {
        imdb_id
    };
    serde_json::json!({
        "id": row.id,
        "user_id": row.user_id,
        "profile_id": row.profile_id,
        "media_id": row.media_id,
        "external_ids": ext,
        "title": row.title,
        "media_type": row.media_type,
        "season": row.season,
        "episode": row.episode,
        "duration": row.duration,
        "progress": row.progress,
        "watched_at": row.watched_at.to_rfc3339(),
        "poster": format!("/poster/{}/{}.jpg", row.media_type, poster_id),
        "action": row.action.as_deref().unwrap_or("WATCHED"),
        "source": row.source.as_deref().unwrap_or("mediafusion"),
        "stream_info": row.stream_info,
    })
}

/// Fetch a watch history row by id, verifying user ownership.
#[allow(dead_code, clippy::type_complexity)]
async fn fetch_watch_row(
    pool: &sqlx::PgPool,
    id: i32,
    user_id: i32,
) -> Result<Option<WatchRow>, sqlx::Error> {
    let row: Option<(
        i32,
        i32,
        i32,
        i32,
        String,
        String,
        Option<i32>,
        Option<i32>,
        Option<i32>,
        i32,
        DateTime<Utc>,
        Option<String>,
        Option<String>,
        Option<serde_json::Value>,
    )> = sqlx::query_as(
        r#"SELECT id, user_id, profile_id, media_id, title, media_type,
                      season, episode, duration, progress, watched_at,
                      action::text, source::text, stream_info
               FROM watch_history
               WHERE id = $1 AND user_id = $2"#,
    )
    .bind(id)
    .bind(user_id)
    .fetch_optional(pool)
    .await?;
    Ok(row.map(
        |(
            id,
            user_id,
            profile_id,
            media_id,
            title,
            media_type,
            season,
            episode,
            duration,
            progress,
            watched_at,
            action,
            source,
            stream_info,
        )| {
            WatchRow {
                id,
                user_id,
                profile_id,
                media_id,
                title,
                media_type,
                season,
                episode,
                duration,
                progress,
                watched_at,
                action,
                source,
                stream_info,
            }
        },
    ))
}

#[allow(clippy::type_complexity)]
fn map_watch_row(
    r: (
        i32,
        i32,
        i32,
        i32,
        String,
        String,
        Option<i32>,
        Option<i32>,
        Option<i32>,
        i32,
        DateTime<Utc>,
        Option<String>,
        Option<String>,
        Option<serde_json::Value>,
    ),
) -> WatchRow {
    WatchRow {
        id: r.0,
        user_id: r.1,
        profile_id: r.2,
        media_id: r.3,
        title: r.4,
        media_type: r.5,
        season: r.6,
        episode: r.7,
        duration: r.8,
        progress: r.9,
        watched_at: r.10,
        action: r.11,
        source: r.12,
        stream_info: r.13,
    }
}

// ─── Handlers ────────────────────────────────────────────────────────────────

/// GET /api/v1/watch-history
#[allow(clippy::type_complexity)]
pub async fn list_watch_history(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<ListQuery>,
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

    // Build dynamic query
    // We use a manual approach for optional filters to stay readable
    let total: i64 = {
        // Count query
        let mut count_sql = String::from("SELECT COUNT(*) FROM watch_history WHERE user_id = $1");
        let mut idx = 2i32;
        if params.profile_id.is_some() {
            count_sql.push_str(&format!(" AND profile_id = ${idx}"));
            idx += 1;
        }
        if params.media_type.is_some() {
            count_sql.push_str(&format!(" AND media_type = ${idx}"));
            idx += 1;
        }
        if params.action.is_some() {
            count_sql.push_str(&format!(" AND action = ${idx}"));
            idx += 1;
        }
        let _ = idx; // suppress unused_assignments: idx is only needed while building the SQL string
        let mut q = sqlx::query_scalar::<_, i64>(&count_sql).bind(user_id as i32);
        if let Some(pid) = params.profile_id {
            q = q.bind(pid);
        }
        if let Some(ref mt) = params.media_type {
            q = q.bind(mt.clone());
        }
        if let Some(ref act) = params.action {
            if let Some(wa) = WatchAction::from_wire(act) {
                q = q.bind(wa);
            }
        }
        match q.fetch_one(&state.pool_ro).await {
            Ok(c) => c,
            Err(e) => {
                tracing::error!("list_watch_history count: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        }
    };

    let rows: Vec<(
        i32,
        i32,
        i32,
        i32,
        String,
        String,
        Option<i32>,
        Option<i32>,
        Option<i32>,
        i32,
        DateTime<Utc>,
        Option<String>,
        Option<String>,
        Option<serde_json::Value>,
    )> = {
        let mut sql = String::from(
            r#"SELECT id, user_id, profile_id, media_id, title, media_type,
                      season, episode, duration, progress, watched_at,
                      action::text, source::text, stream_info
               FROM watch_history WHERE user_id = $1"#,
        );
        let mut idx = 2i32;
        if params.profile_id.is_some() {
            sql.push_str(&format!(" AND profile_id = ${idx}"));
            idx += 1;
        }
        if params.media_type.is_some() {
            sql.push_str(&format!(" AND media_type = ${idx}"));
            idx += 1;
        }
        if params.action.is_some() {
            sql.push_str(&format!(" AND action = ${idx}"));
            idx += 1;
        }
        sql.push_str(&format!(
            " ORDER BY watched_at DESC LIMIT ${idx} OFFSET ${}",
            idx + 1
        ));
        let mut q = sqlx::query_as::<
            _,
            (
                i32,
                i32,
                i32,
                i32,
                String,
                String,
                Option<i32>,
                Option<i32>,
                Option<i32>,
                i32,
                DateTime<Utc>,
                Option<String>,
                Option<String>,
                Option<serde_json::Value>,
            ),
        >(&sql)
        .bind(user_id as i32);
        if let Some(pid) = params.profile_id {
            q = q.bind(pid as i32);
        }
        if let Some(ref mt) = params.media_type {
            q = q.bind(mt.clone());
        }
        if let Some(ref act) = params.action {
            if let Some(wa) = WatchAction::from_wire(act) {
                q = q.bind(wa);
            }
        }
        q = q.bind(page_size).bind(offset);
        match q.fetch_all(&state.pool_ro).await {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("list_watch_history fetch: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        }
    };

    let watch_rows: Vec<WatchRow> = rows.into_iter().map(map_watch_row).collect();
    let media_ids: Vec<i32> = watch_rows.iter().map(|r| r.media_id).collect();
    let ext_map = get_external_ids_batch(&state.pool_ro, &media_ids).await;
    let empty_ext = serde_json::Value::Object(serde_json::Map::new());
    let items: Vec<serde_json::Value> = watch_rows
        .iter()
        .map(|r| build_watch_response(r, ext_map.get(&r.media_id).unwrap_or(&empty_ext)))
        .collect();

    let has_more = offset + page_size < total;
    Json(serde_json::json!({
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": has_more,
    }))
    .into_response()
}

/// GET /api/v1/watch-history/continue-watching
#[allow(clippy::type_complexity)]
pub async fn continue_watching(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<ContinueWatchingQuery>,
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

    let limit = params.limit.clamp(1, 100);

    // Fetch recent entries with progress > 0
    let sql = if let Some(_profile_id) = params.profile_id {
        format!(
            r#"SELECT id, user_id, profile_id, media_id, title, media_type,
                      season, episode, duration, progress, watched_at,
                      action::text, source::text, stream_info
               FROM watch_history
               WHERE user_id = $1 AND profile_id = $2 AND progress > 0
               ORDER BY watched_at DESC
               LIMIT {}"#,
            limit * 3 // Fetch more so we can filter by percent
        )
    } else {
        format!(
            r#"SELECT id, user_id, profile_id, media_id, title, media_type,
                      season, episode, duration, progress, watched_at,
                      action::text, source::text, stream_info
               FROM watch_history
               WHERE user_id = $1 AND progress > 0
               ORDER BY watched_at DESC
               LIMIT {}"#,
            limit * 3
        )
    };

    let rows: Vec<(
        i32,
        i32,
        i32,
        i32,
        String,
        String,
        Option<i32>,
        Option<i32>,
        Option<i32>,
        i32,
        DateTime<Utc>,
        Option<String>,
        Option<String>,
        Option<serde_json::Value>,
    )> = if let Some(pid) = params.profile_id {
        match sqlx::query_as(&sql)
            .bind(user_id as i32)
            .bind(pid as i32)
            .fetch_all(&state.pool_ro)
            .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("continue_watching fetch (with profile): {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        }
    } else {
        match sqlx::query_as(&sql)
            .bind(user_id as i32)
            .fetch_all(&state.pool_ro)
            .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("continue_watching fetch: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        }
    };

    // Filter rows before fetching external IDs — avoids fetching IDs for discarded rows.
    let filtered_rows: Vec<WatchRow> = rows
        .into_iter()
        .map(map_watch_row)
        .filter(|r| {
            r.duration
                .map(|d| d > 0 && (1..90).contains(&(r.progress * 100 / d)))
                .unwrap_or(false)
        })
        .take(limit as usize)
        .collect();

    let media_ids: Vec<i32> = filtered_rows.iter().map(|r| r.media_id).collect();
    let ext_map = get_external_ids_batch(&state.pool_ro, &media_ids).await;
    let empty_ext = serde_json::Value::Object(serde_json::Map::new());
    let items: Vec<serde_json::Value> = filtered_rows
        .iter()
        .map(|r| build_watch_response(r, ext_map.get(&r.media_id).unwrap_or(&empty_ext)))
        .collect();

    Json(serde_json::json!(items)).into_response()
}

/// POST /api/v1/watch-history
#[allow(clippy::type_complexity)]
pub async fn create_watch_history(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<CreateEntry>,
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

    // Verify profile belongs to user
    let profile_exists: bool = match sqlx::query_scalar::<_, i32>(
        "SELECT id FROM user_profiles WHERE id = $1 AND user_id = $2",
    )
    .bind(body.profile_id)
    .bind(user_id as i32)
    .fetch_optional(&state.pool)
    .await
    {
        Ok(Some(_)) => true,
        Ok(None) => false,
        Err(e) => {
            tracing::error!("create_watch_history profile check: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    if !profile_exists {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"detail": "Profile not found or does not belong to user"})),
        )
            .into_response();
    }

    // Check media exists
    let media_exists: bool =
        match sqlx::query_scalar::<_, i32>("SELECT id FROM media WHERE id = $1")
            .bind(body.media_id)
            .fetch_optional(&state.pool)
            .await
        {
            Ok(Some(_)) => true,
            Ok(None) => false,
            Err(e) => {
                tracing::error!("create_watch_history media check: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        };

    if !media_exists {
        return (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"detail": "Media not found"})),
        )
            .into_response();
    }

    // Check for existing entry
    let existing_id: Option<i64> = if body.media_type == "series" {
        sqlx::query_scalar(
            r#"SELECT id FROM watch_history
               WHERE user_id = $1 AND profile_id = $2 AND media_id = $3
                 AND season IS NOT DISTINCT FROM $4 AND episode IS NOT DISTINCT FROM $5
               LIMIT 1"#,
        )
        .bind(user_id as i32)
        .bind(body.profile_id)
        .bind(body.media_id)
        .bind(body.season)
        .bind(body.episode)
        .fetch_optional(&state.pool)
        .await
        .unwrap_or(None)
    } else {
        sqlx::query_scalar(
            r#"SELECT id FROM watch_history
               WHERE user_id = $1 AND profile_id = $2 AND media_id = $3
               LIMIT 1"#,
        )
        .bind(user_id as i32)
        .bind(body.profile_id)
        .bind(body.media_id)
        .fetch_optional(&state.pool)
        .await
        .unwrap_or(None)
    };

    let row: (
        i32,
        i32,
        i32,
        i32,
        String,
        String,
        Option<i32>,
        Option<i32>,
        Option<i32>,
        i32,
        DateTime<Utc>,
        Option<String>,
        Option<String>,
        Option<serde_json::Value>,
    ) = if let Some(eid) = existing_id {
        match sqlx::query_as(
            r#"UPDATE watch_history
               SET progress = $1, duration = COALESCE($2, duration), watched_at = NOW(), title = $3
               WHERE id = $4
               RETURNING id, user_id, profile_id, media_id, title, media_type,
                         season, episode, duration, progress, watched_at,
                         action::text, source::text, stream_info"#,
        )
        .bind(body.progress)
        .bind(body.duration)
        .bind(&body.title)
        .bind(eid)
        .fetch_one(&state.pool)
        .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("create_watch_history update: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        }
    } else {
        match sqlx::query_as(
            r#"INSERT INTO watch_history
                   (user_id, profile_id, media_id, title, media_type, season, episode,
                    duration, progress, watched_at, action, source)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW(), $10, $11)
               RETURNING id, user_id, profile_id, media_id, title, media_type,
                         season, episode, duration, progress, watched_at,
                         action::text, source::text, stream_info"#,
        )
        .bind(user_id as i32)
        .bind(body.profile_id)
        .bind(body.media_id)
        .bind(&body.title)
        .bind(&body.media_type)
        .bind(body.season)
        .bind(body.episode)
        .bind(body.duration)
        .bind(body.progress)
        .bind(WatchAction::Watched)
        .bind(HistorySource::Mediafusion)
        .fetch_one(&state.pool)
        .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("create_watch_history insert: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        }
    };

    let watch_row = map_watch_row(row);
    let resp = row_to_response(&state.pool, &watch_row).await;
    (StatusCode::CREATED, Json(resp)).into_response()
}

/// PATCH /api/v1/watch-history/{id}
#[allow(clippy::type_complexity)]
pub async fn update_progress(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(id): Path<i64>,
    Json(body): Json<UpdateProgress>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(uid) => uid,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let row: Option<(
        i32,
        i32,
        i32,
        i32,
        String,
        String,
        Option<i32>,
        Option<i32>,
        Option<i32>,
        i32,
        DateTime<Utc>,
        Option<String>,
        Option<String>,
        Option<serde_json::Value>,
    )> = match sqlx::query_as(
        r#"UPDATE watch_history
               SET progress = $1, duration = COALESCE($2, duration), watched_at = NOW()
               WHERE id = $3 AND user_id = $4
               RETURNING id, user_id, profile_id, media_id, title, media_type,
                         season, episode, duration, progress, watched_at,
                         action::text, source::text, stream_info"#,
    )
    .bind(body.progress as i32)
    .bind(body.duration.map(|d| d as i32))
    .bind(id as i32)
    .bind(user_id as i32)
    .fetch_optional(&state.pool)
    .await
    {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("update_progress db error: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    match row {
        Some(r) => {
            let watch_row = map_watch_row(r);
            Json(row_to_response(&state.pool, &watch_row).await).into_response()
        }
        None => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"detail": "Entry not found"})),
        )
            .into_response(),
    }
}

/// DELETE /api/v1/watch-history/{id}
pub async fn delete_entry(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(id): Path<i64>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(uid) => uid,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let result = sqlx::query("DELETE FROM watch_history WHERE id = $1 AND user_id = $2")
        .bind(id)
        .bind(user_id as i32)
        .execute(&state.pool)
        .await;

    match result {
        Ok(r) if r.rows_affected() == 0 => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"detail": "Entry not found"})),
        )
            .into_response(),
        Ok(_) => StatusCode::NO_CONTENT.into_response(),
        Err(e) => {
            tracing::error!("delete_entry db error: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

/// DELETE /api/v1/watch-history
pub async fn clear_history(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<ClearQuery>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(uid) => uid,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    if let Some(profile_id) = params.profile_id {
        // Verify profile ownership
        let profile_exists: bool = match sqlx::query_scalar::<_, i32>(
            "SELECT id FROM user_profiles WHERE id = $1 AND user_id = $2",
        )
        .bind(profile_id)
        .bind(user_id as i32)
        .fetch_optional(&state.pool)
        .await
        {
            Ok(Some(_)) => true,
            Ok(None) => false,
            Err(e) => {
                tracing::error!("clear_history profile check: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        };

        if !profile_exists {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({"detail": "Profile not found or does not belong to user"})),
            )
                .into_response();
        }

        match sqlx::query("DELETE FROM watch_history WHERE user_id = $1 AND profile_id = $2")
            .bind(user_id as i32)
            .bind(profile_id)
            .execute(&state.pool)
            .await
        {
            Ok(_) => StatusCode::NO_CONTENT.into_response(),
            Err(e) => {
                tracing::error!("clear_history (profile) db error: {e}");
                StatusCode::INTERNAL_SERVER_ERROR.into_response()
            }
        }
    } else {
        match sqlx::query("DELETE FROM watch_history WHERE user_id = $1")
            .bind(user_id as i32)
            .execute(&state.pool)
            .await
        {
            Ok(_) => StatusCode::NO_CONTENT.into_response(),
            Err(e) => {
                tracing::error!("clear_history db error: {e}");
                StatusCode::INTERNAL_SERVER_ERROR.into_response()
            }
        }
    }
}

/// POST /api/v1/watch-history/track
#[allow(clippy::type_complexity)]
pub async fn track_action(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<TrackAction>,
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

    // Normalize action to the watchaction enum values (client may send lowercase/short form).
    let action = match body.action.to_uppercase().as_str() {
        "WATCH" | "WATCHED" => WatchAction::Watched,
        "DOWNLOAD" | "DOWNLOADED" => WatchAction::Downloaded,
        "QUEUE" | "QUEUED" => WatchAction::Queued,
        other => {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({"detail": format!("Invalid action: {other}")})),
            )
                .into_response();
        }
    };

    // Check media exists
    let media_exists: bool =
        match sqlx::query_scalar::<_, i32>("SELECT id FROM media WHERE id = $1")
            .bind(body.media_id)
            .fetch_optional(&state.pool)
            .await
        {
            Ok(Some(_)) => true,
            Ok(None) => false,
            Err(e) => {
                tracing::error!("track_action media check: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        };

    if !media_exists {
        return (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"detail": "Media not found"})),
        )
            .into_response();
    }

    // Find default profile
    let profile_id: Option<i64> = sqlx::query_scalar(
        "SELECT id FROM user_profiles WHERE user_id = $1 AND is_default = true LIMIT 1",
    )
    .bind(user_id as i32)
    .fetch_optional(&state.pool)
    .await
    .unwrap_or(None);

    let profile_id: i64 = if let Some(pid) = profile_id {
        pid
    } else {
        // Fallback: any profile
        match sqlx::query_scalar::<_, i32>(
            "SELECT id FROM user_profiles WHERE user_id = $1 LIMIT 1",
        )
        .bind(user_id as i32)
        .fetch_optional(&state.pool)
        .await
        {
            Ok(Some(pid)) => pid.into(),
            Ok(None) => {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(serde_json::json!({"detail": "No profile found"})),
                )
                    .into_response();
            }
            Err(e) => {
                tracing::error!("track_action profile fetch: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        }
    };

    // Check existing entry
    let existing_id: Option<i64> = if body.catalog_type == "series" {
        sqlx::query_scalar(
            r#"SELECT id FROM watch_history
               WHERE user_id = $1 AND profile_id = $2 AND media_id = $3
                 AND season IS NOT DISTINCT FROM $4 AND episode IS NOT DISTINCT FROM $5
               LIMIT 1"#,
        )
        .bind(user_id as i32)
        .bind(profile_id)
        .bind(body.media_id)
        .bind(body.season)
        .bind(body.episode)
        .fetch_optional(&state.pool)
        .await
        .unwrap_or(None)
    } else {
        sqlx::query_scalar(
            r#"SELECT id FROM watch_history
               WHERE user_id = $1 AND profile_id = $2 AND media_id = $3
               LIMIT 1"#,
        )
        .bind(user_id as i32)
        .bind(profile_id)
        .bind(body.media_id)
        .fetch_optional(&state.pool)
        .await
        .unwrap_or(None)
    };

    let row: (
        i32,
        i32,
        i32,
        i32,
        String,
        String,
        Option<i32>,
        Option<i32>,
        Option<i32>,
        i32,
        DateTime<Utc>,
        Option<String>,
        Option<String>,
        Option<serde_json::Value>,
    ) = if let Some(eid) = existing_id {
        match sqlx::query_as(
            r#"UPDATE watch_history
               SET action = $1, stream_info = COALESCE($2, stream_info), watched_at = NOW(), title = $3
               WHERE id = $4
               RETURNING id, user_id, profile_id, media_id, title, media_type,
                         season, episode, duration, progress, watched_at,
                         action::text, source::text, stream_info"#,
        )
        .bind(action)
        .bind(&body.stream_info)
        .bind(&body.title)
        .bind(eid)
        .fetch_one(&state.pool)
        .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("track_action update: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        }
    } else {
        match sqlx::query_as(
            r#"INSERT INTO watch_history
                   (user_id, profile_id, media_id, title, media_type, season, episode,
                    progress, watched_at, action, source, stream_info)
               VALUES ($1, $2, $3, $4, $5, $6, $7, 0, NOW(), $8, $9, $10)
               RETURNING id, user_id, profile_id, media_id, title, media_type,
                         season, episode, duration, progress, watched_at,
                         action::text, source::text, stream_info"#,
        )
        .bind(user_id as i32)
        .bind(profile_id)
        .bind(body.media_id)
        .bind(&body.title)
        .bind(&body.catalog_type)
        .bind(body.season)
        .bind(body.episode)
        .bind(action)
        .bind(HistorySource::Mediafusion)
        .bind(&body.stream_info)
        .fetch_one(&state.pool)
        .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("track_action insert: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        }
    };

    let watch_row = map_watch_row(row);
    let resp = row_to_response(&state.pool, &watch_row).await;
    (StatusCode::CREATED, Json(resp)).into_response()
}
