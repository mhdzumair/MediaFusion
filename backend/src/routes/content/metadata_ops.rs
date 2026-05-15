/// Metadata fetch/apply operations.
///
/// Routes (prefix /api/v1/metadata):
///   POST /{media_id}/refresh                → refresh_metadata
///   POST /{media_id}/link-external-id       → link_external_id
///   POST /{media_id}/link-multiple-external-ids → link_multiple_external_ids
///   GET  /{media_id}                        → get_media_metadata
///   GET  /search                            → search_metadata
use std::sync::Arc;

use axum::{
    extract::{Path, Query, Request, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::{DateTime, Utc};
use hmac::{Hmac, KeyInit, Mac};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::Sha256;

use crate::state::AppState;

// ─── Auth ─────────────────────────────────────────────────────────────────────

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

// ─── Request / response types ─────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct LinkExternalIdBody {
    pub provider: String,
    pub external_id: String,
}

#[derive(Deserialize)]
pub struct LinkMultipleExternalIdsBody {
    pub imdb_id: Option<String>,
    pub tmdb_id: Option<String>,
    pub tvdb_id: Option<String>,
    pub mal_id: Option<String>,
    pub trakt_id: Option<String>,
}

#[derive(Deserialize)]
pub struct SearchMetadataQuery {
    pub q: String,
    #[serde(rename = "type")]
    pub media_type: Option<String>,
    pub page: Option<i64>,
    pub page_size: Option<i64>,
}

#[derive(Serialize)]
struct ExternalIdRow {
    provider: String,
    external_id: String,
}

#[derive(Serialize)]
struct MediaMetadataRow {
    id: i32,
    title: String,
    #[serde(rename = "type")]
    media_type: String,
    year: Option<i32>,
    description: Option<String>,
    is_blocked: bool,
    blocked_at: Option<DateTime<Utc>>,
    last_scraped_at: Option<DateTime<Utc>>,
    external_ids: Vec<ExternalIdRow>,
}

#[derive(Serialize)]
struct SearchResultItem {
    id: i32,
    title: String,
    #[serde(rename = "type")]
    media_type: String,
    year: Option<i32>,
    is_blocked: bool,
    external_ids: Vec<ExternalIdRow>,
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

// ─── External metadata helpers (shared with metadata_ops) ────────────────────

fn cinemeta_media_type(media_type: &str) -> &str {
    if media_type.contains("movie") {
        "movie"
    } else {
        "series"
    }
}

async fn cinemeta_fetch_meta(
    http: &reqwest::Client,
    media_type: &str,
    imdb_id: &str,
) -> Option<Value> {
    let url = format!(
        "https://v3-cinemeta.strem.io/meta/{}/{}.json",
        cinemeta_media_type(media_type),
        imdb_id
    );
    let resp = http.get(&url).send().await.ok()?;
    if !resp.status().is_success() {
        return None;
    }
    let data: Value = resp.json().await.ok()?;
    Some(data.get("meta")?.clone())
}

async fn cinemeta_search(http: &reqwest::Client, media_type: &str, title: &str) -> Vec<Value> {
    let encoded = urlencoding::encode(title);
    let url = format!(
        "https://v3-cinemeta.strem.io/catalog/{}/top/search={}.json",
        cinemeta_media_type(media_type),
        encoded
    );
    let Ok(resp) = http.get(&url).send().await else {
        return vec![];
    };
    let Ok(data): Result<Value, _> = resp.json().await else {
        return vec![];
    };
    let metas = data["metas"].as_array().cloned().unwrap_or_default();
    metas
        .into_iter()
        .map(|m| {
            let year_val = m["year"]
                .as_str()
                .and_then(|y| y.split('-').next()?.parse::<i32>().ok())
                .or_else(|| m["year"].as_i64().map(|y| y as i32));
            json!({
                "provider": "imdb",
                "external_id": m["id"],
                "title": m["name"],
                "year": year_val,
                "poster": m["poster"],
            })
        })
        .collect()
}

async fn tmdb_search_ext(
    http: &reqwest::Client,
    api_key: &str,
    media_type: &str,
    title: &str,
) -> Vec<Value> {
    let encoded = urlencoding::encode(title);
    let tmdb_type = if media_type.contains("movie") {
        "movie"
    } else {
        "tv"
    };
    let url = format!(
        "https://api.themoviedb.org/3/search/{tmdb_type}?api_key={api_key}&query={encoded}"
    );
    let Ok(resp) = http.get(&url).send().await else {
        return vec![];
    };
    let Ok(data): Result<Value, _> = resp.json().await else {
        return vec![];
    };
    let results = data["results"].as_array().cloned().unwrap_or_default();
    results
        .into_iter()
        .map(|item| {
            let title_str = item["title"]
                .as_str()
                .or_else(|| item["name"].as_str())
                .unwrap_or("")
                .to_string();
            let year_str = item["release_date"]
                .as_str()
                .or_else(|| item["first_air_date"].as_str())
                .unwrap_or("");
            let year = if year_str.len() >= 4 {
                year_str[..4].parse::<i32>().ok()
            } else {
                None
            };
            let poster = item["poster_path"]
                .as_str()
                .map(|p| format!("https://image.tmdb.org/t/p/w500{p}"));
            let ext_id = item["id"]
                .as_i64()
                .map(|id| id.to_string())
                .unwrap_or_default();
            json!({
                "provider": "tmdb",
                "external_id": ext_id,
                "title": title_str,
                "year": year,
                "poster": poster,
            })
        })
        .collect()
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// POST /api/v1/metadata/{media_id}/refresh
pub async fn refresh_metadata(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i32>,
    _req: Request,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }

    // Check media exists
    let row: Option<(String,)> = match sqlx::query_as("SELECT type::text FROM media WHERE id = $1")
        .bind(media_id)
        .fetch_optional(&state.pool_ro)
        .await
    {
        Ok(v) => v,
        Err(e) => {
            tracing::error!("refresh_metadata: db error: {e}");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "Database error"})),
            )
                .into_response();
        }
    };

    let media_row = match row {
        Some(r) => r,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Media not found"})),
            )
                .into_response()
        }
    };
    let db_media_type = media_row.0.to_lowercase();

    // Get external IDs
    let ext_rows: Vec<(String, String)> =
        sqlx::query_as("SELECT provider, external_id FROM media_external_id WHERE media_id = $1")
            .bind(media_id)
            .fetch_all(&state.pool_ro)
            .await
            .unwrap_or_default();

    // Find IMDB ID
    let imdb_id = ext_rows
        .iter()
        .find(|(p, _)| p == "imdb")
        .map(|(_, id)| id.clone());

    let refreshed_from = if let Some(ref iid) = imdb_id {
        if let Some(meta) = cinemeta_fetch_meta(&state.http, &db_media_type, iid).await {
            let new_title = meta["name"].as_str().map(str::to_string);
            let new_year = meta["year"]
                .as_str()
                .and_then(|y| y.split('-').next()?.parse::<i32>().ok())
                .or_else(|| meta["year"].as_i64().map(|y| y as i32));
            let new_desc = meta["description"].as_str().map(str::to_string);

            let _ = sqlx::query(
                "UPDATE media SET
                   title = COALESCE($2, title),
                   year = COALESCE($3, year),
                   description = COALESCE($4, description),
                   last_scraped_at = NOW(),
                   updated_at = NOW()
                 WHERE id = $1",
            )
            .bind(media_id)
            .bind(&new_title)
            .bind(new_year)
            .bind(&new_desc)
            .execute(&state.pool)
            .await;

            "imdb"
        } else {
            // Still update last_scraped_at
            let _ = sqlx::query("UPDATE media SET last_scraped_at = NOW() WHERE id = $1")
                .bind(media_id)
                .execute(&state.pool)
                .await;
            "none"
        }
    } else {
        let _ = sqlx::query("UPDATE media SET last_scraped_at = NOW() WHERE id = $1")
            .bind(media_id)
            .execute(&state.pool)
            .await;
        "none"
    };

    Json(json!({
        "status": "success",
        "media_id": media_id,
        "refreshed_from": refreshed_from,
    }))
    .into_response()
}

/// POST /api/v1/metadata/{media_id}/link-external-id
pub async fn link_external_id(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i32>,
    Json(body): Json<LinkExternalIdBody>,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }

    // Check media exists
    let exists: bool = match sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM media WHERE id = $1)")
        .bind(media_id)
        .fetch_one(&state.pool)
        .await
    {
        Ok(v) => v,
        Err(e) => {
            tracing::error!("link_external_id: db error checking media: {e}");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "Database error"})),
            )
                .into_response();
        }
    };
    if !exists {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Media not found"})),
        )
            .into_response();
    }

    // Check for conflict: same provider+external_id linked to a different media
    let conflict: Option<i32> = match sqlx::query_scalar(
        "SELECT media_id FROM media_external_id WHERE provider = $1 AND external_id = $2 AND media_id != $3",
    )
    .bind(&body.provider)
    .bind(&body.external_id)
    .bind(media_id)
    .fetch_optional(&state.pool)
    .await
    {
        Ok(v) => v,
        Err(e) => {
            tracing::error!("link_external_id: db error checking conflict: {e}");
            return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"detail": "Database error"}))).into_response();
        }
    };
    if conflict.is_some() {
        return (
            StatusCode::CONFLICT,
            Json(json!({"detail": "External ID already linked to a different media"})),
        )
            .into_response();
    }

    // Upsert
    let result = sqlx::query(
        "INSERT INTO media_external_id (media_id, provider, external_id)
         VALUES ($1, $2, $3)
         ON CONFLICT (provider, external_id) DO UPDATE SET external_id = EXCLUDED.external_id",
    )
    .bind(media_id)
    .bind(&body.provider)
    .bind(&body.external_id)
    .execute(&state.pool)
    .await;

    match result {
        Ok(_) => (
            StatusCode::OK,
            Json(json!({
                "status": "success",
                "message": "External ID linked",
                "media_id": media_id,
                "provider": body.provider,
                "external_id": body.external_id,
            })),
        )
            .into_response(),
        Err(e) => {
            tracing::error!("link_external_id: upsert error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "Database error"})),
            )
                .into_response()
        }
    }
}

/// POST /api/v1/metadata/{media_id}/link-multiple-external-ids
pub async fn link_multiple_external_ids(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i32>,
    Json(body): Json<LinkMultipleExternalIdsBody>,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }

    // Check media exists
    let exists: bool = match sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM media WHERE id = $1)")
        .bind(media_id)
        .fetch_one(&state.pool)
        .await
    {
        Ok(v) => v,
        Err(e) => {
            tracing::error!("link_multiple_external_ids: db error: {e}");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "Database error"})),
            )
                .into_response();
        }
    };
    if !exists {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Media not found"})),
        )
            .into_response();
    }

    let candidates: Vec<(&str, Option<String>)> = vec![
        ("imdb", body.imdb_id),
        ("tmdb", body.tmdb_id),
        ("tvdb", body.tvdb_id),
        ("mal", body.mal_id),
        ("trakt", body.trakt_id),
    ];

    let mut linked_providers: Vec<String> = Vec::new();
    let mut failed_providers: Vec<String> = Vec::new();

    for (provider, id_opt) in candidates {
        let Some(external_id) = id_opt else { continue };

        let result = sqlx::query(
            "INSERT INTO media_external_id (media_id, provider, external_id)
             VALUES ($1, $2, $3)
             ON CONFLICT (provider, external_id) DO UPDATE SET external_id = EXCLUDED.external_id",
        )
        .bind(media_id)
        .bind(provider)
        .bind(&external_id)
        .execute(&state.pool)
        .await;

        match result {
            Ok(_) => linked_providers.push(provider.to_string()),
            Err(e) => {
                tracing::warn!("link_multiple_external_ids: failed provider {provider}: {e}");
                failed_providers.push(provider.to_string());
            }
        }
    }

    (
        StatusCode::OK,
        Json(json!({
            "status": "success",
            "media_id": media_id,
            "linked_providers": linked_providers,
            "failed_providers": failed_providers,
        })),
    )
        .into_response()
}

/// GET /api/v1/metadata/{media_id}
#[allow(clippy::type_complexity)]
pub async fn get_media_metadata(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i32>,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }

    let row: Option<(i32, String, String, Option<i32>, Option<String>, bool, Option<DateTime<Utc>>, Option<DateTime<Utc>>)> =
        match sqlx::query_as(
            "SELECT m.id, m.title, m.type::text, m.year, m.description, m.is_blocked, m.blocked_at, m.last_scraped_at
             FROM media m WHERE m.id = $1",
        )
        .bind(media_id)
        .fetch_optional(&state.pool_ro)
        .await
        {
            Ok(v) => v,
            Err(e) => {
                tracing::error!("get_media_metadata: db error: {e}");
                return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"detail": "Database error"}))).into_response();
            }
        };

    let (id, title, media_type, year, description, is_blocked, blocked_at, last_scraped_at) =
        match row {
            Some(r) => r,
            None => {
                return (
                    StatusCode::NOT_FOUND,
                    Json(json!({"detail": "Media not found"})),
                )
                    .into_response()
            }
        };

    // Fetch external IDs
    let ext_rows: Vec<(String, String)> = match sqlx::query_as(
        "SELECT provider, external_id FROM media_external_id WHERE media_id = $1",
    )
    .bind(media_id)
    .fetch_all(&state.pool_ro)
    .await
    {
        Ok(v) => v,
        Err(e) => {
            tracing::warn!("get_media_metadata: failed to fetch external_ids: {e}");
            vec![]
        }
    };

    let external_ids: Vec<ExternalIdRow> = ext_rows
        .into_iter()
        .map(|(provider, external_id)| ExternalIdRow {
            provider,
            external_id,
        })
        .collect();

    let result = MediaMetadataRow {
        id,
        title,
        media_type,
        year,
        description,
        is_blocked,
        blocked_at,
        last_scraped_at,
        external_ids,
    };

    (StatusCode::OK, Json(result)).into_response()
}

/// POST /api/v1/metadata/search-external
pub async fn search_external_metadata(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    body: Option<Json<serde_json::Value>>,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }

    let body = body.map(|b| b.0).unwrap_or_default();
    let provider = body["provider"].as_str().unwrap_or("imdb");
    let media_type = body["media_type"].as_str().unwrap_or("movie");
    let title = match body["title"].as_str() {
        Some(t) if !t.is_empty() => t.to_string(),
        _ => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "title is required"})),
            )
                .into_response()
        }
    };

    let results = match provider {
        "tmdb" => {
            let api_key = match state.config.tmdb_api_key.clone() {
                Some(k) => k,
                None => return (
                    StatusCode::PRECONDITION_FAILED,
                    Json(json!({"code": "tmdb_key_required", "message": "TMDB API key not configured on server."}))
                ).into_response(),
            };
            tmdb_search_ext(&state.http, &api_key, media_type, &title).await
        }
        _ => cinemeta_search(&state.http, media_type, &title).await,
    };

    Json(json!({"results": results})).into_response()
}

/// POST /api/v1/metadata/{media_id}/migrate
pub async fn migrate_media_id(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i32>,
    body: Option<Json<serde_json::Value>>,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }

    let body = body.map(|b| b.0).unwrap_or_default();
    let new_external_id = match body["new_external_id"].as_str() {
        Some(id) if !id.is_empty() => id.to_string(),
        _ => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "new_external_id is required"})),
            )
                .into_response()
        }
    };

    // Check media exists
    let exists: bool = match sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM media WHERE id = $1)")
        .bind(media_id)
        .fetch_one(&state.pool_ro)
        .await
    {
        Ok(v) => v,
        Err(e) => {
            tracing::error!("migrate_media_id: db error: {e}");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "Database error"})),
            )
                .into_response();
        }
    };
    if !exists {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Media not found"})),
        )
            .into_response();
    }

    // Determine provider and external_id
    let (provider, ext_id) = if new_external_id.starts_with("tt") {
        ("imdb", new_external_id.clone())
    } else if new_external_id.starts_with("tmdb:") {
        (
            "tmdb",
            new_external_id.strip_prefix("tmdb:").unwrap().to_string(),
        )
    } else {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Invalid external ID format. Use 'tt1234567' for IMDb or 'tmdb:12345' for TMDB"})),
        ).into_response();
    };

    // Check for conflict
    let conflict: Option<i32> = sqlx::query_scalar(
        "SELECT media_id FROM media_external_id WHERE provider = $1 AND external_id = $2 AND media_id != $3 LIMIT 1"
    )
    .bind(provider)
    .bind(&ext_id)
    .bind(media_id)
    .fetch_optional(&state.pool_ro)
    .await
    .unwrap_or(None);

    if conflict.is_some() {
        return (
            StatusCode::CONFLICT,
            Json(json!({"detail": format!("External ID {new_external_id} is already in use by another media item")})),
        ).into_response();
    }

    // Upsert
    let result = sqlx::query(
        "INSERT INTO media_external_id (media_id, provider, external_id)
         VALUES ($1, $2, $3)
         ON CONFLICT (media_id, provider) DO UPDATE SET external_id = EXCLUDED.external_id",
    )
    .bind(media_id)
    .bind(provider)
    .bind(&ext_id)
    .execute(&state.pool)
    .await;

    match result {
        Ok(_) => Json(json!({
            "status": "success",
            "media_id": media_id,
            "provider": provider,
            "external_id": ext_id,
        }))
        .into_response(),
        Err(e) => {
            tracing::error!("migrate_media_id: upsert error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "Database error"})),
            )
                .into_response()
        }
    }
}

/// GET /api/v1/metadata/search
#[allow(clippy::type_complexity)]
pub async fn search_metadata(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<SearchMetadataQuery>,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }

    let page = params.page.unwrap_or(1).max(1);
    let page_size = params.page_size.unwrap_or(20).clamp(1, 100);
    let offset = (page - 1) * page_size;
    let q = &params.q;

    // Build query depending on whether type filter is provided
    let (rows, total): (Vec<(i32, String, String, Option<i32>, bool)>, i64) =
        if let Some(ref media_type) = params.media_type {
            let count: i64 = match sqlx::query_scalar(
                "SELECT COUNT(*) FROM media
                 WHERE title ILIKE '%' || $1 || '%'
                   AND type = upper($2)::mediatype",
            )
            .bind(q)
            .bind(media_type)
            .fetch_one(&state.pool_ro)
            .await
            {
                Ok(v) => v,
                Err(e) => {
                    tracing::error!("search_metadata: count error: {e}");
                    return (
                        StatusCode::INTERNAL_SERVER_ERROR,
                        Json(json!({"detail": "Database error"})),
                    )
                        .into_response();
                }
            };

            let rows: Vec<(i32, String, String, Option<i32>, bool)> = match sqlx::query_as(
                "SELECT id, title, type::text, year, is_blocked
                 FROM media
                 WHERE title ILIKE '%' || $1 || '%'
                   AND type = upper($2)::mediatype
                 ORDER BY
                   CASE WHEN LOWER(title) = LOWER($1) THEN 0
                        WHEN LOWER(title) LIKE LOWER($1) || '%' THEN 1
                        ELSE 2 END,
                   id DESC
                 LIMIT $3 OFFSET $4",
            )
            .bind(q)
            .bind(media_type)
            .bind(page_size)
            .bind(offset)
            .fetch_all(&state.pool_ro)
            .await
            {
                Ok(v) => v,
                Err(e) => {
                    tracing::error!("search_metadata: rows error: {e}");
                    return (
                        StatusCode::INTERNAL_SERVER_ERROR,
                        Json(json!({"detail": "Database error"})),
                    )
                        .into_response();
                }
            };

            (rows, count)
        } else {
            let count: i64 = match sqlx::query_scalar(
                "SELECT COUNT(*) FROM media WHERE title ILIKE '%' || $1 || '%'",
            )
            .bind(q)
            .fetch_one(&state.pool_ro)
            .await
            {
                Ok(v) => v,
                Err(e) => {
                    tracing::error!("search_metadata: count error: {e}");
                    return (
                        StatusCode::INTERNAL_SERVER_ERROR,
                        Json(json!({"detail": "Database error"})),
                    )
                        .into_response();
                }
            };

            let rows: Vec<(i32, String, String, Option<i32>, bool)> = match sqlx::query_as(
                "SELECT id, title, type::text, year, is_blocked
                 FROM media
                 WHERE title ILIKE '%' || $1 || '%'
                 ORDER BY
                   CASE WHEN LOWER(title) = LOWER($1) THEN 0
                        WHEN LOWER(title) LIKE LOWER($1) || '%' THEN 1
                        ELSE 2 END,
                   id DESC
                 LIMIT $2 OFFSET $3",
            )
            .bind(q)
            .bind(page_size)
            .bind(offset)
            .fetch_all(&state.pool_ro)
            .await
            {
                Ok(v) => v,
                Err(e) => {
                    tracing::error!("search_metadata: rows error: {e}");
                    return (
                        StatusCode::INTERNAL_SERVER_ERROR,
                        Json(json!({"detail": "Database error"})),
                    )
                        .into_response();
                }
            };

            (rows, count)
        };

    // Fetch external IDs for all result media IDs
    let media_ids: Vec<i32> = rows.iter().map(|r| r.0).collect();
    let ext_rows: Vec<(i32, String, String)> = if media_ids.is_empty() {
        vec![]
    } else {
        match sqlx::query_as(
            "SELECT media_id, provider, external_id FROM media_external_id WHERE media_id = ANY($1)",
        )
        .bind(&media_ids)
        .fetch_all(&state.pool_ro)
        .await
        {
            Ok(v) => v,
            Err(e) => {
                tracing::warn!("search_metadata: failed to fetch external_ids: {e}");
                vec![]
            }
        }
    };

    // Group external IDs by media_id
    let mut ext_map: std::collections::HashMap<i32, Vec<ExternalIdRow>> =
        std::collections::HashMap::new();
    for (mid, provider, external_id) in ext_rows {
        ext_map.entry(mid).or_default().push(ExternalIdRow {
            provider,
            external_id,
        });
    }

    let results: Vec<SearchResultItem> = rows
        .into_iter()
        .map(
            |(id, title, media_type, year, is_blocked)| SearchResultItem {
                external_ids: ext_map.remove(&id).unwrap_or_default(),
                id,
                title,
                media_type,
                year,
                is_blocked,
            },
        )
        .collect();

    let has_more = (page * page_size) < total;

    (
        StatusCode::OK,
        Json(json!({
            "results": results,
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_more": has_more,
        })),
    )
        .into_response()
}
