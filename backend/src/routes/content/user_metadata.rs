/// User Metadata endpoints — user-created/owned media records.
///
/// Routes (prefix /api/v1/metadata/user):
///   POST   /                                    → create_user_metadata
///   GET    /                                    → list_user_metadata
///   GET    /search/all                          → search_all_metadata
///   POST   /import/preview                      → preview_import_from_external  (stub)
///   POST   /import                              → import_from_external          (stub)
///   GET    /{media_id}                          → get_user_metadata
///   PUT    /{media_id}                          → update_user_metadata
///   DELETE /{media_id}                          → delete_user_metadata
///   POST   /{media_id}/seasons                  → add_season_to_series
///   POST   /{media_id}/episodes                 → add_episodes_to_series
///   PUT    /{media_id}/episodes/{episode_id}    → update_episode
///   DELETE /{media_id}/episodes/{episode_id}    → delete_episode
///   DELETE /{media_id}/episodes/{episode_id}/admin → delete_episode_admin
///   DELETE /{media_id}/seasons/{season_number}  → delete_season
///   DELETE /{media_id}/seasons/{season_number}/admin → delete_season_admin
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
use serde_json::json;
use sha2::Sha256;

use crate::{
    db::{EpisodeId, MediaId, MediaType, NudityStatus, SeasonId, SeriesId, UserRole},
    state::AppState,
};

// ─── Auth helper ─────────────────────────────────────────────────────────────

// users.id is INT4 — return i32, not i64.
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

async fn require_mod_or_admin(pool: &sqlx::PgPool, user_id: i32) -> Result<(), Response> {
    let role: Option<UserRole> = sqlx::query_scalar("SELECT role FROM users WHERE id = $1")
        .bind(user_id)
        .fetch_optional(pool)
        .await
        .unwrap_or(None);
    match role {
        Some(UserRole::Moderator) | Some(UserRole::Admin) => Ok(()),
        _ => Err((
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "Moderator or admin role required"})),
        )
            .into_response()),
    }
}

// ─── Request / Response structs ───────────────────────────────────────────────

#[derive(Deserialize)]
pub struct EpisodeCreate {
    pub episode_number: i32,
    pub title: String,
    pub overview: Option<String>,
    pub air_date: Option<String>,
    pub runtime_minutes: Option<i32>,
}

#[derive(Deserialize)]
pub struct SeasonCreate {
    pub season_number: i32,
    pub name: Option<String>,
    pub overview: Option<String>,
    pub air_date: Option<String>,
    #[serde(default)]
    pub episodes: Vec<EpisodeCreate>,
}

#[derive(Deserialize)]
pub struct UserMediaCreate {
    #[serde(rename = "type", alias = "media_type")]
    pub media_type: String,
    pub title: String,
    pub year: Option<i32>,
    pub description: Option<String>,
    pub poster_url: Option<String>,
    pub background_url: Option<String>,
    pub genres: Option<Vec<String>>,
    pub catalogs: Option<Vec<String>>,
    pub external_ids: Option<std::collections::HashMap<String, String>>,
    #[serde(default = "default_true")]
    pub is_public: bool,
    pub runtime_minutes: Option<i32>,
    pub seasons: Option<Vec<SeasonCreate>>,
}

fn default_true() -> bool {
    true
}

#[derive(Deserialize)]
pub struct UserMediaUpdate {
    pub title: Option<String>,
    pub original_title: Option<String>,
    pub year: Option<i32>,
    pub description: Option<String>,
    pub tagline: Option<String>,
    pub poster_url: Option<String>,
    pub background_url: Option<String>,
    pub logo_url: Option<String>,
    pub genres: Option<Vec<String>>,
    pub catalogs: Option<Vec<String>>,
    pub is_public: Option<bool>,
    pub runtime_minutes: Option<i32>,
    pub release_date: Option<String>,
    pub status: Option<String>,
    pub website: Option<String>,
    pub original_language: Option<String>,
    pub nudity_status: Option<String>,
    pub aka_titles: Option<Vec<String>>,
    pub cast: Option<Vec<String>>,
    pub directors: Option<Vec<String>>,
    pub writers: Option<Vec<String>>,
    pub parental_certificate: Option<String>,
    pub external_ids: Option<std::collections::HashMap<String, String>>,
}

#[derive(Deserialize)]
pub struct SeasonAddRequest {
    pub season_number: i32,
    pub name: Option<String>,
    pub overview: Option<String>,
    pub air_date: Option<String>,
    #[serde(default)]
    pub episodes: Vec<EpisodeCreate>,
}

#[derive(Deserialize)]
pub struct EpisodeAddRequest {
    pub season_number: i32,
    pub episodes: Vec<EpisodeCreate>,
}

#[derive(Deserialize)]
pub struct EpisodeUpdateRequest {
    pub title: Option<String>,
    pub overview: Option<String>,
    pub air_date: Option<String>,
    pub runtime_minutes: Option<i32>,
}

#[derive(Deserialize)]
pub struct ListQuery {
    #[serde(default = "default_page")]
    pub page: i64,
    #[serde(default = "default_page_size")]
    pub per_page: i64,
    #[serde(rename = "type")]
    pub media_type: Option<String>,
    pub search: Option<String>,
}

fn default_page() -> i64 {
    1
}
fn default_page_size() -> i64 {
    20
}

#[derive(Deserialize)]
pub struct SearchQuery {
    pub query: String,
    #[serde(rename = "type")]
    pub media_type: Option<String>,
    #[serde(default = "default_search_limit")]
    pub limit: i64,
    #[serde(default = "default_true")]
    pub include_official: bool,
}

fn default_search_limit() -> i64 {
    20
}

#[derive(Deserialize)]
pub struct DeleteMediaQuery {
    #[serde(default)]
    pub force: bool,
}

#[derive(Deserialize)]
pub struct DeleteEpisodeAdminQuery {
    #[serde(default)]
    pub delete_stream_links: bool,
}

// ─── Helper: build a basic media JSON object from DB row ─────────────────────

#[derive(sqlx::FromRow)]
struct MediaBasicRow {
    id: MediaId,
    #[sqlx(rename = "type")]
    media_type: MediaType,
    title: String,
    original_title: Option<String>,
    year: Option<i32>,
    description: Option<String>,
    tagline: Option<String>,
    status: Option<String>,
    website: Option<String>,
    is_public: bool,
    is_user_created: bool,
    created_by_user_id: Option<i32>,
    total_streams: i32,
    created_at: DateTime<Utc>,
    updated_at: Option<DateTime<Utc>>,
    runtime_minutes: Option<i32>,
    release_date: Option<String>,
    original_language: Option<String>,
    nudity_status: NudityStatus,
}

#[allow(clippy::type_complexity)]
async fn media_row_to_json(
    pool: &sqlx::PgPool,
    media_id: i32,
    include_seasons: bool,
) -> serde_json::Value {
    let row = match sqlx::query_as::<_, MediaBasicRow>(
        r#"SELECT id, type, title, original_title, year, description, tagline,
                      status, website, is_public, is_user_created,
                      created_by_user_id, total_streams,
                      created_at, updated_at,
                      runtime_minutes, release_date::text,
                      original_language, nudity_status
               FROM media WHERE id = $1"#,
    )
    .bind(media_id)
    .fetch_optional(pool)
    .await
    {
        Ok(row) => row,
        Err(e) => {
            tracing::error!("media_row_to_json fetch media {media_id}: {e}");
            return json!(null);
        }
    };

    let row = match row {
        Some(r) => r,
        None => return json!(null),
    };

    // External IDs
    let ext_ids: Vec<(String, String)> =
        sqlx::query_as("SELECT provider, external_id FROM media_external_id WHERE media_id = $1")
            .bind(media_id)
            .fetch_all(pool)
            .await
            .unwrap_or_default();
    let mut ext_map = serde_json::Map::new();
    for (p, e) in &ext_ids {
        ext_map.insert(p.clone(), json!(e));
    }

    // Images
    let images: Vec<(String, String)> =
        sqlx::query_as("SELECT image_type, url FROM media_image WHERE media_id = $1 ORDER BY display_order ASC, id ASC")
            .bind(media_id)
            .fetch_all(pool)
            .await
            .unwrap_or_default();
    let mut poster_url: Option<String> = None;
    let mut background_url: Option<String> = None;
    let mut logo_url: Option<String> = None;
    for (img_type, url) in &images {
        match img_type.as_str() {
            "poster" if poster_url.is_none() => poster_url = Some(url.clone()),
            "background" | "backdrop" if background_url.is_none() => {
                background_url = Some(url.clone())
            }
            "logo" if logo_url.is_none() => logo_url = Some(url.clone()),
            _ => {}
        }
    }

    // Genres
    let genres: Vec<(String,)> = sqlx::query_as(
        "SELECT g.name FROM genre g JOIN media_genre_link gl ON gl.genre_id = g.id WHERE gl.media_id = $1",
    )
    .bind(media_id)
    .fetch_all(pool)
    .await
    .unwrap_or_default();

    // Catalogs
    let catalogs: Vec<(String,)> = sqlx::query_as(
        "SELECT c.name FROM catalog c JOIN media_catalog_link cl ON cl.catalog_id = c.id WHERE cl.media_id = $1",
    )
    .bind(media_id)
    .fetch_all(pool)
    .await
    .unwrap_or_default();

    // AKA titles
    let aka: Vec<(String,)> = sqlx::query_as("SELECT title FROM aka_title WHERE media_id = $1")
        .bind(media_id)
        .fetch_all(pool)
        .await
        .unwrap_or_default();

    // Cast
    let cast: Vec<(String,)> = sqlx::query_as(
        "SELECT p.name FROM person p JOIN media_cast mc ON mc.person_id = p.id WHERE mc.media_id = $1 ORDER BY mc.display_order ASC",
    )
    .bind(media_id)
    .fetch_all(pool)
    .await
    .unwrap_or_default();

    // Directors
    let directors: Vec<(String,)> = sqlx::query_as(
        "SELECT p.name FROM person p JOIN media_crew mc ON mc.person_id = p.id WHERE mc.media_id = $1 AND mc.department = 'Directing'",
    )
    .bind(media_id)
    .fetch_all(pool)
    .await
    .unwrap_or_default();

    // Writers
    let writers: Vec<(String,)> = sqlx::query_as(
        "SELECT p.name FROM person p JOIN media_crew mc ON mc.person_id = p.id WHERE mc.media_id = $1 AND mc.department = 'Writing'",
    )
    .bind(media_id)
    .fetch_all(pool)
    .await
    .unwrap_or_default();

    // Parental certificate
    let parental_cert: Option<String> = sqlx::query_scalar(
        "SELECT pc.name FROM parental_certificate pc JOIN media_parental_certificate_link mpcl ON mpcl.certificate_id = pc.id WHERE mpcl.media_id = $1 LIMIT 1",
    )
    .bind(media_id)
    .fetch_optional(pool)
    .await
    .unwrap_or(None);

    let media_type_str = row.media_type.as_wire().to_lowercase();

    // Series-specific data
    let mut total_seasons: Option<i32> = None;
    let mut total_episodes: Option<i32> = None;
    let mut seasons_json: Option<serde_json::Value> = None;

    if row.media_type == MediaType::Series && include_seasons {
        let series_row = sqlx::query_as::<_, (SeriesId, Option<i32>, Option<i32>)>(
            "SELECT id, total_seasons, total_episodes FROM series_metadata WHERE media_id = $1",
        )
        .bind(media_id)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten();

        if let Some((series_id, ts, te)) = series_row {
            total_seasons = ts;
            total_episodes = te;

            let season_rows: Vec<(SeasonId, i32, Option<String>, Option<String>, Option<String>, i32)> =
                sqlx::query_as(
                    "SELECT id, season_number, name, overview, air_date::text, episode_count FROM season WHERE series_id = $1 ORDER BY season_number ASC",
                )
                .bind(series_id)
                .fetch_all(pool)
                .await
                .unwrap_or_default();

            let mut seasons_arr = Vec::new();
            for (sid, snum, sname, soverview, sair_date, sepcnt) in season_rows {
                let ep_rows: Vec<(EpisodeId, i32, String, Option<String>, Option<String>, Option<i32>, bool, bool)> =
                    sqlx::query_as(
                        "SELECT id, episode_number, title, overview, air_date::text, runtime_minutes, is_user_created, is_user_addition FROM episode WHERE season_id = $1 ORDER BY episode_number ASC",
                    )
                    .bind(sid)
                    .fetch_all(pool)
                    .await
                    .unwrap_or_default();

                let episodes_arr: Vec<serde_json::Value> = ep_rows
                    .into_iter()
                    .map(
                        |(eid, enum_, etitle, eoverview, eair, eruntime, euser, eaddition)| {
                            json!({
                                "id": eid,
                                "episode_number": enum_,
                                "title": etitle,
                                "overview": eoverview,
                                "air_date": eair,
                                "runtime_minutes": eruntime,
                                "is_user_created": euser,
                                "is_user_addition": eaddition,
                            })
                        },
                    )
                    .collect();

                seasons_arr.push(json!({
                    "id": sid,
                    "season_number": snum,
                    "name": sname,
                    "overview": soverview,
                    "air_date": sair_date,
                    "episode_count": sepcnt,
                    "episodes": episodes_arr,
                }));
            }
            seasons_json = Some(json!(seasons_arr));
        }
    }

    json!({
        "id": row.id.0,
        "type": media_type_str,
        "title": row.title,
        "original_title": row.original_title,
        "year": row.year,
        "description": row.description,
        "tagline": row.tagline,
        "status": row.status,
        "website": row.website,
        "is_public": row.is_public,
        "is_user_created": row.is_user_created,
        "created_by_user_id": row.created_by_user_id,
        "total_streams": row.total_streams,
        "created_at": row.created_at.to_rfc3339(),
        "updated_at": row.updated_at.map(|d: DateTime<Utc>| d.to_rfc3339()),
        "runtime_minutes": row.runtime_minutes,
        "release_date": row.release_date,
        "original_language": row.original_language,
        "nudity_status": row.nudity_status.as_wire(),
        "poster_url": poster_url,
        "background_url": background_url,
        "logo_url": logo_url,
        "external_ids": serde_json::Value::Object(ext_map),
        "genres": genres.into_iter().map(|(g,)| g).collect::<Vec<_>>(),
        "catalogs": catalogs.into_iter().map(|(c,)| c).collect::<Vec<_>>(),
        "aka_titles": aka.into_iter().map(|(t,)| t).collect::<Vec<_>>(),
        "cast": cast.into_iter().map(|(n,)| n).collect::<Vec<_>>(),
        "directors": directors.into_iter().map(|(n,)| n).collect::<Vec<_>>(),
        "writers": writers.into_iter().map(|(n,)| n).collect::<Vec<_>>(),
        "parental_certificate": parental_cert,
        "total_seasons": total_seasons,
        "total_episodes": total_episodes,
        "seasons": seasons_json,
    })
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// POST /api/v1/metadata/user
pub async fn create_user_metadata(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<UserMediaCreate>,
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

    let media_type = match body.media_type.as_str() {
        "movie" => crate::db::MediaType::Movie,
        "series" => crate::db::MediaType::Series,
        "tv" => crate::db::MediaType::Tv,
        _ => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Invalid media type"})),
            )
                .into_response();
        }
    };

    let external_ids: Vec<(String, String)> = body
        .external_ids
        .as_ref()
        .map(|m| m.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
        .unwrap_or_default();

    let seasons: Vec<crate::db::NormalizedSeason> = body
        .seasons
        .as_ref()
        .map(|seasons| {
            seasons
                .iter()
                .map(|s| crate::db::NormalizedSeason {
                    season_number: s.season_number,
                    name: s.name.clone(),
                    overview: s.overview.clone(),
                    air_date: s.air_date.clone(),
                    episodes: s
                        .episodes
                        .iter()
                        .map(|ep| crate::db::NormalizedEpisode {
                            episode_number: ep.episode_number,
                            title: ep.title.clone(),
                            overview: ep.overview.clone(),
                            air_date: ep.air_date.clone(),
                            runtime_minutes: ep.runtime_minutes,
                            ..Default::default()
                        })
                        .collect(),
                })
                .collect()
        })
        .unwrap_or_default();

    let meta = crate::db::NormalizedMetadata {
        media_type,
        title: body.title.clone(),
        year: body.year,
        description: body.description.clone(),
        poster_url: body.poster_url.clone(),
        backdrop_url: body.background_url.clone(),
        genres: body.genres.clone().unwrap_or_default(),
        catalogs: body.catalogs.clone().unwrap_or_default(),
        external_ids,
        runtime_minutes: body.runtime_minutes,
        seasons,
        ..Default::default()
    };

    let media_id = match crate::db::store_media(
        &state.pool,
        &meta,
        crate::db::StoreMediaOpts::user_created(user_id, body.is_public),
    )
    .await
    {
        Ok(id) => id.0,
        Err(e) => {
            tracing::error!("create_user_metadata store_media: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    let resp = media_row_to_json(&state.pool, media_id, true).await;
    (StatusCode::CREATED, Json(resp)).into_response()
}

/// GET /api/v1/metadata/user
pub async fn list_user_metadata(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<ListQuery>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let page = params.page.max(1);
    let per_page = params.per_page.clamp(1, 100);
    let offset = (page - 1) * per_page;

    let mut count_sql = String::from(
        "SELECT COUNT(*) FROM media WHERE created_by_user_id = $1 AND is_user_created = true",
    );
    let mut fetch_sql = String::from(
        "SELECT id FROM media WHERE created_by_user_id = $1 AND is_user_created = true",
    );
    let mut idx = 2i32;

    if params.media_type.as_deref().is_some_and(|t| t != "all") {
        let db_type = match params.media_type.as_deref().unwrap_or("") {
            "movie" => "MOVIE",
            "series" => "SERIES",
            "tv" => "TV",
            _ => "",
        };
        if !db_type.is_empty() {
            count_sql.push_str(&format!(" AND type = '{}'::", db_type));
            count_sql.push_str("mediatype");
            fetch_sql.push_str(&format!(" AND type = '{}'::", db_type));
            fetch_sql.push_str("mediatype");
        }
    }

    if params.search.is_some() {
        count_sql.push_str(&format!(" AND title ILIKE ${idx}"));
        fetch_sql.push_str(&format!(" AND title ILIKE ${idx}"));
        idx += 1;
    }

    fetch_sql.push_str(&format!(
        " ORDER BY created_at DESC LIMIT ${idx} OFFSET ${}",
        idx + 1
    ));

    let mut count_q = sqlx::query_scalar::<_, i64>(&count_sql).bind(user_id);
    let mut fetch_q = sqlx::query_scalar::<_, i32>(&fetch_sql).bind(user_id);

    if let Some(ref s) = params.search {
        let pattern = format!("%{}%", s);
        count_q = count_q.bind(pattern.clone());
        fetch_q = fetch_q.bind(pattern);
    }

    fetch_q = fetch_q.bind(per_page).bind(offset);

    let total: i64 = count_q.fetch_one(&state.pool_ro).await.unwrap_or(0);
    let ids: Vec<i32> = fetch_q.fetch_all(&state.pool_ro).await.unwrap_or_default();

    let mut items = Vec::new();
    for id in ids {
        items.push(media_row_to_json(&state.pool_ro, id, false).await);
    }

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

/// GET /api/v1/metadata/user/search/all
pub async fn search_all_metadata(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<SearchQuery>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let limit = params.limit.clamp(1, 50);
    let pattern = format!("%{}%", params.query);

    let mut sql = String::from("SELECT id FROM media WHERE title ILIKE $1");

    if let Some(ref mt) = params.media_type {
        match mt.as_str() {
            "movie" => sql.push_str(" AND type = 'MOVIE'::media_type_enum"),
            "series" => sql.push_str(" AND type = 'SERIES'::media_type_enum"),
            "tv" => sql.push_str(" AND type = 'TV'::media_type_enum"),
            _ => {}
        }
    }

    if params.include_official {
        sql.push_str(
            " AND (is_user_created = false OR created_by_user_id = $2 OR is_public = true)",
        );
    } else {
        sql.push_str(" AND created_by_user_id = $2");
    }

    sql.push_str(&format!(" ORDER BY total_streams DESC LIMIT {limit}"));

    let ids: Vec<i64> = sqlx::query_scalar::<_, i64>(&sql)
        .bind(&pattern)
        .bind(user_id)
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default();

    let mut results = Vec::new();
    for id in &ids {
        let row = sqlx::query_as::<_, (String, Option<i32>, MediaType, bool, Option<i32>)>("SELECT title, year, type, is_user_created, created_by_user_id FROM media WHERE id = $1")
                .bind(id)
                .fetch_optional(&state.pool_ro)
                .await
                .ok()
                .flatten();
        if let Some((title, year, mtype, is_user, creator_id)) = row {
            let poster: Option<String> = sqlx::query_scalar(
                "SELECT url FROM media_image WHERE media_id = $1 AND image_type = 'poster' ORDER BY display_order ASC LIMIT 1",
            )
            .bind(id)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None);

            let ext_ids: Vec<(String, String)> = sqlx::query_as(
                "SELECT provider, external_id FROM media_external_id WHERE media_id = $1",
            )
            .bind(id)
            .fetch_all(&state.pool_ro)
            .await
            .unwrap_or_default();

            let mut ext_map = serde_json::Map::new();
            let mut canonical_id = format!("mf:{id}");
            for (p, e) in &ext_ids {
                let formatted = if p == "imdb" {
                    e.clone()
                } else {
                    format!("{p}:{e}")
                };
                if p == "imdb" && canonical_id.starts_with("mf:") {
                    canonical_id = formatted.clone();
                }
                ext_map.insert(p.clone(), json!(formatted));
            }

            results.push(json!({
                "id": id,
                "external_id": canonical_id,
                "external_ids": serde_json::Value::Object(ext_map),
                "title": title,
                "year": year,
                "type": mtype.as_wire(),
                "poster": poster,
                "is_user_created": is_user,
                "is_own": creator_id == Some(user_id as i32),
            }));
        }
    }

    Json(json!({"results": results, "total": results.len()})).into_response()
}

/// GET /api/v1/metadata/user/{media_id}
pub async fn get_user_metadata(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<MediaId>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let row = sqlx::query_as::<_, (bool, Option<i32>)>(
        "SELECT is_public, created_by_user_id FROM media WHERE id = $1",
    )
    .bind(media_id.0)
    .fetch_optional(&state.pool_ro)
    .await
    .ok()
    .flatten();

    match row {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Metadata not found"})),
            )
                .into_response()
        }
        Some((is_public, creator_id)) => {
            if creator_id != Some(user_id as i32) && !is_public {
                return (
                    StatusCode::FORBIDDEN,
                    Json(json!({"detail": "Access denied"})),
                )
                    .into_response();
            }
        }
    }

    let resp = media_row_to_json(&state.pool_ro, media_id.0, true).await;
    Json(resp).into_response()
}

/// PUT /api/v1/metadata/user/{media_id}
pub async fn update_user_metadata(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<MediaId>,
    Json(body): Json<UserMediaUpdate>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let creator_id: Option<Option<i32>> =
        sqlx::query_scalar("SELECT created_by_user_id FROM media WHERE id = $1")
            .bind(media_id.0)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);

    match creator_id {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Metadata not found"})),
            )
                .into_response()
        }
        Some(cid) if cid != Some(user_id as i32) => {
            return (
                StatusCode::FORBIDDEN,
                Json(json!({"detail": "Can only update your own metadata"})),
            )
                .into_response();
        }
        _ => {}
    }

    // Build dynamic update
    let mut updates: Vec<String> = vec!["updated_at = NOW()".to_string()];
    let mut idx = 2i32;

    macro_rules! push_field {
        ($field:expr, $col:expr) => {
            if $field.is_some() {
                updates.push(format!("{} = ${}", $col, idx));
                idx += 1;
            }
        };
    }

    push_field!(body.title, "title");
    push_field!(body.original_title, "original_title");
    push_field!(body.year, "year");
    push_field!(body.description, "description");
    push_field!(body.tagline, "tagline");
    push_field!(body.is_public, "is_public");
    push_field!(body.runtime_minutes, "runtime_minutes");
    push_field!(body.status, "status");
    push_field!(body.website, "website");
    push_field!(body.original_language, "original_language");
    let _ = idx;

    let sql = format!("UPDATE media SET {} WHERE id = $1", updates.join(", "));
    let mut q = sqlx::query(&sql).bind(media_id);
    if let Some(ref v) = body.title {
        q = q.bind(v);
    }
    if let Some(ref v) = body.original_title {
        q = q.bind(v);
    }
    if let Some(v) = body.year {
        q = q.bind(v);
    }
    if let Some(ref v) = body.description {
        q = q.bind(v);
    }
    if let Some(ref v) = body.tagline {
        q = q.bind(v);
    }
    if let Some(v) = body.is_public {
        q = q.bind(v);
    }
    if let Some(v) = body.runtime_minutes {
        q = q.bind(v);
    }
    if let Some(ref v) = body.status {
        q = q.bind(v);
    }
    if let Some(ref v) = body.website {
        q = q.bind(v);
    }
    if let Some(ref v) = body.original_language {
        q = q.bind(v);
    }

    if let Err(e) = q.execute(&state.pool).await {
        tracing::error!("update_user_metadata: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    // Update external IDs
    if let Some(ref ext_ids) = body.external_ids {
        for (provider, ext_id) in ext_ids {
            if !ext_id.is_empty() {
                let _ = sqlx::query(
                    "INSERT INTO media_external_id (media_id, provider, external_id) VALUES ($1, $2, $3) ON CONFLICT (media_id, provider) DO UPDATE SET external_id = EXCLUDED.external_id",
                )
                .bind(media_id)
                .bind(provider)
                .bind(ext_id)
                .execute(&state.pool)
                .await;
            }
        }
    }

    // Update genres
    if let Some(ref genres) = body.genres {
        let _ = sqlx::query("DELETE FROM media_genre_link WHERE media_id = $1")
            .bind(media_id)
            .execute(&state.pool)
            .await;
        for genre_name in genres {
            let genre_id: Option<i32> = sqlx::query_scalar(
                "INSERT INTO genre (name) VALUES ($1) ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
            )
            .bind(genre_name)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);
            if let Some(gid) = genre_id {
                let _ = sqlx::query("INSERT INTO media_genre_link (media_id, genre_id) VALUES ($1, $2) ON CONFLICT DO NOTHING")
                    .bind(media_id)
                    .bind(gid)
                    .execute(&state.pool)
                    .await;
            }
        }
    }

    let resp = media_row_to_json(&state.pool, media_id.0, true).await;
    Json(resp).into_response()
}

/// DELETE /api/v1/metadata/user/{media_id}
pub async fn delete_user_metadata(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<MediaId>,
    Query(params): Query<DeleteMediaQuery>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let creator_id: Option<Option<i32>> =
        sqlx::query_scalar("SELECT created_by_user_id FROM media WHERE id = $1")
            .bind(media_id.0)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);

    match creator_id {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Metadata not found"})),
            )
                .into_response()
        }
        Some(cid) if cid != Some(user_id as i32) => {
            return (
                StatusCode::FORBIDDEN,
                Json(json!({"detail": "Can only delete your own metadata"})),
            )
                .into_response();
        }
        _ => {}
    }

    if !params.force {
        let link_count: i64 =
            sqlx::query_scalar("SELECT COUNT(*) FROM stream_media_link WHERE media_id = $1")
                .bind(media_id)
                .fetch_one(&state.pool)
                .await
                .unwrap_or(0);
        if link_count > 0 {
            return (
                StatusCode::CONFLICT,
                Json(json!({"detail": format!("Cannot delete: {} stream(s) are linked. Use force=true.", link_count)})),
            )
                .into_response();
        }
    }

    if let Err(e) = sqlx::query("DELETE FROM media WHERE id = $1")
        .bind(media_id)
        .execute(&state.pool)
        .await
    {
        tracing::error!("delete_user_metadata: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    StatusCode::NO_CONTENT.into_response()
}

/// POST /api/v1/metadata/user/{media_id}/seasons
pub async fn add_season_to_series(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<MediaId>,
    Json(body): Json<SeasonAddRequest>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let row = sqlx::query_as::<_, (MediaType, Option<i32>)>(
        "SELECT type, created_by_user_id FROM media WHERE id = $1",
    )
    .bind(media_id)
    .fetch_optional(&state.pool)
    .await
    .ok()
    .flatten();

    let (mtype, creator_id) = match row {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Metadata not found"})),
            )
                .into_response()
        }
        Some(r) => r,
    };

    if mtype != MediaType::Series {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Can only add seasons to series"})),
        )
            .into_response();
    }
    if creator_id != Some(user_id) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "Can only modify your own metadata"})),
        )
            .into_response();
    }

    let series_id: Option<SeriesId> =
        sqlx::query_scalar("SELECT id FROM series_metadata WHERE media_id = $1")
            .bind(media_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);

    let series_id = match series_id {
        None => return StatusCode::INTERNAL_SERVER_ERROR.into_response(),
        Some(sid) => sid,
    };

    // Check if season exists
    let exists: bool = sqlx::query_scalar(
        "SELECT EXISTS(SELECT 1 FROM season WHERE series_id = $1 AND season_number = $2)",
    )
    .bind(series_id)
    .bind(body.season_number)
    .fetch_one(&state.pool)
    .await
    .unwrap_or(false);

    if exists {
        return (
            StatusCode::CONFLICT,
            Json(json!({"detail": format!("Season {} already exists", body.season_number)})),
        )
            .into_response();
    }

    let season_id: i64 = match sqlx::query_scalar(
        "INSERT INTO season (series_id, season_number, name, overview, episode_count) VALUES ($1, $2, $3, $4, $5) RETURNING id",
    )
    .bind(series_id)
    .bind(body.season_number)
    .bind(&body.name)
    .bind(&body.overview)
    .bind(body.episodes.len() as i32)
    .fetch_one(&state.pool)
    .await
    {
        Ok(id) => id,
        Err(e) => {
            tracing::error!("add_season_to_series: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    let mut episodes_json = Vec::new();
    for ep in &body.episodes {
        let ep_id: i64 = match sqlx::query_scalar(
            "INSERT INTO episode (season_id, episode_number, title, overview, runtime_minutes, is_user_created, created_by_user_id) VALUES ($1, $2, $3, $4, $5, true, $6) RETURNING id",
        )
        .bind(season_id)
        .bind(ep.episode_number)
        .bind(&ep.title)
        .bind(&ep.overview)
        .bind(ep.runtime_minutes)
        .bind(user_id)
        .fetch_one(&state.pool)
        .await
        {
            Ok(id) => id,
            Err(e) => { tracing::error!("add_season episodes: {e}"); continue; }
        };
        episodes_json.push(json!({
            "id": ep_id,
            "episode_number": ep.episode_number,
            "title": ep.title,
            "overview": ep.overview,
            "air_date": ep.air_date,
            "runtime_minutes": ep.runtime_minutes,
            "is_user_created": true,
            "is_user_addition": false,
        }));
    }

    // Update series totals
    let _ = sqlx::query(
        "UPDATE series_metadata SET total_seasons = COALESCE(total_seasons, 0) + 1, total_episodes = COALESCE(total_episodes, 0) + $1 WHERE id = $2",
    )
    .bind(body.episodes.len() as i32)
    .bind(series_id)
    .execute(&state.pool)
    .await;

    Json(json!({
        "id": season_id,
        "season_number": body.season_number,
        "name": body.name,
        "overview": body.overview,
        "air_date": body.air_date,
        "episode_count": body.episodes.len(),
        "episodes": episodes_json,
    }))
    .into_response()
}

/// POST /api/v1/metadata/user/{media_id}/episodes
pub async fn add_episodes_to_series(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<MediaId>,
    Json(body): Json<EpisodeAddRequest>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let row = sqlx::query_as::<_, (MediaType, Option<i32>)>(
        "SELECT type, created_by_user_id FROM media WHERE id = $1",
    )
    .bind(media_id)
    .fetch_optional(&state.pool)
    .await
    .ok()
    .flatten();

    let (mtype, creator_id) = match row {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Metadata not found"})),
            )
                .into_response()
        }
        Some(r) => r,
    };

    if mtype != MediaType::Series {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Can only add episodes to series"})),
        )
            .into_response();
    }
    if creator_id != Some(user_id) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "Can only modify your own metadata"})),
        )
            .into_response();
    }

    let series_id: Option<SeriesId> =
        sqlx::query_scalar("SELECT id FROM series_metadata WHERE media_id = $1")
            .bind(media_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);
    let series_id = match series_id {
        None => return StatusCode::INTERNAL_SERVER_ERROR.into_response(),
        Some(sid) => sid,
    };

    let season_id: Option<SeasonId> =
        sqlx::query_scalar("SELECT id FROM season WHERE series_id = $1 AND season_number = $2")
            .bind(series_id)
            .bind(body.season_number)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);
    let season_id = match season_id {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": format!("Season {} not found", body.season_number)})),
            )
                .into_response()
        }
        Some(sid) => sid,
    };

    let mut created_episodes = Vec::new();
    for ep in &body.episodes {
        match sqlx::query_scalar::<_, i64>(
            "INSERT INTO episode (season_id, episode_number, title, overview, runtime_minutes, is_user_created, created_by_user_id) VALUES ($1, $2, $3, $4, $5, true, $6) RETURNING id",
        )
        .bind(season_id)
        .bind(ep.episode_number)
        .bind(&ep.title)
        .bind(&ep.overview)
        .bind(ep.runtime_minutes)
        .bind(user_id)
        .fetch_one(&state.pool)
        .await
        {
            Ok(ep_id) => {
                created_episodes.push(json!({
                    "id": ep_id,
                    "episode_number": ep.episode_number,
                    "title": ep.title,
                    "overview": ep.overview,
                    "air_date": ep.air_date,
                    "runtime_minutes": ep.runtime_minutes,
                    "is_user_created": true,
                    "is_user_addition": false,
                }));
            }
            Err(e) => {
                tracing::error!("add_episodes_to_series insert: {e}");
            }
        }
    }

    let _ = sqlx::query("UPDATE season SET episode_count = episode_count + $1 WHERE id = $2")
        .bind(body.episodes.len() as i32)
        .bind(season_id)
        .execute(&state.pool)
        .await;

    let _ = sqlx::query(
        "UPDATE series_metadata SET total_episodes = COALESCE(total_episodes, 0) + $1 WHERE id = $2",
    )
    .bind(created_episodes.len() as i32)
    .bind(series_id)
    .execute(&state.pool)
    .await;

    Json(json!(created_episodes)).into_response()
}

/// PUT /api/v1/metadata/user/{media_id}/episodes/{episode_id}
pub async fn update_episode(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path((media_id, episode_id)): Path<(MediaId, EpisodeId)>,
    Json(body): Json<EpisodeUpdateRequest>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let creator_id: Option<Option<i32>> =
        sqlx::query_scalar("SELECT created_by_user_id FROM media WHERE id = $1")
            .bind(media_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);

    match creator_id {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Metadata not found"})),
            )
                .into_response()
        }
        Some(cid) if cid != Some(user_id) => {
            return (
                StatusCode::FORBIDDEN,
                Json(json!({"detail": "Can only modify your own metadata"})),
            )
                .into_response();
        }
        _ => {}
    }

    let ep_exists: bool = sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM episode WHERE id = $1)")
        .bind(episode_id)
        .fetch_one(&state.pool)
        .await
        .unwrap_or(false);

    if !ep_exists {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Episode not found"})),
        )
            .into_response();
    }

    let mut updates = vec!["updated_at = NOW()".to_string()];
    let mut idx = 2i32;

    if body.title.is_some() {
        updates.push(format!("title = ${idx}"));
        idx += 1;
    }
    if body.overview.is_some() {
        updates.push(format!("overview = ${idx}"));
        idx += 1;
    }
    if body.air_date.is_some() {
        updates.push(format!("air_date = ${idx}::date"));
        idx += 1;
    }
    if body.runtime_minutes.is_some() {
        updates.push(format!("runtime_minutes = ${idx}"));
        idx += 1;
    }
    let _ = idx;

    let sql = format!("UPDATE episode SET {} WHERE id = $1 RETURNING id, episode_number, title, overview, air_date::text, runtime_minutes, is_user_created, is_user_addition", updates.join(", "));
    let mut q = sqlx::query_as::<
        _,
        (
            i64,
            i32,
            String,
            Option<String>,
            Option<String>,
            Option<i32>,
            bool,
            bool,
        ),
    >(&sql)
    .bind(episode_id);

    if let Some(ref v) = body.title {
        q = q.bind(v);
    }
    if let Some(ref v) = body.overview {
        q = q.bind(v);
    }
    if let Some(ref v) = body.air_date {
        q = q.bind(v);
    }
    if let Some(v) = body.runtime_minutes {
        q = q.bind(v);
    }

    match q.fetch_optional(&state.pool).await {
        Ok(Some((eid, enum_, etitle, eoverview, eair, eruntime, euser, eaddition))) => {
            Json(json!({
                "id": eid,
                "episode_number": enum_,
                "title": etitle,
                "overview": eoverview,
                "air_date": eair,
                "runtime_minutes": eruntime,
                "is_user_created": euser,
                "is_user_addition": eaddition,
            }))
            .into_response()
        }
        Ok(None) => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Episode not found"})),
        )
            .into_response(),
        Err(e) => {
            tracing::error!("update_episode: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

/// DELETE /api/v1/metadata/user/{media_id}/episodes/{episode_id}
pub async fn delete_episode(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path((media_id, episode_id)): Path<(MediaId, EpisodeId)>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let creator_id: Option<Option<i32>> =
        sqlx::query_scalar("SELECT created_by_user_id FROM media WHERE id = $1")
            .bind(media_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);

    match creator_id {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Metadata not found"})),
            )
                .into_response()
        }
        Some(cid) if cid != Some(user_id) => {
            return (
                StatusCode::FORBIDDEN,
                Json(json!({"detail": "Can only modify your own metadata"})),
            )
                .into_response();
        }
        _ => {}
    }

    let season_id: Option<SeasonId> =
        sqlx::query_scalar("SELECT season_id FROM episode WHERE id = $1")
            .bind(episode_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);

    if season_id.is_none() {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Episode not found"})),
        )
            .into_response();
    }

    let _ = sqlx::query("DELETE FROM episode_image WHERE episode_id = $1")
        .bind(episode_id)
        .execute(&state.pool)
        .await;

    if let Err(e) = sqlx::query("DELETE FROM episode WHERE id = $1")
        .bind(episode_id)
        .execute(&state.pool)
        .await
    {
        tracing::error!("delete_episode: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    if let Some(sid) = season_id {
        let _ = sqlx::query(
            "UPDATE season SET episode_count = GREATEST(0, episode_count - 1) WHERE id = $1",
        )
        .bind(sid)
        .execute(&state.pool)
        .await;
        let series_id: Option<SeriesId> =
            sqlx::query_scalar("SELECT series_id FROM season WHERE id = $1")
                .bind(sid)
                .fetch_optional(&state.pool)
                .await
                .unwrap_or(None);
        if let Some(smid) = series_id {
            let _ = sqlx::query("UPDATE series_metadata SET total_episodes = GREATEST(0, COALESCE(total_episodes, 1) - 1) WHERE id = $1")
                .bind(smid)
                .execute(&state.pool)
                .await;
        }
    }

    StatusCode::NO_CONTENT.into_response()
}

/// DELETE /api/v1/metadata/user/{media_id}/episodes/{episode_id}/admin
pub async fn admin_delete_episode(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path((media_id, episode_id)): Path<(MediaId, EpisodeId)>,
    Query(params): Query<DeleteEpisodeAdminQuery>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    if let Err(resp) = require_mod_or_admin(&state.pool, user_id).await {
        return resp;
    }

    let media_exists: bool = sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM media WHERE id = $1)")
        .bind(media_id)
        .fetch_one(&state.pool)
        .await
        .unwrap_or(false);

    if !media_exists {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Metadata not found"})),
        )
            .into_response();
    }

    let season_id: Option<SeasonId> =
        sqlx::query_scalar("SELECT season_id FROM episode WHERE id = $1")
            .bind(episode_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);

    if season_id.is_none() {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Episode not found"})),
        )
            .into_response();
    }

    if params.delete_stream_links {
        let _ = sqlx::query("DELETE FROM file_media_link WHERE media_id = $1 AND episode_number = (SELECT episode_number FROM episode WHERE id = $2)")
            .bind(media_id)
            .bind(episode_id)
            .execute(&state.pool)
            .await;
    }

    let _ = sqlx::query("DELETE FROM episode_image WHERE episode_id = $1")
        .bind(episode_id)
        .execute(&state.pool)
        .await;

    if let Err(e) = sqlx::query("DELETE FROM episode WHERE id = $1")
        .bind(episode_id)
        .execute(&state.pool)
        .await
    {
        tracing::error!("delete_episode_admin: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    if let Some(sid) = season_id {
        let _ = sqlx::query(
            "UPDATE season SET episode_count = GREATEST(0, episode_count - 1) WHERE id = $1",
        )
        .bind(sid)
        .execute(&state.pool)
        .await;
        let series_id: Option<SeriesId> =
            sqlx::query_scalar("SELECT series_id FROM season WHERE id = $1")
                .bind(sid)
                .fetch_optional(&state.pool)
                .await
                .unwrap_or(None);
        if let Some(smid) = series_id {
            let _ = sqlx::query("UPDATE series_metadata SET total_episodes = GREATEST(0, COALESCE(total_episodes, 1) - 1) WHERE id = $1")
                .bind(smid)
                .execute(&state.pool)
                .await;
        }
    }

    StatusCode::NO_CONTENT.into_response()
}

/// DELETE /api/v1/metadata/user/{media_id}/seasons/{season_number}
pub async fn delete_season(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path((media_id, season_number)): Path<(MediaId, i32)>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let creator_id: Option<Option<i32>> =
        sqlx::query_scalar("SELECT created_by_user_id FROM media WHERE id = $1")
            .bind(media_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);

    match creator_id {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Metadata not found"})),
            )
                .into_response()
        }
        Some(cid) if cid != Some(user_id) => {
            return (
                StatusCode::FORBIDDEN,
                Json(json!({"detail": "Can only modify your own metadata"})),
            )
                .into_response();
        }
        _ => {}
    }

    let series_id: Option<SeriesId> =
        sqlx::query_scalar("SELECT id FROM series_metadata WHERE media_id = $1")
            .bind(media_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);
    let series_id = match series_id {
        None => return StatusCode::INTERNAL_SERVER_ERROR.into_response(),
        Some(sid) => sid,
    };

    let season_row: Option<(i64, i32)> = sqlx::query_as(
        "SELECT id, episode_count FROM season WHERE series_id = $1 AND season_number = $2",
    )
    .bind(series_id)
    .bind(season_number)
    .fetch_optional(&state.pool)
    .await
    .unwrap_or(None);

    let (season_id, ep_count) = match season_row {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": format!("Season {} not found", season_number)})),
            )
                .into_response()
        }
        Some(r) => r,
    };

    if let Err(e) = sqlx::query("DELETE FROM season WHERE id = $1")
        .bind(season_id)
        .execute(&state.pool)
        .await
    {
        tracing::error!("delete_season: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    let _ = sqlx::query(
        "UPDATE series_metadata SET total_seasons = GREATEST(0, COALESCE(total_seasons, 1) - 1), total_episodes = GREATEST(0, COALESCE(total_episodes, $1) - $1) WHERE id = $2",
    )
    .bind(ep_count)
    .bind(series_id)
    .execute(&state.pool)
    .await;

    StatusCode::NO_CONTENT.into_response()
}

/// DELETE /api/v1/metadata/user/{media_id}/seasons/{season_number}/admin
pub async fn admin_delete_season(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path((media_id, season_number)): Path<(MediaId, i32)>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    if let Err(resp) = require_mod_or_admin(&state.pool, user_id).await {
        return resp;
    }

    let media_exists: bool = sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM media WHERE id = $1)")
        .bind(media_id)
        .fetch_one(&state.pool)
        .await
        .unwrap_or(false);

    if !media_exists {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Metadata not found"})),
        )
            .into_response();
    }

    let series_id: Option<SeriesId> =
        sqlx::query_scalar("SELECT id FROM series_metadata WHERE media_id = $1")
            .bind(media_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);
    let series_id = match series_id {
        None => return StatusCode::INTERNAL_SERVER_ERROR.into_response(),
        Some(sid) => sid,
    };

    let season_row: Option<(i64, i32)> = sqlx::query_as(
        "SELECT id, episode_count FROM season WHERE series_id = $1 AND season_number = $2",
    )
    .bind(series_id)
    .bind(season_number)
    .fetch_optional(&state.pool)
    .await
    .unwrap_or(None);

    let (season_id, ep_count) = match season_row {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": format!("Season {} not found", season_number)})),
            )
                .into_response()
        }
        Some(r) => r,
    };

    if let Err(e) = sqlx::query("DELETE FROM season WHERE id = $1")
        .bind(season_id)
        .execute(&state.pool)
        .await
    {
        tracing::error!("delete_season_admin: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    let _ = sqlx::query(
        "UPDATE series_metadata SET total_seasons = GREATEST(0, COALESCE(total_seasons, 1) - 1), total_episodes = GREATEST(0, COALESCE(total_episodes, $1) - $1) WHERE id = $2",
    )
    .bind(ep_count)
    .bind(series_id)
    .execute(&state.pool)
    .await;

    StatusCode::NO_CONTENT.into_response()
}

// ─── Import request body ──────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct ImportFromExternalRequest {
    pub provider: String,
    pub external_id: String,
    pub media_type: String,
}

fn validate_external_id(provider: &str, external_id: &str) -> Result<(), String> {
    match provider {
        "imdb" if !external_id.starts_with("tt") => {
            return Err("IMDB external_id must start with 'tt'".to_string());
        }
        "tmdb" | "tvdb" if external_id.parse::<i64>().is_err() => {
            return Err(format!("{} external_id must be numeric", provider));
        }
        _ => {}
    }
    Ok(())
}

/// POST /api/v1/metadata/user/import/preview  (preview only, no DB writes)
pub async fn import_user_metadata_preview(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<ImportFromExternalRequest>,
) -> Response {
    let _user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    if let Err(msg) = validate_external_id(&body.provider, &body.external_id) {
        return (StatusCode::BAD_REQUEST, Json(json!({"detail": msg}))).into_response();
    }

    // Check if media already exists
    let existing: Option<(i64, String, Option<i32>)> = sqlx::query_as(
        r#"SELECT m.id, m.title, m.year FROM media m
           JOIN media_external_id meid ON m.id = meid.media_id
           WHERE meid.provider = $1 AND meid.external_id = $2
           LIMIT 1"#,
    )
    .bind(&body.provider)
    .bind(&body.external_id)
    .fetch_optional(&state.pool_ro)
    .await
    .unwrap_or(None);

    match existing {
        Some((media_id, title, year)) => Json(json!({
            "exists": true,
            "media_id": media_id,
            "title": title,
            "year": year,
            "provider": body.provider,
            "external_id": body.external_id,
            "media_type": body.media_type,
            "message": "Media already exists in the database",
        }))
        .into_response(),
        None => {
            let tmdb_key = state.config.tmdb_api_key.as_deref();
            let is_series = body.media_type.eq_ignore_ascii_case("series")
                || body.media_type.eq_ignore_ascii_case("show");
            match crate::scrapers::metadata::fetch_by_external_id_with_opts(
                &state.http,
                &body.provider,
                &body.external_id,
                is_series,
                crate::scrapers::metadata::FetchCtx::with_tmdb_tvdb(
                    tmdb_key,
                    state.config.tvdb_api_key.as_deref(),
                    state.config.imdb_cinemeta_fallback_enabled,
                ),
            )
            .await
            {
                Some(details) => Json(json!({
                    "exists": false,
                    "media_id": null,
                    "provider": body.provider,
                    "external_id": body.external_id,
                    "media_type": body.media_type,
                    "preview": {
                        "title": details.title,
                        "year": details.year,
                        "description": details.description,
                        "poster_url": details.poster_url,
                        "imdb_id": details.imdb_id,
                        "tmdb_id": details.tmdb_id,
                    },
                    "message": "Media found. Use /import to create it.",
                }))
                .into_response(),
                None => Json(json!({
                    "exists": false,
                    "media_id": null,
                    "provider": body.provider,
                    "external_id": body.external_id,
                    "media_type": body.media_type,
                    "preview": null,
                    "message": "Media not found in external provider.",
                }))
                .into_response(),
            }
        }
    }
}

/// POST /api/v1/metadata/user/import
pub async fn import_from_external(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<ImportFromExternalRequest>,
) -> Response {
    let _user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    if let Err(msg) = validate_external_id(&body.provider, &body.external_id) {
        return (StatusCode::BAD_REQUEST, Json(json!({"detail": msg}))).into_response();
    }

    // Check if media already exists
    let existing: Option<i32> = sqlx::query_scalar(
        r#"SELECT m.id FROM media m
           JOIN media_external_id meid ON m.id = meid.media_id
           WHERE meid.provider = $1 AND meid.external_id = $2
           LIMIT 1"#,
    )
    .bind(&body.provider)
    .bind(&body.external_id)
    .fetch_optional(&state.pool_ro)
    .await
    .unwrap_or(None);

    match existing {
        Some(media_id) => {
            let media_json = media_row_to_json(&state.pool_ro, media_id, false).await;
            Json(media_json).into_response()
        }
        None => {
            let tmdb_key = state.config.tmdb_api_key.as_deref();
            let is_series = body.media_type.eq_ignore_ascii_case("series")
                || body.media_type.eq_ignore_ascii_case("show");

            let ctx = crate::scrapers::metadata::FetchCtx {
                tmdb_api_key: tmdb_key,
                tvdb_api_key: state.config.tvdb_api_key.as_deref(),
                mdblist_api_key: state.config.mdblist_api_key.as_deref(),
                trakt_client_id: state.config.trakt_client_id.as_deref(),
                trakt_client_secret: state.config.trakt_client_secret.as_deref(),
                cinemeta_fallback: state.config.imdb_cinemeta_fallback_enabled,
            };

            let normalized = match crate::scrapers::metadata::fetch_normalized(
                &state.http,
                &ctx,
                &body.provider,
                &body.external_id,
                is_series,
            )
            .await
            {
                Some(d) => d,
                None => {
                    return (
                        StatusCode::NOT_FOUND,
                        Json(json!({
                            "detail": "Media not found in external provider. Check the ID or add it manually."
                        })),
                    )
                        .into_response()
                }
            };

            let media_id = match crate::db::store_media(
                &state.pool,
                &normalized,
                crate::db::StoreMediaOpts::default(),
            )
            .await
            {
                Ok(id) => id.0,
                Err(e) => {
                    tracing::error!("import_from_external store_media: {e}");
                    return (
                        StatusCode::INTERNAL_SERVER_ERROR,
                        Json(json!({"detail": "Failed to create media entry"})),
                    )
                        .into_response();
                }
            };

            let media_json = media_row_to_json(&state.pool_ro, media_id, true).await;
            Json(media_json).into_response()
        }
    }
}
