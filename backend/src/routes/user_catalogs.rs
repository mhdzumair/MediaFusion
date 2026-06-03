/// User catalog management endpoints.
///
/// Routes (prefix /api/v1/user/catalogs):
///   POST   /                              → create_user_catalog
///   GET    /                              → list_user_catalogs
///   GET    /public                        → list_public_catalogs
///   GET    /subscribed                    → list_subscribed_catalogs
///   GET    /share/{uuid}                  → get_catalog_by_share_link
///   GET    /{catalog_id}                  → get_user_catalog
///   PATCH  /{catalog_id}                  → update_user_catalog
///   DELETE /{catalog_id}                  → delete_user_catalog
///   GET    /{catalog_id}/items            → list_catalog_items
///   POST   /{catalog_id}/items            → add_catalog_item
///   DELETE /{catalog_id}/items/{item_id}  → remove_catalog_item
///   PUT    /{catalog_id}/items/reorder    → reorder_items
///   POST   /{catalog_id}/subscribe        → subscribe_catalog
///   DELETE /{catalog_id}/subscribe        → unsubscribe_catalog
///   GET    /{catalog_id}/subscribed       → check_subscription
use std::sync::Arc;

use axum::{
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use chrono::{DateTime, Utc};
use serde::Deserialize;

use crate::{routes::auth_guard, state::AppState};

// ─── Auth helper ──────────────────────────────────────────────────────────────

// Optional auth — returns None if no/invalid/inactive token (rather than 401)
async fn optional_token(
    pool: &sqlx::PgPool,
    headers: &HeaderMap,
    secret_key: &str,
) -> Option<i32> {
    auth_guard::validate_active_user(pool, headers, secret_key).await
}

// ─── Request / Response structs ───────────────────────────────────────────────

#[derive(Deserialize)]
pub struct CatalogCreate {
    pub name: String,
    pub description: Option<String>,
    pub poster: Option<String>,
    #[serde(default)]
    pub is_public: bool,
}

#[derive(Deserialize)]
pub struct CatalogUpdate {
    pub name: Option<String>,
    pub description: Option<String>,
    pub poster: Option<String>,
    pub is_public: Option<bool>,
}

#[derive(Deserialize)]
pub struct CatalogListQuery {
    #[serde(default = "default_limit")]
    pub limit: i32,
    #[serde(default)]
    pub offset: i32,
}

fn default_limit() -> i32 {
    50
}

#[derive(Deserialize)]
pub struct CatalogItemListQuery {
    #[serde(default = "default_items_limit")]
    pub limit: i32,
    #[serde(default)]
    pub offset: i32,
}

fn default_items_limit() -> i32 {
    100
}

#[derive(Deserialize)]
pub struct CatalogItemAdd {
    pub media_id: Option<i32>,
    pub stream_id: Option<i32>,
    pub notes: Option<String>,
}

#[derive(Deserialize)]
pub struct ReorderRequest {
    pub item_ids: Vec<i32>,
}

// ─── DB row type ──────────────────────────────────────────────────────────────

type CatalogRow = (
    i32,
    String,
    i32,
    String,
    Option<String>,
    Option<String>,
    bool,
    i32,
    i32,
    DateTime<Utc>,
    Option<DateTime<Utc>>,
);

fn catalog_row_to_json(r: &CatalogRow) -> serde_json::Value {
    serde_json::json!({
        "id": r.0,
        "share_code": r.1,
        "user_id": r.2,
        "name": r.3,
        "description": r.4,
        "poster": r.5,
        "is_public": r.6,
        "item_count": r.7,
        "subscriber_count": r.8,
        "created_at": r.9.to_rfc3339(),
        "updated_at": r.10.map(|d| d.to_rfc3339()),
    })
}

type CatalogItemRow = (i32, i32, Option<i32>, i32, Option<String>, DateTime<Utc>);

fn catalog_item_to_json(r: &CatalogItemRow) -> serde_json::Value {
    serde_json::json!({
        "id": r.0,
        "catalog_id": r.1,
        "media_id": r.2,
        "display_order": r.3,
        "notes": r.4,
        "added_at": r.5.to_rfc3339(),
    })
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// POST /api/v1/user/catalogs
pub async fn create_user_catalog(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<CatalogCreate>,
) -> Response {
    let user_id = match auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    if body.name.is_empty() || body.name.len() > 100 {
        return (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(serde_json::json!({"detail": "Name must be 1-100 characters"})),
        )
            .into_response();
    }

    let row: CatalogRow = match sqlx::query_as(
        r#"INSERT INTO user_catalog (user_id, name, description, poster_url, is_public)
           VALUES ($1, $2, $3, $4, $5)
           RETURNING id, share_code, user_id, name, description, poster_url, is_public,
                     (SELECT COUNT(*) FROM user_catalog_item WHERE catalog_id = id) AS item_count,
                     (SELECT COUNT(*) FROM user_catalog_subscription WHERE catalog_id = id) AS subscriber_count,
                     created_at, updated_at"#,
    )
    .bind(user_id)
    .bind(&body.name)
    .bind(&body.description)
    .bind(&body.poster)
    .bind(body.is_public)
    .fetch_one(&state.pool)
    .await
    {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("create_user_catalog insert: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    (StatusCode::CREATED, Json(catalog_row_to_json(&row))).into_response()
}

/// GET /api/v1/user/catalogs
pub async fn list_user_catalogs(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<CatalogListQuery>,
) -> Response {
    let user_id = match auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await {
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
    let rows: Vec<CatalogRow> = match sqlx::query_as(
        r#"SELECT c.id, c.share_code, c.user_id, c.name, c.description, c.poster_url, c.is_public,
                  (SELECT COUNT(*) FROM user_catalog_item WHERE catalog_id = c.id) AS item_count,
                  (SELECT COUNT(*) FROM user_catalog_subscription WHERE catalog_id = c.id) AS subscriber_count,
                  c.created_at, c.updated_at
           FROM user_catalog c
           WHERE c.user_id = $1
           ORDER BY c.created_at DESC
           LIMIT $2 OFFSET $3"#,
    )
    .bind(user_id)
    .bind(limit)
    .bind(params.offset)
    .fetch_all(&state.pool_ro)
    .await
    {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("list_user_catalogs: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    let catalogs: Vec<serde_json::Value> = rows.iter().map(catalog_row_to_json).collect();
    Json(serde_json::json!({
        "catalogs": catalogs,
        "total": catalogs.len(),
    }))
    .into_response()
}

/// GET /api/v1/user/catalogs/public
pub async fn list_public_catalogs(
    State(state): State<Arc<AppState>>,
    Query(params): Query<CatalogListQuery>,
) -> Response {
    let limit = params.limit.clamp(1, 100);
    let rows: Vec<CatalogRow> = match sqlx::query_as(
        r#"SELECT c.id, c.share_code, c.user_id, c.name, c.description, c.poster_url, c.is_public,
                  (SELECT COUNT(*) FROM user_catalog_item WHERE catalog_id = c.id) AS item_count,
                  (SELECT COUNT(*) FROM user_catalog_subscription WHERE catalog_id = c.id) AS subscriber_count,
                  c.created_at, c.updated_at
           FROM user_catalog c
           WHERE c.is_public = true
           ORDER BY c.created_at DESC
           LIMIT $1 OFFSET $2"#,
    )
    .bind(limit)
    .bind(params.offset)
    .fetch_all(&state.pool_ro)
    .await
    {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("list_public_catalogs: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    let catalogs: Vec<serde_json::Value> = rows.iter().map(catalog_row_to_json).collect();
    Json(serde_json::json!({
        "catalogs": catalogs,
        "total": catalogs.len(),
    }))
    .into_response()
}

/// GET /api/v1/user/catalogs/subscribed
pub async fn list_subscribed_catalogs(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> Response {
    let user_id = match auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let rows: Vec<CatalogRow> = match sqlx::query_as(
        r#"SELECT c.id, c.share_code, c.user_id, c.name, c.description, c.poster_url, c.is_public,
                  (SELECT COUNT(*) FROM user_catalog_item WHERE catalog_id = c.id) AS item_count,
                  (SELECT COUNT(*) FROM user_catalog_subscription WHERE catalog_id = c.id) AS subscriber_count,
                  c.created_at, c.updated_at
           FROM user_catalog c
           JOIN user_catalog_subscription s ON s.catalog_id = c.id
           WHERE s.user_id = $1
           ORDER BY s.subscribed_at DESC"#,
    )
    .bind(user_id)
    .fetch_all(&state.pool_ro)
    .await
    {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("list_subscribed_catalogs: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    let catalogs: Vec<serde_json::Value> = rows.iter().map(catalog_row_to_json).collect();
    Json(serde_json::json!({
        "catalogs": catalogs,
        "total": catalogs.len(),
    }))
    .into_response()
}

/// GET /api/v1/user/catalogs/share/{uuid}
pub async fn get_catalog_by_share_link(
    State(state): State<Arc<AppState>>,
    Path(uuid): Path<String>,
) -> Response {
    let row: Option<CatalogRow> = match sqlx::query_as(
        r#"SELECT c.id, c.share_code, c.user_id, c.name, c.description, c.poster_url, c.is_public,
                  (SELECT COUNT(*) FROM user_catalog_item WHERE catalog_id = c.id) AS item_count,
                  (SELECT COUNT(*) FROM user_catalog_subscription WHERE catalog_id = c.id) AS subscriber_count,
                  c.created_at, c.updated_at
           FROM user_catalog c
           WHERE c.share_code = $1"#,
    )
    .bind(&uuid)
    .fetch_optional(&state.pool_ro)
    .await
    {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("get_catalog_by_share_link: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    match row {
        None => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"detail": "Catalog not found"})),
        )
            .into_response(),
        Some(r) if !r.6 => (
            StatusCode::FORBIDDEN,
            Json(serde_json::json!({"detail": "This catalog is not public"})),
        )
            .into_response(),
        Some(r) => Json(catalog_row_to_json(&r)).into_response(),
    }
}

/// GET /api/v1/user/catalogs/{catalog_id}
pub async fn get_user_catalog(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(catalog_id): Path<i32>,
) -> Response {
    let user_id = optional_token(&state.pool, &headers, &state.config.secret_key_raw).await;

    let row: Option<CatalogRow> = match sqlx::query_as(
        r#"SELECT c.id, c.share_code, c.user_id, c.name, c.description, c.poster_url, c.is_public,
                  (SELECT COUNT(*) FROM user_catalog_item WHERE catalog_id = c.id) AS item_count,
                  (SELECT COUNT(*) FROM user_catalog_subscription WHERE catalog_id = c.id) AS subscriber_count,
                  c.created_at, c.updated_at
           FROM user_catalog c
           WHERE c.id = $1"#,
    )
    .bind(catalog_id)
    .fetch_optional(&state.pool_ro)
    .await
    {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("get_user_catalog: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    match row {
        None => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"detail": "Catalog not found"})),
        )
            .into_response(),
        Some(r) => {
            if !r.6 {
                // private
                match user_id {
                    Some(uid) if uid == r.2 => {}
                    _ => {
                        return (
                            StatusCode::FORBIDDEN,
                            Json(serde_json::json!({"detail": "Not authorized to view this catalog"})),
                        )
                            .into_response();
                    }
                }
            }
            Json(catalog_row_to_json(&r)).into_response()
        }
    }
}

/// PATCH /api/v1/user/catalogs/{catalog_id}
pub async fn update_user_catalog(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(catalog_id): Path<i32>,
    Json(body): Json<CatalogUpdate>,
) -> Response {
    let user_id = match auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    // Fetch catalog
    let existing: Option<(i32, i32, bool)> =
        match sqlx::query_as("SELECT id, user_id, is_public FROM user_catalog WHERE id = $1")
            .bind(catalog_id)
            .fetch_optional(&state.pool)
            .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("update_user_catalog fetch: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        };

    let (_, catalog_owner, _) = match existing {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({"detail": "Catalog not found"})),
            )
                .into_response();
        }
        Some(r) => r,
    };

    if catalog_owner != user_id {
        return (
            StatusCode::FORBIDDEN,
            Json(serde_json::json!({"detail": "Not authorized to update this catalog"})),
        )
            .into_response();
    }

    // Build update
    let mut set_parts: Vec<String> = Vec::new();
    let mut idx = 1i32;

    if body.name.is_some() {
        idx += 1;
        set_parts.push(format!("name = ${idx}"));
    }
    if body.description.is_some() {
        idx += 1;
        set_parts.push(format!("description = ${idx}"));
    }
    if body.poster.is_some() {
        idx += 1;
        set_parts.push(format!("poster = ${idx}"));
    }
    if body.is_public.is_some() {
        idx += 1;
        set_parts.push(format!("is_public = ${idx}"));
    }

    if set_parts.is_empty() {
        // Nothing to update — return current
        let row: CatalogRow = sqlx::query_as(
            r#"SELECT c.id, c.share_code, c.user_id, c.name, c.description, c.poster_url, c.is_public,
                      (SELECT COUNT(*) FROM user_catalog_item WHERE catalog_id = c.id) AS item_count,
                      (SELECT COUNT(*) FROM user_catalog_subscription WHERE catalog_id = c.id) AS subscriber_count,
                      c.created_at, c.updated_at
               FROM user_catalog c WHERE c.id = $1"#,
        )
        .bind(catalog_id)
        .fetch_one(&state.pool)
        .await
        .unwrap();
        return Json(catalog_row_to_json(&row)).into_response();
    }

    set_parts.push("updated_at = NOW()".to_string());
    let id_placeholder = 1i32;
    let sql = format!(
        r#"UPDATE user_catalog SET {} WHERE id = ${id_placeholder}
           RETURNING id, share_code, user_id, name, description, poster_url, is_public,
                     (SELECT COUNT(*) FROM user_catalog_item WHERE catalog_id = id) AS item_count,
                     (SELECT COUNT(*) FROM user_catalog_subscription WHERE catalog_id = id) AS subscriber_count,
                     created_at, updated_at"#,
        set_parts.join(", ")
    );

    let mut q = sqlx::query_as::<_, CatalogRow>(&sql).bind(catalog_id);
    if let Some(ref name) = body.name {
        q = q.bind(name.clone());
    }
    if let Some(ref desc) = body.description {
        q = q.bind(desc.clone());
    }
    if let Some(ref poster) = body.poster {
        q = q.bind(poster.clone());
    }
    if let Some(is_public) = body.is_public {
        q = q.bind(is_public);
    }

    match q.fetch_one(&state.pool).await {
        Ok(r) => Json(catalog_row_to_json(&r)).into_response(),
        Err(e) => {
            tracing::error!("update_user_catalog update: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

/// DELETE /api/v1/user/catalogs/{catalog_id}
pub async fn delete_user_catalog(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(catalog_id): Path<i32>,
) -> Response {
    let user_id = match auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let existing: Option<(i32, i32)> =
        match sqlx::query_as("SELECT id, user_id FROM user_catalog WHERE id = $1")
            .bind(catalog_id)
            .fetch_optional(&state.pool)
            .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("delete_user_catalog fetch: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        };

    match existing {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({"detail": "Catalog not found"})),
            )
                .into_response();
        }
        Some((_, owner)) if owner != user_id => {
            return (
                StatusCode::FORBIDDEN,
                Json(serde_json::json!({"detail": "Not authorized to delete this catalog"})),
            )
                .into_response();
        }
        _ => {}
    }

    match sqlx::query("DELETE FROM user_catalog WHERE id = $1")
        .bind(catalog_id)
        .execute(&state.pool)
        .await
    {
        Ok(_) => StatusCode::NO_CONTENT.into_response(),
        Err(e) => {
            tracing::error!("delete_user_catalog delete: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

/// GET /api/v1/user/catalogs/{catalog_id}/items
pub async fn list_catalog_items(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(catalog_id): Path<i32>,
    Query(params): Query<CatalogItemListQuery>,
) -> Response {
    let user_id = optional_token(&state.pool, &headers, &state.config.secret_key_raw).await;

    // Check catalog exists and access
    let catalog: Option<(i32, i32, bool)> =
        match sqlx::query_as("SELECT id, user_id, is_public FROM user_catalog WHERE id = $1")
            .bind(catalog_id)
            .fetch_optional(&state.pool_ro)
            .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("list_catalog_items catalog check: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        };

    match catalog {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({"detail": "Catalog not found"})),
            )
                .into_response();
        }
        Some((_, owner, is_public)) => {
            if !is_public {
                match user_id {
                    Some(uid) if uid == owner => {}
                    _ => {
                        return (
                            StatusCode::FORBIDDEN,
                            Json(serde_json::json!({"detail": "Not authorized to view this catalog"})),
                        )
                            .into_response();
                    }
                }
            }
        }
    }

    let limit = params.limit.clamp(1, 500);
    let rows: Vec<CatalogItemRow> = match sqlx::query_as(
        r#"SELECT id, catalog_id, media_id, display_order, notes, added_at
           FROM user_catalog_item
           WHERE catalog_id = $1
           ORDER BY position ASC, added_at ASC
           LIMIT $2 OFFSET $3"#,
    )
    .bind(catalog_id)
    .bind(limit)
    .bind(params.offset)
    .fetch_all(&state.pool_ro)
    .await
    {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("list_catalog_items fetch: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    let items: Vec<serde_json::Value> = rows.iter().map(catalog_item_to_json).collect();
    Json(serde_json::json!({
        "items": items,
        "total": items.len(),
    }))
    .into_response()
}

/// POST /api/v1/user/catalogs/{catalog_id}/items
pub async fn add_catalog_item(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(catalog_id): Path<i32>,
    Json(body): Json<CatalogItemAdd>,
) -> Response {
    let user_id = match auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    if body.media_id.is_none() && body.stream_id.is_none() {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"detail": "Either media_id or stream_id must be provided"})),
        )
            .into_response();
    }

    // Check catalog ownership
    let catalog: Option<(i32, i32)> =
        match sqlx::query_as("SELECT id, user_id FROM user_catalog WHERE id = $1")
            .bind(catalog_id)
            .fetch_optional(&state.pool)
            .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("add_catalog_item catalog check: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        };

    match catalog {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({"detail": "Catalog not found"})),
            )
                .into_response();
        }
        Some((_, owner)) if owner != user_id => {
            return (
                StatusCode::FORBIDDEN,
                Json(serde_json::json!({"detail": "Not authorized to add to this catalog"})),
            )
                .into_response();
        }
        _ => {}
    }

    // Get next position
    let next_position: i32 = sqlx::query_scalar(
        "SELECT COALESCE(MAX(position), 0) + 1 FROM user_catalog_item WHERE catalog_id = $1",
    )
    .bind(catalog_id)
    .fetch_one(&state.pool)
    .await
    .unwrap_or(1);

    let row: CatalogItemRow = match sqlx::query_as(
        r#"INSERT INTO user_catalog_item (catalog_id, media_id, stream_id, notes, position, added_at)
           VALUES ($1, $2, $3, $4, $5, NOW())
           RETURNING id, catalog_id, media_id, display_order, notes, added_at"#,
    )
    .bind(catalog_id)
    .bind(body.media_id)
    .bind(body.stream_id)
    .bind(&body.notes)
    .bind(next_position)
    .fetch_one(&state.pool)
    .await
    {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("add_catalog_item insert: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    (StatusCode::CREATED, Json(catalog_item_to_json(&row))).into_response()
}

/// DELETE /api/v1/user/catalogs/{catalog_id}/items/{item_id}
pub async fn remove_catalog_item(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path((catalog_id, item_id)): Path<(i32, i32)>,
) -> Response {
    let user_id = match auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    // Check ownership
    let owner: Option<i32> =
        match sqlx::query_scalar("SELECT user_id FROM user_catalog WHERE id = $1")
            .bind(catalog_id)
            .fetch_optional(&state.pool)
            .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("remove_catalog_item ownership check: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        };

    match owner {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({"detail": "Catalog not found"})),
            )
                .into_response();
        }
        Some(o) if o != user_id => {
            return (
                StatusCode::FORBIDDEN,
                Json(serde_json::json!({"detail": "Not authorized to remove from this catalog"})),
            )
                .into_response();
        }
        _ => {}
    }

    let result = sqlx::query("DELETE FROM user_catalog_item WHERE id = $1 AND catalog_id = $2")
        .bind(item_id)
        .bind(catalog_id)
        .execute(&state.pool)
        .await;

    match result {
        Ok(r) if r.rows_affected() == 0 => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"detail": "Item not found in catalog"})),
        )
            .into_response(),
        Ok(_) => StatusCode::NO_CONTENT.into_response(),
        Err(e) => {
            tracing::error!("remove_catalog_item delete: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

/// PUT /api/v1/user/catalogs/{catalog_id}/items/reorder
pub async fn reorder_items(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(catalog_id): Path<i32>,
    Json(body): Json<ReorderRequest>,
) -> Response {
    let user_id = match auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    if body.item_ids.is_empty() {
        return (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(serde_json::json!({"detail": "item_ids must not be empty"})),
        )
            .into_response();
    }

    let owner: Option<i32> =
        match sqlx::query_scalar("SELECT user_id FROM user_catalog WHERE id = $1")
            .bind(catalog_id)
            .fetch_optional(&state.pool)
            .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("reorder_items ownership: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        };

    match owner {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({"detail": "Catalog not found"})),
            )
                .into_response();
        }
        Some(o) if o != user_id => {
            return (
                StatusCode::FORBIDDEN,
                Json(serde_json::json!({"detail": "Not authorized to reorder this catalog"})),
            )
                .into_response();
        }
        _ => {}
    }

    // Update positions in a transaction
    let mut tx = match state.pool.begin().await {
        Ok(t) => t,
        Err(e) => {
            tracing::error!("reorder_items begin tx: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    for (position, &item_id) in body.item_ids.iter().enumerate() {
        if let Err(e) = sqlx::query(
            "UPDATE user_catalog_item SET position = $1 WHERE id = $2 AND catalog_id = $3",
        )
        .bind(position as i32 + 1)
        .bind(item_id)
        .bind(catalog_id)
        .execute(&mut *tx)
        .await
        {
            tracing::error!("reorder_items update item {item_id}: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    }

    if let Err(e) = tx.commit().await {
        tracing::error!("reorder_items commit: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    StatusCode::NO_CONTENT.into_response()
}

/// POST /api/v1/user/catalogs/{catalog_id}/subscribe
pub async fn subscribe_catalog(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(catalog_id): Path<i32>,
) -> Response {
    let user_id = match auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let catalog: Option<(i32, bool)> =
        match sqlx::query_as("SELECT user_id, is_public FROM user_catalog WHERE id = $1")
            .bind(catalog_id)
            .fetch_optional(&state.pool)
            .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("subscribe_catalog catalog fetch: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        };

    let (owner, is_public) = match catalog {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({"detail": "Catalog not found"})),
            )
                .into_response();
        }
        Some(r) => r,
    };

    if !is_public {
        return (
            StatusCode::FORBIDDEN,
            Json(serde_json::json!({"detail": "Cannot subscribe to a private catalog"})),
        )
            .into_response();
    }

    if owner == user_id {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"detail": "Cannot subscribe to your own catalog"})),
        )
            .into_response();
    }

    let existing: Option<i32> = sqlx::query_scalar(
        "SELECT 1 FROM user_catalog_subscription WHERE user_id = $1 AND catalog_id = $2",
    )
    .bind(user_id)
    .bind(catalog_id)
    .fetch_optional(&state.pool)
    .await
    .unwrap_or(None);

    if existing.is_some() {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"detail": "Already subscribed to this catalog"})),
        )
            .into_response();
    }

    match sqlx::query(
        "INSERT INTO user_catalog_subscription (user_id, catalog_id, subscribed_at) VALUES ($1, $2, NOW())",
    )
    .bind(user_id)
    .bind(catalog_id)
    .execute(&state.pool)
    .await
    {
        Ok(_) => (
            StatusCode::CREATED,
            Json(serde_json::json!({"message": "Subscribed successfully"})),
        )
            .into_response(),
        Err(e) => {
            tracing::error!("subscribe_catalog insert: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

/// DELETE /api/v1/user/catalogs/{catalog_id}/subscribe
pub async fn unsubscribe_catalog(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(catalog_id): Path<i32>,
) -> Response {
    let user_id = match auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await {
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
        sqlx::query("DELETE FROM user_catalog_subscription WHERE user_id = $1 AND catalog_id = $2")
            .bind(user_id)
            .bind(catalog_id)
            .execute(&state.pool)
            .await;

    match result {
        Ok(r) if r.rows_affected() == 0 => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"detail": "Not subscribed to this catalog"})),
        )
            .into_response(),
        Ok(_) => StatusCode::NO_CONTENT.into_response(),
        Err(e) => {
            tracing::error!("unsubscribe_catalog: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

/// GET /api/v1/user/catalogs/{catalog_id}/subscribed
pub async fn check_subscription(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(catalog_id): Path<i32>,
) -> Response {
    let user_id = match auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let subscribed: Option<i64> = sqlx::query_scalar(
        "SELECT 1 FROM user_catalog_subscription WHERE user_id = $1 AND catalog_id = $2",
    )
    .bind(user_id)
    .bind(catalog_id)
    .fetch_optional(&state.pool_ro)
    .await
    .unwrap_or(None);

    Json(serde_json::json!({"subscribed": subscribed.is_some()})).into_response()
}
