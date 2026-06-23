/// Admin genre management endpoints.
///
/// Routes:
///   GET    /api/v1/admin/genres                          → list_genres
///   POST   /api/v1/admin/genres                          → create_genre
///   PATCH  /api/v1/admin/genres/{id}                     → update_genre
///   DELETE /api/v1/admin/genres/{id}                     → delete_genre
///   DELETE /api/v1/admin/genres/{id}/types/{media_type}  → delete_genre_type
///   POST   /api/v1/admin/genres/reload                   → reload_genres_cache
use std::sync::Arc;

use axum::{
    Json,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use chrono::{DateTime, Utc};
use hmac::{Hmac, KeyInit, Mac};
use serde::{Deserialize, Serialize};
use serde_json::json;
use sha2::Sha256;

use crate::state::AppState;

// ─── Auth helpers (same pattern as admin_keyword_filters) ─────────────────────

fn validate_admin(headers: &HeaderMap, secret_key: &str) -> Option<i32> {
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
    if data["role"].as_str() != Some("admin") {
        return None;
    }
    data["sub"].as_str()?.parse().ok()
}

async fn check_admin_role(pool: &sqlx::PgPool, user_id: i32) -> bool {
    crate::db::get_user_role(pool, user_id)
        .await
        .is_some_and(crate::db::is_admin)
}

// ─── Auth guard macro ─────────────────────────────────────────────────────────

macro_rules! require_admin {
    ($headers:expr_2021, $state:expr_2021) => {{
        let user_id = match validate_admin($headers, &$state.config.secret_key_raw) {
            Some(id) => id,
            None => {
                return (
                    StatusCode::UNAUTHORIZED,
                    Json(json!({"detail": "Unauthorized"})),
                )
                    .into_response()
            }
        };
        if !check_admin_role(&$state.pool, user_id).await {
            return (
                StatusCode::FORBIDDEN,
                Json(json!({"detail": "Admin role required"})),
            )
                .into_response();
        }
    }};
}

// ─── Query params ─────────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct GenreListQuery {
    #[serde(default = "default_page")]
    pub page: i64,
    #[serde(default = "default_page_size")]
    pub page_size: i64,
    pub search: Option<String>,
    /// Filter to genres that have a pairing with this media type (any visibility).
    /// Wire values: "movie" | "series" | "tv" | "events"
    pub media_type: Option<String>,
}

fn default_page() -> i64 {
    1
}
fn default_page_size() -> i64 {
    50
}

// ─── Request / Response types ─────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct CreateGenreRequest {
    pub name: String,
    /// Wire media type names to attach: "movie", "series", "tv", "events"
    pub media_types: Vec<String>,
}

#[derive(Deserialize)]
pub struct UpdateGenreRequest {
    pub name: Option<String>,
    /// Full replacement of type pairings. Each entry sets is_hidden for that type;
    /// omitting a type from this list leaves it unchanged.
    pub types: Option<Vec<TypeUpdate>>,
}

#[derive(Deserialize)]
pub struct TypeUpdate {
    pub media_type: String,
    pub is_hidden: bool,
}

#[derive(Serialize, sqlx::FromRow)]
pub struct GenreRow {
    pub id: i32,
    pub name: String,
    pub usage_count: i64,
}

#[derive(Serialize, sqlx::FromRow)]
pub struct GenreTypeRow {
    pub genre_id: i32,
    pub media_type: String,
    pub is_hidden: bool,
    pub created_at: DateTime<Utc>,
}

#[derive(Serialize)]
pub struct GenreDetail {
    pub id: i32,
    pub name: String,
    pub usage_count: i64,
    pub types: Vec<GenreTypeRow>,
}

// ─── Cache invalidation ───────────────────────────────────────────────────────

async fn invalidate_cache(state: &AppState) {
    crate::db::genres::invalidate_genres_cache(&state.redis).await;
}

// ─── Handlers ────────────────────────────────────────────────────────────────

/// GET /api/v1/admin/genres?page=1&page_size=50&search=xxx
pub async fn list_genres(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(q): Query<GenreListQuery>,
) -> impl IntoResponse {
    require_admin!(&headers, state);

    let page = q.page.max(1);
    let page_size = q.page_size.clamp(1, 500);
    let offset = (page - 1) * page_size;
    let search_pat = q.search.as_deref().map(|s| format!("%{s}%"));
    let mt = q.media_type.as_deref().map(|s| s.to_ascii_lowercase());

    // Step 1: paginated genre list with usage counts.
    // Four query variants: (search?, media_type?).
    let (genres, total) = match (&search_pat, &mt) {
        (Some(pat), Some(mt)) => {
            let total: i64 = sqlx::query_scalar(
                "SELECT COUNT(*) FROM genre g \
                 WHERE g.name ILIKE $1 \
                   AND EXISTS (SELECT 1 FROM genre_media_type WHERE genre_id = g.id AND media_type = $2)",
            )
            .bind(pat)
            .bind(mt)
            .fetch_one(&state.pool)
            .await
            .unwrap_or(0);
            let genres: Vec<GenreRow> = sqlx::query_as(
                "SELECT g.id, g.name, COUNT(mgl.media_id) AS usage_count \
                 FROM genre g LEFT JOIN media_genre_link mgl ON mgl.genre_id = g.id \
                 WHERE g.name ILIKE $1 \
                   AND EXISTS (SELECT 1 FROM genre_media_type WHERE genre_id = g.id AND media_type = $2) \
                 GROUP BY g.id ORDER BY g.name LIMIT $3 OFFSET $4",
            )
            .bind(pat)
            .bind(mt)
            .bind(page_size)
            .bind(offset)
            .fetch_all(&state.pool)
            .await
            .unwrap_or_default();
            (genres, total)
        }
        (Some(pat), None) => {
            let total: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM genre WHERE name ILIKE $1")
                .bind(pat)
                .fetch_one(&state.pool)
                .await
                .unwrap_or(0);
            let genres: Vec<GenreRow> = sqlx::query_as(
                "SELECT g.id, g.name, COUNT(mgl.media_id) AS usage_count \
                 FROM genre g LEFT JOIN media_genre_link mgl ON mgl.genre_id = g.id \
                 WHERE g.name ILIKE $1 \
                 GROUP BY g.id ORDER BY g.name LIMIT $2 OFFSET $3",
            )
            .bind(pat)
            .bind(page_size)
            .bind(offset)
            .fetch_all(&state.pool)
            .await
            .unwrap_or_default();
            (genres, total)
        }
        (None, Some(mt)) => {
            let total: i64 = sqlx::query_scalar(
                "SELECT COUNT(*) FROM genre g \
                 WHERE EXISTS (SELECT 1 FROM genre_media_type WHERE genre_id = g.id AND media_type = $1)",
            )
            .bind(mt)
            .fetch_one(&state.pool)
            .await
            .unwrap_or(0);
            let genres: Vec<GenreRow> = sqlx::query_as(
                "SELECT g.id, g.name, COUNT(mgl.media_id) AS usage_count \
                 FROM genre g LEFT JOIN media_genre_link mgl ON mgl.genre_id = g.id \
                 WHERE EXISTS (SELECT 1 FROM genre_media_type WHERE genre_id = g.id AND media_type = $1) \
                 GROUP BY g.id ORDER BY g.name LIMIT $2 OFFSET $3",
            )
            .bind(mt)
            .bind(page_size)
            .bind(offset)
            .fetch_all(&state.pool)
            .await
            .unwrap_or_default();
            (genres, total)
        }
        (None, None) => {
            let total: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM genre")
                .fetch_one(&state.pool)
                .await
                .unwrap_or(0);
            let genres: Vec<GenreRow> = sqlx::query_as(
                "SELECT g.id, g.name, COUNT(mgl.media_id) AS usage_count \
                 FROM genre g LEFT JOIN media_genre_link mgl ON mgl.genre_id = g.id \
                 GROUP BY g.id ORDER BY g.name LIMIT $1 OFFSET $2",
            )
            .bind(page_size)
            .bind(offset)
            .fetch_all(&state.pool)
            .await
            .unwrap_or_default();
            (genres, total)
        }
    };

    if genres.is_empty() {
        return Json(json!({
            "items": [],
            "total": total,
            "page": page,
            "page_size": page_size,
        }))
        .into_response();
    }

    // Step 2: fetch type pairings for the returned genre ids.
    let ids: Vec<i32> = genres.iter().map(|g| g.id).collect();
    let type_rows: Vec<GenreTypeRow> = sqlx::query_as(
        "SELECT genre_id, media_type, is_hidden, created_at \
         FROM genre_media_type WHERE genre_id = ANY($1::int4[]) \
         ORDER BY genre_id, media_type",
    )
    .bind(&ids[..])
    .fetch_all(&state.pool)
    .await
    .unwrap_or_default();

    // Step 3: merge into enriched items.
    let items: Vec<GenreDetail> = genres
        .into_iter()
        .map(|g| {
            let types = type_rows
                .iter()
                .filter(|t| t.genre_id == g.id)
                .map(|t| GenreTypeRow {
                    genre_id: t.genre_id,
                    media_type: t.media_type.clone(),
                    is_hidden: t.is_hidden,
                    created_at: t.created_at,
                })
                .collect();
            GenreDetail {
                id: g.id,
                name: g.name,
                usage_count: g.usage_count,
                types,
            }
        })
        .collect();

    Json(json!({
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }))
    .into_response()
}

/// POST /api/v1/admin/genres  body: {name, media_types}
pub async fn create_genre(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<CreateGenreRequest>,
) -> impl IntoResponse {
    require_admin!(&headers, state);

    let name = body.name.trim().to_string();
    if name.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "name must not be empty"})),
        )
            .into_response();
    }

    // Create genre row.
    let genre_id: Result<i32, _> = sqlx::query_scalar(
        "INSERT INTO genre(name) VALUES($1) ON CONFLICT(name) DO NOTHING RETURNING id",
    )
    .bind(&name)
    .fetch_optional(&state.pool)
    .await
    .map(|r| r.flatten().unwrap_or(0));

    let genre_id = match genre_id {
        Ok(id) if id > 0 => id,
        Ok(_) => {
            // Genre already exists — return conflict.
            return (
                StatusCode::CONFLICT,
                Json(json!({"detail": "genre already exists"})),
            )
                .into_response();
        }
        Err(e) => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": e.to_string()})),
            )
                .into_response();
        }
    };

    // Insert type pairings.
    for media_type in &body.media_types {
        let mt = media_type.to_ascii_lowercase();
        if crate::db::MediaType::from_wire(&mt).is_none() {
            continue;
        }
        let _ = sqlx::query(
            "INSERT INTO genre_media_type(genre_id, media_type) VALUES($1, $2) ON CONFLICT DO NOTHING",
        )
        .bind(genre_id)
        .bind(&mt)
        .execute(&state.pool)
        .await;
    }

    invalidate_cache(&state).await;

    let types: Vec<GenreTypeRow> = sqlx::query_as(
        "SELECT genre_id, media_type, is_hidden, created_at FROM genre_media_type WHERE genre_id = $1 ORDER BY media_type",
    )
    .bind(genre_id)
    .fetch_all(&state.pool)
    .await
    .unwrap_or_default();

    (
        StatusCode::CREATED,
        Json(json!(GenreDetail {
            id: genre_id,
            name,
            usage_count: 0,
            types,
        })),
    )
        .into_response()
}

/// PATCH /api/v1/admin/genres/{id}  body: {name?, types?}
pub async fn update_genre(
    headers: HeaderMap,
    Path(id): Path<i32>,
    State(state): State<Arc<AppState>>,
    Json(body): Json<UpdateGenreRequest>,
) -> impl IntoResponse {
    require_admin!(&headers, state);

    if let Some(ref name) = body.name {
        let name = name.trim().to_string();
        if name.is_empty() {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "name must not be empty"})),
            )
                .into_response();
        }
        match sqlx::query("UPDATE genre SET name = $1 WHERE id = $2")
            .bind(&name)
            .bind(id)
            .execute(&state.pool)
            .await
        {
            Ok(r) if r.rows_affected() == 0 => {
                return (
                    StatusCode::NOT_FOUND,
                    Json(json!({"detail": "genre not found"})),
                )
                    .into_response();
            }
            Err(e) => {
                return (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({"detail": e.to_string()})),
                )
                    .into_response();
            }
            _ => {}
        }
    }

    // Update per-type is_hidden flags; upsert so new types can be added.
    if let Some(ref type_updates) = body.types {
        for tu in type_updates {
            let mt = tu.media_type.to_ascii_lowercase();
            if crate::db::MediaType::from_wire(&mt).is_none() {
                continue;
            }
            let _ = sqlx::query(
                "INSERT INTO genre_media_type(genre_id, media_type, is_hidden) \
                 VALUES($1, $2, $3) \
                 ON CONFLICT(genre_id, media_type) DO UPDATE SET is_hidden = EXCLUDED.is_hidden",
            )
            .bind(id)
            .bind(&mt)
            .bind(tu.is_hidden)
            .execute(&state.pool)
            .await;
        }
    }

    invalidate_cache(&state).await;

    // Return updated state.
    let genre: Option<GenreRow> = sqlx::query_as(
        "SELECT g.id, g.name, COUNT(mgl.media_id) AS usage_count \
         FROM genre g LEFT JOIN media_genre_link mgl ON mgl.genre_id = g.id \
         WHERE g.id = $1 GROUP BY g.id",
    )
    .bind(id)
    .fetch_optional(&state.pool)
    .await
    .ok()
    .flatten();

    let Some(genre) = genre else {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "genre not found"})),
        )
            .into_response();
    };

    let types: Vec<GenreTypeRow> = sqlx::query_as(
        "SELECT genre_id, media_type, is_hidden, created_at FROM genre_media_type WHERE genre_id = $1 ORDER BY media_type",
    )
    .bind(id)
    .fetch_all(&state.pool)
    .await
    .unwrap_or_default();

    Json(json!(GenreDetail {
        id: genre.id,
        name: genre.name,
        usage_count: genre.usage_count,
        types,
    }))
    .into_response()
}

/// DELETE /api/v1/admin/genres/{id}
pub async fn delete_genre(
    headers: HeaderMap,
    Path(id): Path<i32>,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    require_admin!(&headers, state);

    match sqlx::query("DELETE FROM genre WHERE id = $1")
        .bind(id)
        .execute(&state.pool)
        .await
    {
        Ok(r) if r.rows_affected() == 0 => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "genre not found"})),
        )
            .into_response(),
        Ok(_) => {
            invalidate_cache(&state).await;
            (StatusCode::NO_CONTENT).into_response()
        }
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"detail": e.to_string()})),
        )
            .into_response(),
    }
}

/// DELETE /api/v1/admin/genres/{id}/types/{media_type}
pub async fn delete_genre_type(
    headers: HeaderMap,
    Path((id, media_type)): Path<(i32, String)>,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    require_admin!(&headers, state);

    let mt = media_type.to_ascii_lowercase();
    match sqlx::query("DELETE FROM genre_media_type WHERE genre_id = $1 AND media_type = $2")
        .bind(id)
        .bind(&mt)
        .execute(&state.pool)
        .await
    {
        Ok(r) if r.rows_affected() == 0 => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "genre type pairing not found"})),
        )
            .into_response(),
        Ok(_) => {
            invalidate_cache(&state).await;
            (StatusCode::NO_CONTENT).into_response()
        }
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"detail": e.to_string()})),
        )
            .into_response(),
    }
}

/// POST /api/v1/admin/genres/reload — clear genres Redis cache
pub async fn reload_genres_cache(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    require_admin!(&headers, state);
    invalidate_cache(&state).await;
    Json(json!({"detail": "genres cache cleared"})).into_response()
}
