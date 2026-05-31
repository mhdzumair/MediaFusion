/// Reference data endpoints for metadata editing flows.
///
/// Routes (prefix /api/v1/metadata/reference):
///   GET /genres               → list_genres
///   GET /catalogs             → list_catalogs
///   GET /stars                → list_stars
///   GET /parental-certificates → list_parental_certificates
use std::sync::Arc;

use axum::{
    extract::{Query, State},
    response::{IntoResponse, Response},
    Json,
};
use serde::Deserialize;
use serde_json::json;

use crate::state::AppState;

#[derive(Debug)]
struct ReferenceListRow {
    id: i32,
    name: String,
    usage_count: i64,
}

// ─── Query ────────────────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct RefQuery {
    pub search: Option<String>,
    #[serde(default = "default_page")]
    pub page: i64,
    #[serde(default = "default_per_page")]
    pub per_page: i64,
}

fn default_page() -> i64 {
    1
}
fn default_per_page() -> i64 {
    50
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// GET /api/v1/metadata/reference/genres
pub async fn list_genres(
    State(state): State<Arc<AppState>>,
    Query(params): Query<RefQuery>,
) -> Response {
    let page = params.page.max(1);
    let per_page = params.per_page.clamp(1, 100);
    let offset = (page - 1) * per_page;

    let (total, rows) = if let Some(ref search) = params.search {
        let pattern = format!("%{search}%");
        let total: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM genre WHERE name ILIKE $1")
            .bind(&pattern)
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);

        let rows = sqlx::query_as!(
            ReferenceListRow,
            r#"SELECT g.id, g.name, COUNT(mgl.media_id) as "usage_count!"
               FROM genre g
               LEFT JOIN media_genre_link mgl ON g.id = mgl.genre_id
               WHERE g.name ILIKE $1
               GROUP BY g.id, g.name
               ORDER BY g.name
               LIMIT $2 OFFSET $3"#,
            pattern,
            per_page,
            offset,
        )
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default();

        (total, rows)
    } else {
        let total: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM genre")
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);

        let rows = sqlx::query_as!(
            ReferenceListRow,
            r#"SELECT g.id, g.name, COUNT(mgl.media_id) as "usage_count!"
               FROM genre g
               LEFT JOIN media_genre_link mgl ON g.id = mgl.genre_id
               GROUP BY g.id, g.name
               ORDER BY g.name
               LIMIT $1 OFFSET $2"#,
            per_page,
            offset,
        )
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default();

        (total, rows)
    };

    let items: Vec<serde_json::Value> = rows
        .into_iter()
        .filter(|row| {
            let lower = row.name.to_lowercase();
            lower != "adult" && lower != "18+"
        })
        .map(|row| {
            json!({"id": row.id, "name": row.name, "usage_count": row.usage_count})
        })
        .collect();

    let pages = if total > 0 {
        (total + per_page - 1) / per_page
    } else {
        1
    };
    Json(json!({
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
        "has_more": page < pages,
    }))
    .into_response()
}

/// GET /api/v1/metadata/reference/catalogs
pub async fn list_catalogs(
    State(state): State<Arc<AppState>>,
    Query(params): Query<RefQuery>,
) -> Response {
    let page = params.page.max(1);
    let per_page = params.per_page.clamp(1, 100);
    let offset = (page - 1) * per_page;

    let (total, rows) = if let Some(ref search) = params.search {
        let pattern = format!("%{search}%");
        let total: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM catalog WHERE name ILIKE $1")
            .bind(&pattern)
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);

        let rows = sqlx::query_as!(
            ReferenceListRow,
            r#"SELECT c.id, c.name, COUNT(mcl.media_id) as "usage_count!"
               FROM catalog c
               LEFT JOIN media_catalog_link mcl ON c.id = mcl.catalog_id
               WHERE c.name ILIKE $1
               GROUP BY c.id, c.name
               ORDER BY c.name
               LIMIT $2 OFFSET $3"#,
            pattern,
            per_page,
            offset,
        )
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default();

        (total, rows)
    } else {
        let total: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM catalog")
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);

        let rows = sqlx::query_as!(
            ReferenceListRow,
            r#"SELECT c.id, c.name, COUNT(mcl.media_id) as "usage_count!"
               FROM catalog c
               LEFT JOIN media_catalog_link mcl ON c.id = mcl.catalog_id
               GROUP BY c.id, c.name
               ORDER BY c.name
               LIMIT $1 OFFSET $2"#,
            per_page,
            offset,
        )
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default();

        (total, rows)
    };

    let items: Vec<serde_json::Value> = rows
        .into_iter()
        .map(|row| json!({"id": row.id, "name": row.name, "usage_count": row.usage_count}))
        .collect();

    let pages = if total > 0 {
        (total + per_page - 1) / per_page
    } else {
        1
    };
    Json(json!({
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
        "has_more": page < pages,
    }))
    .into_response()
}

/// GET /api/v1/metadata/reference/stars
pub async fn list_stars(
    State(state): State<Arc<AppState>>,
    Query(params): Query<RefQuery>,
) -> Response {
    let page = params.page.max(1);
    let per_page = params.per_page.clamp(1, 100);
    let offset = (page - 1) * per_page;

    let (total, rows) = if let Some(ref search) = params.search {
        let pattern = format!("%{search}%");
        let total: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM person WHERE name ILIKE $1")
            .bind(&pattern)
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);

        let rows = sqlx::query_as!(
            ReferenceListRow,
            r#"SELECT p.id, p.name, COUNT(mc.media_id) as "usage_count!"
               FROM person p
               LEFT JOIN media_cast mc ON p.id = mc.person_id
               WHERE p.name ILIKE $1
               GROUP BY p.id, p.name
               ORDER BY p.name
               LIMIT $2 OFFSET $3"#,
            pattern,
            per_page,
            offset,
        )
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default();

        (total, rows)
    } else {
        let total: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM person")
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);

        let rows = sqlx::query_as!(
            ReferenceListRow,
            r#"SELECT p.id, p.name, COUNT(mc.media_id) as "usage_count!"
               FROM person p
               LEFT JOIN media_cast mc ON p.id = mc.person_id
               GROUP BY p.id, p.name
               ORDER BY p.name
               LIMIT $1 OFFSET $2"#,
            per_page,
            offset,
        )
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default();

        (total, rows)
    };

    let items: Vec<serde_json::Value> = rows
        .into_iter()
        .map(|row| json!({"id": row.id, "name": row.name, "usage_count": row.usage_count}))
        .collect();

    let pages = if total > 0 {
        (total + per_page - 1) / per_page
    } else {
        1
    };
    Json(json!({
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
        "has_more": page < pages,
    }))
    .into_response()
}

/// GET /api/v1/metadata/reference/parental-certificates
pub async fn list_parental_certificates(
    State(state): State<Arc<AppState>>,
    Query(params): Query<RefQuery>,
) -> Response {
    let page = params.page.max(1);
    let per_page = params.per_page.clamp(1, 100);
    let offset = (page - 1) * per_page;

    let (total, rows) = if let Some(ref search) = params.search {
        let pattern = format!("%{search}%");
        let total: i64 =
            sqlx::query_scalar("SELECT COUNT(*) FROM parental_certificate WHERE name ILIKE $1")
                .bind(&pattern)
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0);

        let rows = sqlx::query_as!(
            ReferenceListRow,
            r#"SELECT pc.id, pc.name, COUNT(mpcl.media_id) as "usage_count!"
               FROM parental_certificate pc
               LEFT JOIN media_parental_certificate_link mpcl ON pc.id = mpcl.certificate_id
               WHERE pc.name ILIKE $1
               GROUP BY pc.id, pc.name
               ORDER BY pc.name
               LIMIT $2 OFFSET $3"#,
            pattern,
            per_page,
            offset,
        )
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default();

        (total, rows)
    } else {
        let total: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM parental_certificate")
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);

        let rows = sqlx::query_as!(
            ReferenceListRow,
            r#"SELECT pc.id, pc.name, COUNT(mpcl.media_id) as "usage_count!"
               FROM parental_certificate pc
               LEFT JOIN media_parental_certificate_link mpcl ON pc.id = mpcl.certificate_id
               GROUP BY pc.id, pc.name
               ORDER BY pc.name
               LIMIT $1 OFFSET $2"#,
            per_page,
            offset,
        )
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default();

        (total, rows)
    };

    let items: Vec<serde_json::Value> = rows
        .into_iter()
        .map(|row| json!({"id": row.id, "name": row.name, "usage_count": row.usage_count}))
        .collect();

    let pages = if total > 0 {
        (total + per_page - 1) / per_page
    } else {
        1
    };
    Json(json!({
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
        "has_more": page < pages,
    }))
    .into_response()
}
