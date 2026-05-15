/// Moderator metadata management endpoints — native Rust implementation.
///
/// Routes (prefix /api/v1/moderator/metadata):
///   GET  /                          → moderator_list_metadata
///   GET  /{media_id}                → moderator_get_metadata
///   POST /search-external           → moderator_search_external_metadata   [501]
///   POST /{media_id}/fetch-external → moderator_fetch_external_metadata    [501]
///   POST /{media_id}/apply-external → moderator_apply_external_metadata    [501]
///   POST /{media_id}/migrate-id     → moderator_migrate_metadata_id
use std::sync::Arc;

use axum::{
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::Utc;
use hmac::{Hmac, KeyInit, Mac};
use serde::{Deserialize, Deserializer};
use serde_json::{json, Value};
use sha2::Sha256;

fn bool_opt_from_str<'de, D: Deserializer<'de>>(d: D) -> Result<Option<bool>, D::Error> {
    let s: Option<String> = Option::deserialize(d)?;
    Ok(s.map(|v| !matches!(v.to_lowercase().as_str(), "false" | "0" | "no" | "")))
}

use crate::state::AppState;

// ─── Auth helper ──────────────────────────────────────────────────────────────

fn validate_moderator_token(headers: &HeaderMap, secret_key: &str) -> Option<(i64, String)> {
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
    let data: Value = serde_json::from_slice(&decoded).ok()?;
    let exp = data["exp"].as_f64()?;
    if exp < Utc::now().timestamp() as f64 {
        return None;
    }
    if data["type"].as_str() != Some("access") {
        return None;
    }
    let user_id: i64 = data["sub"].as_str()?.parse().ok()?;
    let role = data["role"].as_str().unwrap_or("user").to_string();
    if role != "moderator" && role != "admin" {
        return None;
    }
    Some((user_id, role))
}

// ─── Query params ─────────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct ListMetadataQuery {
    pub page: Option<i64>,
    pub per_page: Option<i64>,
    pub media_type: Option<String>,
    pub search: Option<String>,
    #[serde(default, deserialize_with = "bool_opt_from_str")]
    pub has_streams: Option<bool>,
}

#[derive(Deserialize)]
pub struct MigrateIdBody {
    pub new_external_id: String,
}

type RelationMaps = (
    std::collections::HashMap<i32, Vec<String>>, // genres
    std::collections::HashMap<i32, Vec<String>>, // catalogs
    std::collections::HashMap<i32, String>,      // poster
    std::collections::HashMap<i32, String>,      // background
    std::collections::HashMap<i32, f64>,         // imdb_rating
    std::collections::HashMap<i32, f64>,         // tmdb_rating
    std::collections::HashMap<i32, Vec<String>>, // cast
    std::collections::HashMap<i32, Vec<String>>, // aka_titles
    std::collections::HashMap<i32, serde_json::Map<String, Value>>, // ext_ids
    std::collections::HashMap<i32, Vec<String>>, // parental
);

async fn fetch_relation_maps(state: &AppState, media_ids: &[i32]) -> RelationMaps {
    let (
        genre_rows,
        catalog_rows,
        image_rows,
        rating_rows,
        cast_rows,
        aka_rows,
        ext_id_rows,
        parental_rows,
    ) = tokio::join!(
        sqlx::query_as::<_, (i32, String)>(
            "SELECT mgl.media_id, g.name FROM genre g \
                 JOIN media_genre_link mgl ON mgl.genre_id = g.id \
                 WHERE mgl.media_id = ANY($1)"
        )
        .bind(media_ids)
        .fetch_all(&state.pool_ro),
        sqlx::query_as::<_, (i32, String)>(
            "SELECT mcl.media_id, c.name FROM catalog c \
                 JOIN media_catalog_link mcl ON mcl.catalog_id = c.id \
                 WHERE mcl.media_id = ANY($1)"
        )
        .bind(media_ids)
        .fetch_all(&state.pool_ro),
        sqlx::query_as::<_, (i32, String, String, bool)>(
            "SELECT media_id, image_type, url, is_primary FROM media_image \
                 WHERE media_id = ANY($1) AND is_primary = true"
        )
        .bind(media_ids)
        .fetch_all(&state.pool_ro),
        sqlx::query_as::<_, (i32, String, f64)>(
            "SELECT mr.media_id, rp.name, mr.rating FROM media_rating mr \
                 JOIN rating_provider rp ON rp.id = mr.rating_provider_id \
                 WHERE mr.media_id = ANY($1)"
        )
        .bind(media_ids)
        .fetch_all(&state.pool_ro),
        sqlx::query_as::<_, (i32, String, i32)>(
            "SELECT mc.media_id, p.name, mc.display_order FROM media_cast mc \
                 JOIN person p ON p.id = mc.person_id \
                 WHERE mc.media_id = ANY($1) \
                 ORDER BY mc.media_id, mc.display_order"
        )
        .bind(media_ids)
        .fetch_all(&state.pool_ro),
        sqlx::query_as::<_, (i32, String)>(
            "SELECT media_id, title FROM aka_title WHERE media_id = ANY($1)"
        )
        .bind(media_ids)
        .fetch_all(&state.pool_ro),
        sqlx::query_as::<_, (i32, String, String)>(
            "SELECT media_id, provider, external_id FROM media_external_id \
                 WHERE media_id = ANY($1)"
        )
        .bind(media_ids)
        .fetch_all(&state.pool_ro),
        sqlx::query_as::<_, (i32, String)>(
            "SELECT mpcl.media_id, pc.name FROM parental_certificate pc \
                 JOIN media_parental_certificate_link mpcl ON mpcl.parental_certificate_id = pc.id \
                 WHERE mpcl.media_id = ANY($1)"
        )
        .bind(media_ids)
        .fetch_all(&state.pool_ro),
    );

    let mut genres_map: std::collections::HashMap<i32, Vec<String>> = Default::default();
    for (mid, name) in genre_rows.unwrap_or_default() {
        genres_map.entry(mid).or_default().push(name);
    }
    let mut catalogs_map: std::collections::HashMap<i32, Vec<String>> = Default::default();
    for (mid, name) in catalog_rows.unwrap_or_default() {
        catalogs_map.entry(mid).or_default().push(name);
    }
    let mut poster_map: std::collections::HashMap<i32, String> = Default::default();
    let mut background_map: std::collections::HashMap<i32, String> = Default::default();
    for (mid, img_type, url, _is_primary) in image_rows.unwrap_or_default() {
        if img_type == "poster" {
            poster_map.entry(mid).or_insert(url);
        } else if img_type == "background" {
            background_map.entry(mid).or_insert(url);
        }
    }
    let mut imdb_rating_map: std::collections::HashMap<i32, f64> = Default::default();
    let mut tmdb_rating_map: std::collections::HashMap<i32, f64> = Default::default();
    for (mid, provider, rating) in rating_rows.unwrap_or_default() {
        if provider.to_lowercase() == "imdb" {
            imdb_rating_map.insert(mid, rating);
        } else if provider.to_lowercase() == "tmdb" {
            tmdb_rating_map.insert(mid, rating);
        }
    }
    let mut cast_map: std::collections::HashMap<i32, Vec<String>> = Default::default();
    for (mid, name, _order) in cast_rows.unwrap_or_default() {
        cast_map.entry(mid).or_default().push(name);
    }
    let mut aka_map: std::collections::HashMap<i32, Vec<String>> = Default::default();
    for (mid, title) in aka_rows.unwrap_or_default() {
        aka_map.entry(mid).or_default().push(title);
    }
    let mut ext_ids_map: std::collections::HashMap<i32, serde_json::Map<String, Value>> =
        Default::default();
    for (mid, provider, ext_id) in ext_id_rows.unwrap_or_default() {
        ext_ids_map
            .entry(mid)
            .or_default()
            .insert(provider, Value::String(ext_id));
    }
    let mut parental_map: std::collections::HashMap<i32, Vec<String>> = Default::default();
    for (mid, name) in parental_rows.unwrap_or_default() {
        parental_map.entry(mid).or_default().push(name);
    }

    (
        genres_map,
        catalogs_map,
        poster_map,
        background_map,
        imdb_rating_map,
        tmdb_rating_map,
        cast_map,
        aka_map,
        ext_ids_map,
        parental_map,
    )
}

#[allow(clippy::too_many_arguments)]
fn build_media_response(
    id: i32,
    media_type: &str,
    title: &str,
    year: Option<i32>,
    description: Option<&str>,
    runtime_minutes: Option<i32>,
    is_user_created: bool,
    is_blocked: bool,
    blocked_at: Option<chrono::DateTime<chrono::Utc>>,
    block_reason: Option<&str>,
    total_streams: i32,
    created_at: chrono::DateTime<chrono::Utc>,
    updated_at: Option<chrono::DateTime<chrono::Utc>>,
    last_stream_added: Option<chrono::DateTime<chrono::Utc>>,
    is_add_title_to_poster: bool,
    nudity_status: Option<&str>,
    maps: &RelationMaps,
) -> Value {
    let (
        genres_map,
        catalogs_map,
        poster_map,
        background_map,
        imdb_rating_map,
        tmdb_rating_map,
        cast_map,
        aka_map,
        ext_ids_map,
        parental_map,
    ) = maps;

    let external_ids = ext_ids_map
        .get(&id)
        .cloned()
        .map(Value::Object)
        .unwrap_or(Value::Null);

    json!({
        "id": id,
        "external_ids": external_ids,
        "type": media_type.to_lowercase(),
        "title": title,
        "year": year,
        "poster": poster_map.get(&id),
        "is_poster_working": true,
        "is_add_title_to_poster": is_add_title_to_poster,
        "background": background_map.get(&id),
        "description": description,
        "is_user_created": is_user_created,
        "is_blocked": is_blocked,
        "blocked_at": blocked_at,
        "block_reason": block_reason,
        "runtime": runtime_minutes.map(|r| r.to_string()),
        "website": null,
        "total_streams": total_streams,
        "created_at": created_at,
        "updated_at": updated_at,
        "last_stream_added": last_stream_added,
        "imdb_rating": imdb_rating_map.get(&id),
        "tmdb_rating": tmdb_rating_map.get(&id),
        "parent_guide_nudity_status": nudity_status,
        "end_date": null,
        "country": null,
        "tv_language": null,
        "logo": null,
        "genres": genres_map.get(&id).cloned().unwrap_or_default(),
        "catalogs": catalogs_map.get(&id).cloned().unwrap_or_default(),
        "stars": cast_map.get(&id).cloned().unwrap_or_default(),
        "parental_certificates": parental_map.get(&id).cloned().unwrap_or_default(),
        "aka_titles": aka_map.get(&id).cloned().unwrap_or_default(),
    })
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// GET /api/v1/moderator/metadata
pub async fn moderator_list_metadata(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<ListMetadataQuery>,
) -> impl IntoResponse {
    if validate_moderator_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response();
    }

    let page = params.page.unwrap_or(1).max(1);
    let per_page = params.per_page.unwrap_or(20).clamp(1, 100);
    let offset = (page - 1) * per_page;

    // Build WHERE clause
    let mut conditions: Vec<String> = Vec::new();
    if let Some(ref mt) = params.media_type {
        let mt_upper = mt.to_uppercase();
        conditions.push(format!("m.type = '{mt_upper}'::mediatype"));
    }
    if let Some(hs) = params.has_streams {
        if hs {
            conditions.push("m.total_streams > 0".to_string());
        } else {
            conditions.push("m.total_streams = 0".to_string());
        }
    }
    if let Some(ref s) = params.search {
        let escaped = s.replace('\'', "''");
        conditions.push(format!(
            "(m.title ILIKE '%{escaped}%' OR EXISTS (\
              SELECT 1 FROM media_external_id ei WHERE ei.media_id = m.id AND LOWER(ei.external_id) = LOWER('{escaped}')))"
        ));
    }

    let where_clause = if conditions.is_empty() {
        String::new()
    } else {
        format!("WHERE {}", conditions.join(" AND "))
    };

    // Count
    let count_sql = format!("SELECT COUNT(*) FROM media m {where_clause}");
    let total: i64 = sqlx::query_scalar(&count_sql)
        .fetch_one(&state.pool_ro)
        .await
        .unwrap_or(0);

    // Fetch page
    type MediaRow = (
        i32,                                   // id
        String,                                // type
        String,                                // title
        Option<i32>,                           // year
        Option<String>,                        // description
        Option<i32>,                           // runtime_minutes
        bool,                                  // is_user_created
        bool,                                  // is_blocked
        Option<chrono::DateTime<chrono::Utc>>, // blocked_at
        Option<String>,                        // block_reason
        i32,                                   // total_streams
        chrono::DateTime<chrono::Utc>,         // created_at
        Option<chrono::DateTime<chrono::Utc>>, // updated_at
        Option<chrono::DateTime<chrono::Utc>>, // last_stream_added
        bool,                                  // is_add_title_to_poster
        Option<String>,                        // nudity_status
    );

    let list_sql = format!(
        "SELECT m.id, m.type::text, m.title, m.year, m.description, m.runtime_minutes, \
                m.is_user_created, m.is_blocked, m.blocked_at, m.block_reason, m.total_streams, \
                m.created_at, m.updated_at, m.last_stream_added, m.is_add_title_to_poster, \
                m.nudity_status::text \
         FROM media m {where_clause} \
         ORDER BY m.created_at DESC \
         LIMIT {per_page} OFFSET {offset}"
    );

    let rows: Vec<MediaRow> = sqlx::query_as(&list_sql)
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default();

    let media_ids: Vec<i32> = rows.iter().map(|r| r.0).collect();
    let maps = fetch_relation_maps(&state, &media_ids).await;

    let items: Vec<Value> = rows
        .iter()
        .map(|r| {
            build_media_response(
                r.0,
                &r.1,
                &r.2,
                r.3,
                r.4.as_deref(),
                r.5,
                r.6,
                r.7,
                r.8,
                r.9.as_deref(),
                r.10,
                r.11,
                r.12,
                r.13,
                r.14,
                r.15.as_deref(),
                &maps,
            )
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
    }))
    .into_response()
}

/// GET /api/v1/moderator/metadata/{media_id}
pub async fn moderator_get_metadata(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i32>,
) -> impl IntoResponse {
    if validate_moderator_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response();
    }

    type MediaRow = (
        i32,
        String,
        String,
        Option<i32>,
        Option<String>,
        Option<i32>,
        bool,
        bool,
        Option<chrono::DateTime<chrono::Utc>>,
        Option<String>,
        i32,
        chrono::DateTime<chrono::Utc>,
        Option<chrono::DateTime<chrono::Utc>>,
        Option<chrono::DateTime<chrono::Utc>>,
        bool,
        Option<String>,
    );

    let row: Option<MediaRow> = sqlx::query_as(
        "SELECT id, type::text, title, year, description, runtime_minutes, \
                is_user_created, is_blocked, blocked_at, block_reason, total_streams, \
                created_at, updated_at, last_stream_added, is_add_title_to_poster, \
                nudity_status::text \
         FROM media WHERE id = $1",
    )
    .bind(media_id)
    .fetch_optional(&state.pool_ro)
    .await
    .unwrap_or(None);

    let r = match row {
        Some(r) => r,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Metadata not found"})),
            )
                .into_response();
        }
    };

    let ids = vec![media_id];
    let maps = fetch_relation_maps(&state, &ids).await;

    let resp = build_media_response(
        r.0,
        &r.1,
        &r.2,
        r.3,
        r.4.as_deref(),
        r.5,
        r.6,
        r.7,
        r.8,
        r.9.as_deref(),
        r.10,
        r.11,
        r.12,
        r.13,
        r.14,
        r.15.as_deref(),
        &maps,
    );

    Json(resp).into_response()
}

// ─── External metadata helpers ────────────────────────────────────────────────

#[derive(serde::Deserialize)]
struct ExternalMetaBody {
    provider: Option<String>,
    external_id: Option<String>,
    title: Option<String>,
    year: Option<i32>,
    #[serde(rename = "media_type")]
    media_type: Option<String>,
}

/// Resolve the TMDB server key (no user lookup — moderator context uses server key directly).
fn get_server_tmdb_key(state: &crate::state::AppState) -> Option<String> {
    state.config.tmdb_api_key.clone()
}

fn cinemeta_type(media_type: &str) -> &str {
    if media_type.contains("movie") {
        "movie"
    } else {
        "series"
    }
}

async fn search_cinemeta(http: &reqwest::Client, media_type: &str, title: &str) -> Vec<Value> {
    let encoded = urlencoding::encode(title);
    let url = format!(
        "https://v3-cinemeta.strem.io/catalog/{}/top/search={}.json",
        cinemeta_type(media_type),
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

async fn search_tmdb(
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

async fn fetch_cinemeta_meta(
    http: &reqwest::Client,
    media_type: &str,
    external_id: &str,
) -> Option<Value> {
    let url = format!(
        "https://v3-cinemeta.strem.io/meta/{}/{}.json",
        cinemeta_type(media_type),
        external_id
    );
    let resp = http.get(&url).send().await.ok()?;
    let data: Value = resp.json().await.ok()?;
    let m = data.get("meta")?;
    let year_val = m["year"]
        .as_str()
        .and_then(|y| y.split('-').next()?.parse::<i32>().ok())
        .or_else(|| m["year"].as_i64().map(|y| y as i32));
    let genres: Vec<String> = m["genres"]
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();
    Some(json!({
        "provider": "imdb",
        "external_id": m["id"],
        "title": m["name"],
        "year": year_val,
        "description": m["description"],
        "poster": m["poster"],
        "genres": genres,
    }))
}

async fn fetch_tmdb_meta(
    http: &reqwest::Client,
    api_key: &str,
    media_type: &str,
    external_id: &str,
) -> Option<Value> {
    let tmdb_type = if media_type.contains("movie") {
        "movie"
    } else {
        "tv"
    };
    let url = format!("https://api.themoviedb.org/3/{tmdb_type}/{external_id}?api_key={api_key}");
    let resp = http.get(&url).send().await.ok()?;
    if !resp.status().is_success() {
        return None;
    }
    let item: Value = resp.json().await.ok()?;
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
    let genres: Vec<String> = item["genres"]
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(|v| v["name"].as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();
    Some(json!({
        "provider": "tmdb",
        "external_id": external_id,
        "title": title_str,
        "year": year,
        "description": item["overview"].as_str(),
        "poster": poster,
        "genres": genres,
    }))
}

// ─── Moderator external metadata handlers ─────────────────────────────────────

/// POST /api/v1/moderator/metadata/search-external
pub async fn moderator_search_external_metadata(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    body: Option<Json<Value>>,
) -> impl IntoResponse {
    if validate_moderator_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response();
    }

    let body = body.map(|b| b.0).unwrap_or_default();
    let params: ExternalMetaBody = match serde_json::from_value(body) {
        Ok(p) => p,
        Err(_) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Invalid request body"})),
            )
                .into_response();
        }
    };

    let provider = params.provider.as_deref().unwrap_or("imdb");
    let media_type = params.media_type.as_deref().unwrap_or("movie");
    let title = match params.title {
        Some(ref t) if !t.is_empty() => t.clone(),
        _ => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "title is required"})),
            )
                .into_response()
        }
    };

    let mut results = match provider {
        "tmdb" => {
            let api_key = match get_server_tmdb_key(&state) {
                Some(k) => k,
                None => return (
                    StatusCode::PRECONDITION_FAILED,
                    Json(json!({"code": "tmdb_key_required", "message": "TMDB API key not configured on server."}))
                ).into_response(),
            };
            search_tmdb(&state.http, &api_key, media_type, &title).await
        }
        _ => search_cinemeta(&state.http, media_type, &title).await,
    };

    if let Some(filter_year) = params.year {
        results.retain(|r| match r.get("year").and_then(|v| v.as_i64()) {
            Some(ry) => (ry - filter_year as i64).abs() <= 1,
            None => true,
        });
    }

    Json(json!({"results": results})).into_response()
}

/// POST /api/v1/moderator/metadata/{media_id}/fetch-external
pub async fn moderator_fetch_external_metadata(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i32>,
    body: Option<Json<Value>>,
) -> impl IntoResponse {
    if validate_moderator_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response();
    }

    // Check media exists
    let row: Option<(String,)> = sqlx::query_as("SELECT type::text FROM media WHERE id = $1")
        .bind(media_id)
        .fetch_optional(&state.pool_ro)
        .await
        .unwrap_or(None);
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

    let body = body.map(|b| b.0).unwrap_or_default();
    let params: ExternalMetaBody = match serde_json::from_value(body) {
        Ok(p) => p,
        Err(_) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Invalid request body"})),
            )
                .into_response();
        }
    };

    let provider = params.provider.as_deref().unwrap_or("imdb");
    let external_id = match params.external_id {
        Some(ref id) if !id.is_empty() => id.clone(),
        _ => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "external_id is required"})),
            )
                .into_response()
        }
    };

    let preview = match provider {
        "tmdb" => {
            let api_key = match get_server_tmdb_key(&state) {
                Some(k) => k,
                None => return (
                    StatusCode::PRECONDITION_FAILED,
                    Json(json!({"code": "tmdb_key_required", "message": "TMDB API key not configured on server."}))
                ).into_response(),
            };
            fetch_tmdb_meta(&state.http, &api_key, &db_media_type, &external_id).await
        }
        _ => fetch_cinemeta_meta(&state.http, &db_media_type, &external_id).await,
    };

    match preview {
        Some(p) => Json(p).into_response(),
        None => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "External metadata not found"})),
        )
            .into_response(),
    }
}

/// POST /api/v1/moderator/metadata/{media_id}/apply-external
pub async fn moderator_apply_external_metadata(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i32>,
    body: Option<Json<Value>>,
) -> impl IntoResponse {
    if validate_moderator_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response();
    }

    // Check media exists
    let row: Option<(String,)> = sqlx::query_as("SELECT type::text FROM media WHERE id = $1")
        .bind(media_id)
        .fetch_optional(&state.pool_ro)
        .await
        .unwrap_or(None);
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

    let body = body.map(|b| b.0).unwrap_or_default();
    let params: ExternalMetaBody = match serde_json::from_value(body) {
        Ok(p) => p,
        Err(_) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Invalid request body"})),
            )
                .into_response();
        }
    };

    let provider = params.provider.as_deref().unwrap_or("imdb");
    let external_id = match params.external_id {
        Some(ref id) if !id.is_empty() => id.clone(),
        _ => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "external_id is required"})),
            )
                .into_response()
        }
    };

    let preview = match provider {
        "tmdb" => {
            let api_key = match get_server_tmdb_key(&state) {
                Some(k) => k,
                None => return (
                    StatusCode::PRECONDITION_FAILED,
                    Json(json!({"code": "tmdb_key_required", "message": "TMDB API key not configured on server."}))
                ).into_response(),
            };
            fetch_tmdb_meta(&state.http, &api_key, &db_media_type, &external_id).await
        }
        _ => fetch_cinemeta_meta(&state.http, &db_media_type, &external_id).await,
    };

    let meta = match preview {
        Some(p) => p,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "External metadata not found"})),
            )
                .into_response()
        }
    };

    // Update media fields
    let new_title = meta["title"].as_str().map(str::to_string);
    let new_year = meta["year"].as_i64().map(|y| y as i32);
    let new_desc = meta["description"].as_str().map(str::to_string);

    let mut updated_fields: Vec<&str> = Vec::new();
    if new_title.is_some() {
        updated_fields.push("title");
    }
    if new_year.is_some() {
        updated_fields.push("year");
    }
    if new_desc.is_some() {
        updated_fields.push("description");
    }

    if new_title.is_some() || new_year.is_some() || new_desc.is_some() {
        let _ = sqlx::query(
            "UPDATE media SET
               title = COALESCE($2, title),
               year = COALESCE($3, year),
               description = COALESCE($4, description),
               updated_at = NOW()
             WHERE id = $1",
        )
        .bind(media_id)
        .bind(&new_title)
        .bind(new_year)
        .bind(&new_desc)
        .execute(&state.pool)
        .await;
    }

    // Upsert external ID
    let _ = sqlx::query(
        "INSERT INTO media_external_id (media_id, provider, external_id)
         VALUES ($1, $2, $3)
         ON CONFLICT (media_id, provider) DO UPDATE SET external_id = EXCLUDED.external_id",
    )
    .bind(media_id)
    .bind(provider)
    .bind(&external_id)
    .execute(&state.pool)
    .await;

    Json(json!({
        "status": "success",
        "media_id": media_id,
        "updated_fields": updated_fields,
    }))
    .into_response()
}

/// POST /api/v1/moderator/metadata/{media_id}/migrate-id
pub async fn moderator_migrate_metadata_id(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i32>,
    Json(body): Json<MigrateIdBody>,
) -> impl IntoResponse {
    if validate_moderator_token(&headers, &state.config.secret_key_raw).is_none() {
        return (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response();
    }

    // Verify media exists
    let exists: Option<i32> = sqlx::query_scalar("SELECT id FROM media WHERE id = $1")
        .bind(media_id)
        .fetch_optional(&state.pool_ro)
        .await
        .unwrap_or(None);

    if exists.is_none() {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Metadata not found"})),
        )
            .into_response();
    }

    let new_id = body.new_external_id.trim().to_string();
    let (provider, provider_id) = if new_id.starts_with("tt") {
        ("imdb", new_id.clone())
    } else if new_id.starts_with("tmdb:") {
        ("tmdb", new_id.strip_prefix("tmdb:").unwrap().to_string())
    } else {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Invalid external ID format. Use 'tt1234567' for IMDb or 'tmdb:12345' for TMDB"})),
        )
            .into_response();
    };

    // Check if the new ID is already in use by another media
    let conflict: Option<i32> = sqlx::query_scalar(
        "SELECT media_id FROM media_external_id \
         WHERE provider = $1 AND external_id = $2 AND media_id != $3 LIMIT 1",
    )
    .bind(provider)
    .bind(&provider_id)
    .bind(media_id)
    .fetch_optional(&state.pool_ro)
    .await
    .unwrap_or(None);

    if conflict.is_some() {
        return (
            StatusCode::CONFLICT,
            Json(json!({"detail": format!("External ID {new_id} is already in use by another media item")})),
        )
            .into_response();
    }

    // Upsert external ID
    let result = sqlx::query(
        "INSERT INTO media_external_id (media_id, provider, external_id) \
         VALUES ($1, $2, $3) \
         ON CONFLICT (media_id, provider) DO UPDATE SET external_id = EXCLUDED.external_id",
    )
    .bind(media_id)
    .bind(provider)
    .bind(&provider_id)
    .execute(&state.pool)
    .await;

    if let Err(e) = result {
        tracing::error!("moderator_migrate_metadata_id: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    // Return updated metadata
    let ids = vec![media_id];
    let maps = fetch_relation_maps(&state, &ids).await;

    type MediaRow = (
        i32,
        String,
        String,
        Option<i32>,
        Option<String>,
        Option<i32>,
        bool,
        bool,
        Option<chrono::DateTime<chrono::Utc>>,
        Option<String>,
        i32,
        chrono::DateTime<chrono::Utc>,
        Option<chrono::DateTime<chrono::Utc>>,
        Option<chrono::DateTime<chrono::Utc>>,
        bool,
        Option<String>,
    );

    let row: Option<MediaRow> = sqlx::query_as(
        "SELECT id, type::text, title, year, description, runtime_minutes, \
                is_user_created, is_blocked, blocked_at, block_reason, total_streams, \
                created_at, updated_at, last_stream_added, is_add_title_to_poster, \
                nudity_status::text \
         FROM media WHERE id = $1",
    )
    .bind(media_id)
    .fetch_optional(&state.pool_ro)
    .await
    .unwrap_or(None);

    match row {
        Some(r) => {
            let resp = build_media_response(
                r.0,
                &r.1,
                &r.2,
                r.3,
                r.4.as_deref(),
                r.5,
                r.6,
                r.7,
                r.8,
                r.9.as_deref(),
                r.10,
                r.11,
                r.12,
                r.13,
                r.14,
                r.15.as_deref(),
                &maps,
            );
            Json(resp).into_response()
        }
        None => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    }
}
