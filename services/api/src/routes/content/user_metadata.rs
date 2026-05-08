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
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use serde_json::json;
use sha2::Sha256;

use crate::state::AppState;

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

async fn require_mod_or_admin(pool: &sqlx::PgPool, user_id: i64) -> Result<(), Response> {
    let role: Option<String> =
        sqlx::query_scalar("SELECT role::text FROM users WHERE id = $1")
            .bind(user_id)
            .fetch_optional(pool)
            .await
            .unwrap_or(None);
    match role.as_deref() {
        Some("moderator") | Some("admin") => Ok(()),
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
    #[serde(rename = "type")]
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

async fn media_row_to_json(
    pool: &sqlx::PgPool,
    media_id: i64,
    include_seasons: bool,
) -> serde_json::Value {
    // Basic fields
    let row: Option<(i64, String, String, Option<String>, Option<i32>, Option<String>, Option<String>, Option<String>, Option<String>, bool, bool, Option<i64>, i64, DateTime<Utc>, Option<DateTime<Utc>>, Option<i32>, Option<String>, Option<String>, Option<String>, Option<String>)> =
        sqlx::query_as(
            r#"SELECT id, type, title, original_title, year, description, tagline,
                      status, website, is_public, is_user_created,
                      created_by_user_id, total_streams,
                      created_at, updated_at,
                      runtime_minutes, release_date::text,
                      original_language, nudity_status::text, source
               FROM media WHERE id = $1"#,
        )
        .bind(media_id)
        .fetch_optional(pool)
        .await
        .unwrap_or(None);

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
            "background" | "backdrop" if background_url.is_none() => background_url = Some(url.clone()),
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
    let aka: Vec<(String,)> =
        sqlx::query_as("SELECT title FROM aka_title WHERE media_id = $1")
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

    let media_type_str = row.1.to_lowercase();

    // Series-specific data
    let mut total_seasons: Option<i64> = None;
    let mut total_episodes: Option<i64> = None;
    let mut seasons_json: Option<serde_json::Value> = None;

    if media_type_str == "series" && include_seasons {
        let series_row: Option<(i64, Option<i64>, Option<i64>)> = sqlx::query_as(
            "SELECT id, total_seasons, total_episodes FROM series_metadata WHERE media_id = $1",
        )
        .bind(media_id)
        .fetch_optional(pool)
        .await
        .unwrap_or(None);

        if let Some((series_id, ts, te)) = series_row {
            total_seasons = ts;
            total_episodes = te;

            let season_rows: Vec<(i64, i32, Option<String>, Option<String>, Option<String>, i32)> =
                sqlx::query_as(
                    "SELECT id, season_number, name, overview, air_date::text, episode_count FROM season WHERE series_id = $1 ORDER BY season_number ASC",
                )
                .bind(series_id)
                .fetch_all(pool)
                .await
                .unwrap_or_default();

            let mut seasons_arr = Vec::new();
            for (sid, snum, sname, soverview, sair_date, sepcnt) in season_rows {
                let ep_rows: Vec<(i64, i32, String, Option<String>, Option<String>, Option<i32>, bool, bool)> =
                    sqlx::query_as(
                        "SELECT id, episode_number, title, overview, air_date::text, runtime_minutes, is_user_created, is_user_addition FROM episode WHERE season_id = $1 ORDER BY episode_number ASC",
                    )
                    .bind(sid)
                    .fetch_all(pool)
                    .await
                    .unwrap_or_default();

                let episodes_arr: Vec<serde_json::Value> = ep_rows
                    .into_iter()
                    .map(|(eid, enum_, etitle, eoverview, eair, eruntime, euser, eaddition)| {
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
                    })
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
        "id": row.0,
        "type": media_type_str,
        "title": row.2,
        "original_title": row.3,
        "year": row.4,
        "description": row.5,
        "tagline": row.6,
        "status": row.7,
        "website": row.8,
        "is_public": row.9,
        "is_user_created": row.10,
        "created_by_user_id": row.11,
        "total_streams": row.12,
        "created_at": row.13.to_rfc3339(),
        "updated_at": row.14.map(|d: DateTime<Utc>| d.to_rfc3339()),
        "runtime_minutes": row.15,
        "release_date": row.16,
        "original_language": row.17,
        "nudity_status": row.18,
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
            return (StatusCode::UNAUTHORIZED, Json(json!({"detail": "Unauthorized"}))).into_response();
        }
    };

    let media_type_db = match body.media_type.as_str() {
        "movie" => "MOVIE",
        "series" => "SERIES",
        "tv" => "TV",
        _ => {
            return (StatusCode::BAD_REQUEST, Json(json!({"detail": "Invalid media type"}))).into_response();
        }
    };

    let media_id: i64 = match sqlx::query_scalar(
        r#"INSERT INTO media (type, title, year, description, runtime_minutes, is_user_created, created_by_user_id, is_public, total_streams, created_at)
           VALUES ($1::media_type_enum, $2, $3, $4, $5, true, $6, $7, 0, NOW())
           RETURNING id"#,
    )
    .bind(media_type_db)
    .bind(&body.title)
    .bind(body.year)
    .bind(&body.description)
    .bind(body.runtime_minutes)
    .bind(user_id)
    .bind(body.is_public)
    .fetch_one(&state.pool)
    .await
    {
        Ok(id) => id,
        Err(e) => {
            tracing::error!("create_user_metadata insert: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    // Add external IDs
    if let Some(ref ext_ids) = body.external_ids {
        for (provider, ext_id) in ext_ids {
            let _ = sqlx::query(
                "INSERT INTO media_external_id (media_id, provider, external_id) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
            )
            .bind(media_id)
            .bind(provider)
            .bind(ext_id)
            .execute(&state.pool)
            .await;
        }
    }

    // Add genres
    if let Some(ref genres) = body.genres {
        for genre_name in genres {
            let genre_id: Option<i64> = sqlx::query_scalar(
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

    // Add images
    if let Some(ref poster) = body.poster_url {
        let _ = sqlx::query(
            "INSERT INTO media_image (media_id, image_type, url, display_order) VALUES ($1, 'poster', $2, 0)",
        )
        .bind(media_id)
        .bind(poster)
        .execute(&state.pool)
        .await;
    }
    if let Some(ref bg) = body.background_url {
        let _ = sqlx::query(
            "INSERT INTO media_image (media_id, image_type, url, display_order) VALUES ($1, 'background', $2, 0)",
        )
        .bind(media_id)
        .bind(bg)
        .execute(&state.pool)
        .await;
    }

    // Type-specific metadata
    if media_type_db == "MOVIE" {
        let _ = sqlx::query("INSERT INTO movie_metadata (media_id) VALUES ($1) ON CONFLICT DO NOTHING")
            .bind(media_id)
            .execute(&state.pool)
            .await;
    } else if media_type_db == "SERIES" {
        let total_ep_count: i32 = body
            .seasons
            .as_ref()
            .map(|s| s.iter().map(|s| s.episodes.len() as i32).sum())
            .unwrap_or(0);
        let total_seasons_count: i32 = body.seasons.as_ref().map(|s| s.len() as i32).unwrap_or(0);

        let series_id: i64 = match sqlx::query_scalar(
            "INSERT INTO series_metadata (media_id, total_seasons, total_episodes) VALUES ($1, $2, $3) RETURNING id",
        )
        .bind(media_id)
        .bind(total_seasons_count)
        .bind(total_ep_count)
        .fetch_one(&state.pool)
        .await
        {
            Ok(id) => id,
            Err(e) => {
                tracing::error!("create_user_metadata series_metadata: {e}");
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            }
        };

        if let Some(ref seasons) = body.seasons {
            for season_data in seasons {
                let season_id: i64 = match sqlx::query_scalar(
                    "INSERT INTO season (series_id, season_number, name, overview, episode_count) VALUES ($1, $2, $3, $4, $5) RETURNING id",
                )
                .bind(series_id)
                .bind(season_data.season_number)
                .bind(&season_data.name)
                .bind(&season_data.overview)
                .bind(season_data.episodes.len() as i32)
                .fetch_one(&state.pool)
                .await
                {
                    Ok(id) => id,
                    Err(e) => {
                        tracing::error!("create_user_metadata season insert: {e}");
                        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
                    }
                };

                for ep in &season_data.episodes {
                    let _ = sqlx::query(
                        "INSERT INTO episode (season_id, episode_number, title, overview, runtime_minutes, is_user_created, created_by_user_id) VALUES ($1, $2, $3, $4, $5, true, $6)",
                    )
                    .bind(season_id)
                    .bind(ep.episode_number)
                    .bind(&ep.title)
                    .bind(&ep.overview)
                    .bind(ep.runtime_minutes)
                    .bind(user_id)
                    .execute(&state.pool)
                    .await;
                }
            }
        }
    }

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
        None => return (StatusCode::UNAUTHORIZED, Json(json!({"detail": "Unauthorized"}))).into_response(),
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
            count_sql.push_str("media_type_enum");
            fetch_sql.push_str(&format!(" AND type = '{}'::", db_type));
            fetch_sql.push_str("media_type_enum");
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
    let mut fetch_q = sqlx::query_scalar::<_, i64>(&fetch_sql).bind(user_id);

    if let Some(ref s) = params.search {
        let pattern = format!("%{}%", s);
        count_q = count_q.bind(pattern.clone());
        fetch_q = fetch_q.bind(pattern);
    }

    fetch_q = fetch_q.bind(per_page).bind(offset);

    let total: i64 = count_q.fetch_one(&state.pool_ro).await.unwrap_or(0);
    let ids: Vec<i64> = fetch_q.fetch_all(&state.pool_ro).await.unwrap_or_default();

    let mut items = Vec::new();
    for id in ids {
        items.push(media_row_to_json(&state.pool_ro, id, false).await);
    }

    let pages = if total > 0 { (total + per_page - 1) / per_page } else { 1 };

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
        None => return (StatusCode::UNAUTHORIZED, Json(json!({"detail": "Unauthorized"}))).into_response(),
    };

    let limit = params.limit.clamp(1, 50);
    let pattern = format!("%{}%", params.query);

    let mut sql = String::from(
        "SELECT id FROM media WHERE title ILIKE $1",
    );

    if let Some(ref mt) = params.media_type {
        match mt.as_str() {
            "movie" => sql.push_str(" AND type = 'MOVIE'::media_type_enum"),
            "series" => sql.push_str(" AND type = 'SERIES'::media_type_enum"),
            "tv" => sql.push_str(" AND type = 'TV'::media_type_enum"),
            _ => {}
        }
    }

    if params.include_official {
        sql.push_str(&format!(
            " AND (is_user_created = false OR created_by_user_id = $2 OR is_public = true)"
        ));
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
        let row: Option<(String, Option<i32>, String, bool, Option<i64>)> =
            sqlx::query_as("SELECT title, year, type::text, is_user_created, created_by_user_id FROM media WHERE id = $1")
                .bind(id)
                .fetch_optional(&state.pool_ro)
                .await
                .unwrap_or(None);
        if let Some((title, year, mtype, is_user, creator_id)) = row {
            let poster: Option<String> = sqlx::query_scalar(
                "SELECT url FROM media_image WHERE media_id = $1 AND image_type = 'poster' ORDER BY display_order ASC LIMIT 1",
            )
            .bind(id)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None);

            let ext_ids: Vec<(String, String)> =
                sqlx::query_as("SELECT provider, external_id FROM media_external_id WHERE media_id = $1")
                    .bind(id)
                    .fetch_all(&state.pool_ro)
                    .await
                    .unwrap_or_default();

            let mut ext_map = serde_json::Map::new();
            let mut canonical_id = format!("mf:{id}");
            for (p, e) in &ext_ids {
                let formatted = if p == "imdb" { e.clone() } else { format!("{p}:{e}") };
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
                "type": mtype.to_lowercase(),
                "poster": poster,
                "is_user_created": is_user,
                "is_own": creator_id == Some(user_id),
            }));
        }
    }

    Json(json!({"results": results, "total": results.len()})).into_response()
}

/// GET /api/v1/metadata/user/{media_id}
pub async fn get_user_metadata(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i64>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => return (StatusCode::UNAUTHORIZED, Json(json!({"detail": "Unauthorized"}))).into_response(),
    };

    let row: Option<(bool, Option<i64>)> =
        sqlx::query_as("SELECT is_public, created_by_user_id FROM media WHERE id = $1")
            .bind(media_id)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None);

    match row {
        None => return (StatusCode::NOT_FOUND, Json(json!({"detail": "Metadata not found"}))).into_response(),
        Some((is_public, creator_id)) => {
            if creator_id != Some(user_id) && !is_public {
                return (StatusCode::FORBIDDEN, Json(json!({"detail": "Access denied"}))).into_response();
            }
        }
    }

    let resp = media_row_to_json(&state.pool_ro, media_id, true).await;
    Json(resp).into_response()
}

/// PUT /api/v1/metadata/user/{media_id}
pub async fn update_user_metadata(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i64>,
    Json(body): Json<UserMediaUpdate>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => return (StatusCode::UNAUTHORIZED, Json(json!({"detail": "Unauthorized"}))).into_response(),
    };

    let creator_id: Option<Option<i64>> =
        sqlx::query_scalar("SELECT created_by_user_id FROM media WHERE id = $1")
            .bind(media_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);

    match creator_id {
        None => return (StatusCode::NOT_FOUND, Json(json!({"detail": "Metadata not found"}))).into_response(),
        Some(cid) if cid != Some(user_id) => {
            return (StatusCode::FORBIDDEN, Json(json!({"detail": "Can only update your own metadata"}))).into_response();
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

    let sql = format!("UPDATE media SET {} WHERE id = $1", updates.join(", "));
    let mut q = sqlx::query(&sql).bind(media_id);
    if let Some(ref v) = body.title { q = q.bind(v); }
    if let Some(ref v) = body.original_title { q = q.bind(v); }
    if let Some(v) = body.year { q = q.bind(v); }
    if let Some(ref v) = body.description { q = q.bind(v); }
    if let Some(ref v) = body.tagline { q = q.bind(v); }
    if let Some(v) = body.is_public { q = q.bind(v); }
    if let Some(v) = body.runtime_minutes { q = q.bind(v); }
    if let Some(ref v) = body.status { q = q.bind(v); }
    if let Some(ref v) = body.website { q = q.bind(v); }
    if let Some(ref v) = body.original_language { q = q.bind(v); }

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
            let genre_id: Option<i64> = sqlx::query_scalar(
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

    let resp = media_row_to_json(&state.pool, media_id, true).await;
    Json(resp).into_response()
}

/// DELETE /api/v1/metadata/user/{media_id}
pub async fn delete_user_metadata(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i64>,
    Query(params): Query<DeleteMediaQuery>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => return (StatusCode::UNAUTHORIZED, Json(json!({"detail": "Unauthorized"}))).into_response(),
    };

    let creator_id: Option<Option<i64>> =
        sqlx::query_scalar("SELECT created_by_user_id FROM media WHERE id = $1")
            .bind(media_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);

    match creator_id {
        None => return (StatusCode::NOT_FOUND, Json(json!({"detail": "Metadata not found"}))).into_response(),
        Some(cid) if cid != Some(user_id) => {
            return (StatusCode::FORBIDDEN, Json(json!({"detail": "Can only delete your own metadata"}))).into_response();
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
    Path(media_id): Path<i64>,
    Json(body): Json<SeasonAddRequest>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => return (StatusCode::UNAUTHORIZED, Json(json!({"detail": "Unauthorized"}))).into_response(),
    };

    let row: Option<(String, Option<i64>)> =
        sqlx::query_as("SELECT type::text, created_by_user_id FROM media WHERE id = $1")
            .bind(media_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);

    let (mtype, creator_id) = match row {
        None => return (StatusCode::NOT_FOUND, Json(json!({"detail": "Metadata not found"}))).into_response(),
        Some(r) => r,
    };

    if mtype.to_uppercase() != "SERIES" {
        return (StatusCode::BAD_REQUEST, Json(json!({"detail": "Can only add seasons to series"}))).into_response();
    }
    if creator_id != Some(user_id) {
        return (StatusCode::FORBIDDEN, Json(json!({"detail": "Can only modify your own metadata"}))).into_response();
    }

    let series_id: Option<i64> =
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
        return (StatusCode::CONFLICT, Json(json!({"detail": format!("Season {} already exists", body.season_number)}))).into_response();
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
    Path(media_id): Path<i64>,
    Json(body): Json<EpisodeAddRequest>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => return (StatusCode::UNAUTHORIZED, Json(json!({"detail": "Unauthorized"}))).into_response(),
    };

    let row: Option<(String, Option<i64>)> =
        sqlx::query_as("SELECT type::text, created_by_user_id FROM media WHERE id = $1")
            .bind(media_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);

    let (mtype, creator_id) = match row {
        None => return (StatusCode::NOT_FOUND, Json(json!({"detail": "Metadata not found"}))).into_response(),
        Some(r) => r,
    };

    if mtype.to_uppercase() != "SERIES" {
        return (StatusCode::BAD_REQUEST, Json(json!({"detail": "Can only add episodes to series"}))).into_response();
    }
    if creator_id != Some(user_id) {
        return (StatusCode::FORBIDDEN, Json(json!({"detail": "Can only modify your own metadata"}))).into_response();
    }

    let series_id: Option<i64> =
        sqlx::query_scalar("SELECT id FROM series_metadata WHERE media_id = $1")
            .bind(media_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);
    let series_id = match series_id {
        None => return StatusCode::INTERNAL_SERVER_ERROR.into_response(),
        Some(sid) => sid,
    };

    let season_id: Option<i64> =
        sqlx::query_scalar("SELECT id FROM season WHERE series_id = $1 AND season_number = $2")
            .bind(series_id)
            .bind(body.season_number)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);
    let season_id = match season_id {
        None => return (StatusCode::NOT_FOUND, Json(json!({"detail": format!("Season {} not found", body.season_number)}))).into_response(),
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

    let _ = sqlx::query(
        "UPDATE season SET episode_count = episode_count + $1 WHERE id = $2",
    )
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
    Path((media_id, episode_id)): Path<(i64, i64)>,
    Json(body): Json<EpisodeUpdateRequest>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => return (StatusCode::UNAUTHORIZED, Json(json!({"detail": "Unauthorized"}))).into_response(),
    };

    let creator_id: Option<Option<i64>> =
        sqlx::query_scalar("SELECT created_by_user_id FROM media WHERE id = $1")
            .bind(media_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);

    match creator_id {
        None => return (StatusCode::NOT_FOUND, Json(json!({"detail": "Metadata not found"}))).into_response(),
        Some(cid) if cid != Some(user_id) => {
            return (StatusCode::FORBIDDEN, Json(json!({"detail": "Can only modify your own metadata"}))).into_response();
        }
        _ => {}
    }

    let ep_exists: bool = sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM episode WHERE id = $1)")
        .bind(episode_id)
        .fetch_one(&state.pool)
        .await
        .unwrap_or(false);

    if !ep_exists {
        return (StatusCode::NOT_FOUND, Json(json!({"detail": "Episode not found"}))).into_response();
    }

    let mut updates = vec!["updated_at = NOW()".to_string()];
    let mut idx = 2i32;

    if body.title.is_some() { updates.push(format!("title = ${idx}")); idx += 1; }
    if body.overview.is_some() { updates.push(format!("overview = ${idx}")); idx += 1; }
    if body.air_date.is_some() { updates.push(format!("air_date = ${idx}::date")); idx += 1; }
    if body.runtime_minutes.is_some() { updates.push(format!("runtime_minutes = ${idx}")); idx += 1; }

    let sql = format!("UPDATE episode SET {} WHERE id = $1 RETURNING id, episode_number, title, overview, air_date::text, runtime_minutes, is_user_created, is_user_addition", updates.join(", "));
    let mut q = sqlx::query_as::<_, (i64, i32, String, Option<String>, Option<String>, Option<i32>, bool, bool)>(&sql).bind(episode_id);

    if let Some(ref v) = body.title { q = q.bind(v); }
    if let Some(ref v) = body.overview { q = q.bind(v); }
    if let Some(ref v) = body.air_date { q = q.bind(v); }
    if let Some(v) = body.runtime_minutes { q = q.bind(v); }

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
        Ok(None) => (StatusCode::NOT_FOUND, Json(json!({"detail": "Episode not found"}))).into_response(),
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
    Path((media_id, episode_id)): Path<(i64, i64)>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => return (StatusCode::UNAUTHORIZED, Json(json!({"detail": "Unauthorized"}))).into_response(),
    };

    let creator_id: Option<Option<i64>> =
        sqlx::query_scalar("SELECT created_by_user_id FROM media WHERE id = $1")
            .bind(media_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);

    match creator_id {
        None => return (StatusCode::NOT_FOUND, Json(json!({"detail": "Metadata not found"}))).into_response(),
        Some(cid) if cid != Some(user_id) => {
            return (StatusCode::FORBIDDEN, Json(json!({"detail": "Can only modify your own metadata"}))).into_response();
        }
        _ => {}
    }

    let season_id: Option<i64> =
        sqlx::query_scalar("SELECT season_id FROM episode WHERE id = $1")
            .bind(episode_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);

    if season_id.is_none() {
        return (StatusCode::NOT_FOUND, Json(json!({"detail": "Episode not found"}))).into_response();
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
        let _ = sqlx::query("UPDATE season SET episode_count = GREATEST(0, episode_count - 1) WHERE id = $1")
            .bind(sid)
            .execute(&state.pool)
            .await;
        let series_id: Option<i64> =
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
pub async fn delete_episode_admin(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path((media_id, episode_id)): Path<(i64, i64)>,
    Query(params): Query<DeleteEpisodeAdminQuery>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => return (StatusCode::UNAUTHORIZED, Json(json!({"detail": "Unauthorized"}))).into_response(),
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
        return (StatusCode::NOT_FOUND, Json(json!({"detail": "Metadata not found"}))).into_response();
    }

    let season_id: Option<i64> =
        sqlx::query_scalar("SELECT season_id FROM episode WHERE id = $1")
            .bind(episode_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);

    if season_id.is_none() {
        return (StatusCode::NOT_FOUND, Json(json!({"detail": "Episode not found"}))).into_response();
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
        let _ = sqlx::query("UPDATE season SET episode_count = GREATEST(0, episode_count - 1) WHERE id = $1")
            .bind(sid)
            .execute(&state.pool)
            .await;
        let series_id: Option<i64> =
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
    Path((media_id, season_number)): Path<(i64, i32)>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => return (StatusCode::UNAUTHORIZED, Json(json!({"detail": "Unauthorized"}))).into_response(),
    };

    let creator_id: Option<Option<i64>> =
        sqlx::query_scalar("SELECT created_by_user_id FROM media WHERE id = $1")
            .bind(media_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);

    match creator_id {
        None => return (StatusCode::NOT_FOUND, Json(json!({"detail": "Metadata not found"}))).into_response(),
        Some(cid) if cid != Some(user_id) => {
            return (StatusCode::FORBIDDEN, Json(json!({"detail": "Can only modify your own metadata"}))).into_response();
        }
        _ => {}
    }

    let series_id: Option<i64> =
        sqlx::query_scalar("SELECT id FROM series_metadata WHERE media_id = $1")
            .bind(media_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);
    let series_id = match series_id {
        None => return StatusCode::INTERNAL_SERVER_ERROR.into_response(),
        Some(sid) => sid,
    };

    let season_row: Option<(i64, i32)> =
        sqlx::query_as("SELECT id, episode_count FROM season WHERE series_id = $1 AND season_number = $2")
            .bind(series_id)
            .bind(season_number)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);

    let (season_id, ep_count) = match season_row {
        None => return (StatusCode::NOT_FOUND, Json(json!({"detail": format!("Season {} not found", season_number)}))).into_response(),
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
pub async fn delete_season_admin(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path((media_id, season_number)): Path<(i64, i32)>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => return (StatusCode::UNAUTHORIZED, Json(json!({"detail": "Unauthorized"}))).into_response(),
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
        return (StatusCode::NOT_FOUND, Json(json!({"detail": "Metadata not found"}))).into_response();
    }

    let series_id: Option<i64> =
        sqlx::query_scalar("SELECT id FROM series_metadata WHERE media_id = $1")
            .bind(media_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);
    let series_id = match series_id {
        None => return StatusCode::INTERNAL_SERVER_ERROR.into_response(),
        Some(sid) => sid,
    };

    let season_row: Option<(i64, i32)> =
        sqlx::query_as("SELECT id, episode_count FROM season WHERE series_id = $1 AND season_number = $2")
            .bind(series_id)
            .bind(season_number)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);

    let (season_id, ep_count) = match season_row {
        None => return (StatusCode::NOT_FOUND, Json(json!({"detail": format!("Season {} not found", season_number)}))).into_response(),
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

/// POST /api/v1/metadata/user/import/preview  (stub — preview only, no DB writes)
pub async fn preview_import_from_external(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> Response {
    let _user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => return (StatusCode::UNAUTHORIZED, Json(json!({"detail": "Unauthorized"}))).into_response(),
    };
    (
        StatusCode::NOT_IMPLEMENTED,
        Json(json!({"detail": "External import preview not implemented in Rust API"})),
    )
        .into_response()
}

/// POST /api/v1/metadata/user/import  (stub)
pub async fn import_from_external(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> Response {
    let _user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => return (StatusCode::UNAUTHORIZED, Json(json!({"detail": "Unauthorized"}))).into_response(),
    };
    (
        StatusCode::NOT_IMPLEMENTED,
        Json(json!({"detail": "External import not implemented in Rust API"})),
    )
        .into_response()
}
