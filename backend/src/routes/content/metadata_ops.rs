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
use serde_json::json;
use sha2::Sha256;

use crate::{db::MediaType, db::UserId, state::AppState};

use super::import_helpers;

// ─── Auth ─────────────────────────────────────────────────────────────────────

fn validate_token(headers: &HeaderMap, secret_key: &str) -> Option<UserId> {
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
    data["sub"].as_str()?.parse::<i32>().ok().map(UserId)
}

// ─── Request / response types ─────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct LinkExternalIdBody {
    pub provider: String,
    pub external_id: String,
    #[serde(rename = "type")]
    pub media_type: Option<String>,
    #[serde(default = "default_true")]
    pub fetch_metadata: bool,
}

fn default_true() -> bool {
    true
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

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// POST /api/v1/metadata/{media_id}/refresh
pub async fn refresh_metadata(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i32>,
    _req: Request,
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

    // Check media exists
    let row: Option<(crate::db::MediaType,)> =
        match sqlx::query_as("SELECT type FROM media WHERE id = $1")
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
    let db_media_type = media_row.0.as_wire();

    // Get external IDs
    let ext_rows: Vec<(String, String)> =
        sqlx::query_as("SELECT provider, external_id FROM media_external_id WHERE media_id = $1")
            .bind(media_id)
            .fetch_all(&state.pool_ro)
            .await
            .unwrap_or_default();

    // Find IMDB ID
    let _imdb_id = ext_rows
        .iter()
        .find(|(p, _)| p == "imdb")
        .map(|(_, id)| id.clone());

    let meta_type = if db_media_type == "series" {
        "series"
    } else {
        "movie"
    };
    let (refreshed_providers, message) = {
        let keys = crate::scrapers::metadata::resolve_metadata_keys(
            &state.pool_ro,
            Some(user_id),
            crate::scrapers::metadata::ResolvedMetadataKeys::server_keys_from_config(&state.config),
        )
        .await;
        crate::scrapers::metadata::refresh_media_from_providers(
            &state.pool,
            &state.http,
            media_id,
            meta_type,
            keys.fetch_ctx(
                state.config.trakt_client_id.as_deref(),
                state.config.trakt_client_secret.as_deref(),
                state.config.imdb_cinemeta_fallback_enabled,
            ),
            None,
        )
        .await
    };

    Json(json!({
        "status": "success",
        "media_id": media_id,
        "message": message,
        "refreshed_providers": refreshed_providers,
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

    let mut external_id = body.external_id.trim().to_string();
    let provider = body.provider.to_lowercase();
    if provider == "imdb" && !external_id.starts_with("tt") {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "IMDb ID must start with 'tt'"})),
        )
            .into_response();
    }
    if matches!(
        provider.as_str(),
        "tmdb" | "tvdb" | "mal" | "kitsu" | "anilist"
    ) {
        if external_id.contains(':') {
            external_id = external_id
                .rsplit_once(':')
                .map(|(_, id)| id.to_string())
                .unwrap_or(external_id);
        }
        if !external_id.chars().all(|c| c.is_ascii_digit()) {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": format!("{} ID must be numeric", provider.to_uppercase())})),
            )
                .into_response();
        }
    }

    let result = sqlx::query(
        "INSERT INTO media_external_id (media_id, provider, external_id)
         VALUES ($1, $2, $3)
         ON CONFLICT (provider, external_id) DO UPDATE SET media_id = EXCLUDED.media_id",
    )
    .bind(media_id)
    .bind(&provider)
    .bind(&external_id)
    .execute(&state.pool)
    .await;

    match result {
        Ok(_) => {}
        Err(e) => {
            tracing::error!("link_external_id: upsert error: {e}");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"detail": "Database error"})),
            )
                .into_response();
        }
    }

    let mut metadata_updated = false;
    if body.fetch_metadata {
        let meta_type = body.media_type.as_deref().unwrap_or("movie");
        let is_series = meta_type == "series";
        let keys = crate::scrapers::metadata::resolve_metadata_keys(
            &state.pool_ro,
            Some(user_id),
            crate::scrapers::metadata::ResolvedMetadataKeys::server_keys_from_config(&state.config),
        )
        .await;
        let ctx = keys.fetch_ctx(
            state.config.trakt_client_id.as_deref(),
            state.config.trakt_client_secret.as_deref(),
            state.config.imdb_cinemeta_fallback_enabled,
        );
        if let Some(meta) = crate::scrapers::metadata::fetch_normalized(
            &state.http,
            &ctx,
            &provider,
            &external_id,
            is_series,
        )
        .await
        {
            import_helpers::apply_fetched_metadata_to_media(&state.pool, media_id, &meta).await;
            metadata_updated = true;
        }
    }

    (
        StatusCode::OK,
        Json(json!({
            "status": "success",
            "message": "External ID linked",
            "media_id": media_id,
            "provider": provider,
            "external_id": external_id,
            "metadata_updated": metadata_updated,
        })),
    )
        .into_response()
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
            "SELECT m.id, m.title, m.type, m.year, m.description, m.is_blocked, m.blocked_at, m.last_scraped_at
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

    // Hard-block media whose title matches the global keyword filter.
    {
        let kf = state.keyword_filters.read().unwrap();
        if kf.matches_blocked_keyword(&title) {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Media not found"})),
            )
                .into_response();
        }
    }

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

/// POST /api/v1/metadata/search/matches
pub async fn search_media_matches_metadata(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    body: Option<Json<serde_json::Value>>,
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

    let body = body.map(|b| b.0).unwrap_or_default();
    let title = body["title"]
        .as_str()
        .map(str::trim)
        .filter(|t| !t.is_empty())
        .map(str::to_string);
    let external_id = body["external_id"]
        .as_str()
        .map(str::trim)
        .filter(|t| !t.is_empty())
        .map(str::to_string);
    let year = body["year"].as_i64().map(|y| y as i32);
    let media_type = body["media_type"].as_str().unwrap_or("movie");
    let limit = body["limit"].as_u64().unwrap_or(10) as usize;
    let include_user_content = body["include_user_content"].as_bool().unwrap_or(true);
    let include_official = body["include_official"].as_bool().unwrap_or(true);
    let include_catalog = body["include_catalog"].as_bool().unwrap_or(true);
    let include_external = body["include_external"].as_bool().unwrap_or(true);

    if title.is_none() && external_id.is_none() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "title or external_id is required"})),
        )
            .into_response();
    }

    let keys = crate::scrapers::metadata::resolve_metadata_keys(
        &state.pool_ro,
        Some(user_id),
        crate::scrapers::metadata::ResolvedMetadataKeys::server_keys_from_config(&state.config),
    )
    .await;

    let results = crate::scrapers::metadata::search_media_matches(
        &state.http,
        &state.pool_ro,
        crate::scrapers::metadata::MediaMatchSearchOptions {
            title: title.as_deref(),
            year,
            external_id: external_id.as_deref(),
            media_type,
            limit,
            user_id: Some(user_id),
            include_user_content,
            include_official,
            include_catalog,
            include_external,
            tmdb_api_key: keys.tmdb.as_deref(),
            tvdb_api_key: keys.tvdb.as_deref(),
            cinemeta_fallback_enabled: state.config.imdb_cinemeta_fallback_enabled,
        },
    )
    .await;

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

    // Read keyword filter once (clone out to drop the lock before awaits).
    let kf = { state.keyword_filters.read().unwrap().clone() };

    let (rows, total): (Vec<(i32, String, String, Option<i32>, bool)>, i64) =
        if let Some(ref media_type) = params.media_type {
            let kf_frag = kf.keyword_title_block_fragment();
            let count_sql = format!(
                "SELECT COUNT(*) FROM media m \
                 WHERE m.title ILIKE '%' || $1 || '%' \
                   AND m.type = $2\
                 {kf_frag}"
            );
            let mt_val =
                MediaType::from_wire(&media_type.to_ascii_lowercase()).unwrap_or(MediaType::Movie);
            let count_q = sqlx::query_scalar::<_, i64>(&count_sql).bind(q).bind(mt_val);
            let count: i64 = match count_q.fetch_one(&state.pool_ro).await {
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

            let rows_sql = format!(
                "SELECT m.id, m.title, m.type::text, m.year, m.is_blocked \
                 FROM media m \
                 WHERE m.title ILIKE '%' || $1 || '%' \
                   AND m.type = $2\
                 {kf_frag} \
                 ORDER BY \
                   CASE WHEN LOWER(m.title) = LOWER($1) THEN 0 \
                        WHEN LOWER(m.title) LIKE LOWER($1) || '%' THEN 1 \
                        ELSE 2 END, \
                   m.id DESC \
                 LIMIT $3 OFFSET $4"
            );
            let rows_q = sqlx::query_as::<_, (i32, String, String, Option<i32>, bool)>(
                &rows_sql,
            )
            .bind(q)
            .bind(mt_val)
            .bind(page_size)
            .bind(offset);
            let rows: Vec<(i32, String, String, Option<i32>, bool)> =
                match rows_q.fetch_all(&state.pool_ro).await {
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
            let kf_frag = kf.keyword_title_block_fragment();
            let count_sql = format!(
                "SELECT COUNT(*) FROM media m \
                 WHERE m.title ILIKE '%' || $1 || '%'\
                 {kf_frag}"
            );
            let count_q = sqlx::query_scalar::<_, i64>(&count_sql).bind(q);
            let count: i64 = match count_q.fetch_one(&state.pool_ro).await {
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

            let rows_sql = format!(
                "SELECT m.id, m.title, m.type::text, m.year, m.is_blocked \
                 FROM media m \
                 WHERE m.title ILIKE '%' || $1 || '%'\
                 {kf_frag} \
                 ORDER BY \
                   CASE WHEN LOWER(m.title) = LOWER($1) THEN 0 \
                        WHEN LOWER(m.title) LIKE LOWER($1) || '%' THEN 1 \
                        ELSE 2 END, \
                   m.id DESC \
                 LIMIT $2 OFFSET $3"
            );
            let rows_q = sqlx::query_as::<_, (i32, String, String, Option<i32>, bool)>(
                &rows_sql,
            )
            .bind(q)
            .bind(page_size)
            .bind(offset);
            let rows: Vec<(i32, String, String, Option<i32>, bool)> =
                match rows_q.fetch_all(&state.pool_ro).await {
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
