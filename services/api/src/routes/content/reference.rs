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
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::Utc;
use hmac::{Hmac, Mac};
use serde::Deserialize;
use serde_json::json;
use sha2::Sha256;

use crate::state::AppState;

// ─── Auth ─────────────────────────────────────────────────────────────────────

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
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<RefQuery>,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }

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

        let rows: Vec<(i32, String, i64)> = sqlx::query_as(
            r#"SELECT g.id, g.name, COUNT(mgl.media_id) as usage_count
               FROM genre g
               LEFT JOIN media_genre_link mgl ON g.id = mgl.genre_id
               WHERE g.name ILIKE $1
               GROUP BY g.id, g.name
               ORDER BY g.name
               LIMIT $2 OFFSET $3"#,
        )
        .bind(&pattern)
        .bind(per_page)
        .bind(offset)
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default();

        (total, rows)
    } else {
        let total: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM genre")
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);

        let rows: Vec<(i32, String, i64)> = sqlx::query_as(
            r#"SELECT g.id, g.name, COUNT(mgl.media_id) as usage_count
               FROM genre g
               LEFT JOIN media_genre_link mgl ON g.id = mgl.genre_id
               GROUP BY g.id, g.name
               ORDER BY g.name
               LIMIT $1 OFFSET $2"#,
        )
        .bind(per_page)
        .bind(offset)
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default();

        (total, rows)
    };

    let items: Vec<serde_json::Value> = rows
        .into_iter()
        .filter(|(_, name, _)| {
            let lower = name.to_lowercase();
            lower != "adult" && lower != "18+"
        })
        .map(|(id, name, usage_count)| json!({"id": id, "name": name, "usage_count": usage_count}))
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
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<RefQuery>,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }

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

        let rows: Vec<(i32, String, i64)> = sqlx::query_as(
            r#"SELECT c.id, c.name, COUNT(mcl.media_id) as usage_count
               FROM catalog c
               LEFT JOIN media_catalog_link mcl ON c.id = mcl.catalog_id
               WHERE c.name ILIKE $1
               GROUP BY c.id, c.name
               ORDER BY c.name
               LIMIT $2 OFFSET $3"#,
        )
        .bind(&pattern)
        .bind(per_page)
        .bind(offset)
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default();

        (total, rows)
    } else {
        let total: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM catalog")
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);

        let rows: Vec<(i32, String, i64)> = sqlx::query_as(
            r#"SELECT c.id, c.name, COUNT(mcl.media_id) as usage_count
               FROM catalog c
               LEFT JOIN media_catalog_link mcl ON c.id = mcl.catalog_id
               GROUP BY c.id, c.name
               ORDER BY c.name
               LIMIT $1 OFFSET $2"#,
        )
        .bind(per_page)
        .bind(offset)
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default();

        (total, rows)
    };

    let items: Vec<serde_json::Value> = rows
        .into_iter()
        .map(|(id, name, usage_count)| json!({"id": id, "name": name, "usage_count": usage_count}))
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
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<RefQuery>,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }

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

        let rows: Vec<(i32, String, i64)> = sqlx::query_as(
            r#"SELECT p.id, p.name, COUNT(mc.media_id) as usage_count
               FROM person p
               LEFT JOIN media_cast mc ON p.id = mc.person_id
               WHERE p.name ILIKE $1
               GROUP BY p.id, p.name
               ORDER BY p.name
               LIMIT $2 OFFSET $3"#,
        )
        .bind(&pattern)
        .bind(per_page)
        .bind(offset)
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default();

        (total, rows)
    } else {
        let total: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM person")
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);

        let rows: Vec<(i32, String, i64)> = sqlx::query_as(
            r#"SELECT p.id, p.name, COUNT(mc.media_id) as usage_count
               FROM person p
               LEFT JOIN media_cast mc ON p.id = mc.person_id
               GROUP BY p.id, p.name
               ORDER BY p.name
               LIMIT $1 OFFSET $2"#,
        )
        .bind(per_page)
        .bind(offset)
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default();

        (total, rows)
    };

    let items: Vec<serde_json::Value> = rows
        .into_iter()
        .map(|(id, name, usage_count)| json!({"id": id, "name": name, "usage_count": usage_count}))
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
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<RefQuery>,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }

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

        let rows: Vec<(i32, String, i64)> = sqlx::query_as(
            r#"SELECT pc.id, pc.name, COUNT(mpcl.media_id) as usage_count
               FROM parental_certificate pc
               LEFT JOIN media_parental_certificate_link mpcl ON pc.id = mpcl.certificate_id
               WHERE pc.name ILIKE $1
               GROUP BY pc.id, pc.name
               ORDER BY pc.name
               LIMIT $2 OFFSET $3"#,
        )
        .bind(&pattern)
        .bind(per_page)
        .bind(offset)
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default();

        (total, rows)
    } else {
        let total: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM parental_certificate")
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);

        let rows: Vec<(i32, String, i64)> = sqlx::query_as(
            r#"SELECT pc.id, pc.name, COUNT(mpcl.media_id) as usage_count
               FROM parental_certificate pc
               LEFT JOIN media_parental_certificate_link mpcl ON pc.id = mpcl.certificate_id
               GROUP BY pc.id, pc.name
               ORDER BY pc.name
               LIMIT $1 OFFSET $2"#,
        )
        .bind(per_page)
        .bind(offset)
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default();

        (total, rows)
    };

    let items: Vec<serde_json::Value> = rows
        .into_iter()
        .map(|(id, name, usage_count)| json!({"id": id, "name": name, "usage_count": usage_count}))
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
