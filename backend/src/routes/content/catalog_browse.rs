/// Catalog browsing endpoints — native Rust implementation.
///
/// Routes (prefix /api/v1/catalog):
///   GET  /available                         → get_available_catalogs
///   GET  /genres                            → get_genres
///   GET  /search                            → search_catalog
///   GET  /{catalog_type}                    → browse_catalog
///   GET  /{catalog_type}/{media_id}         → get_media_detail
///   GET  /{catalog_type}/{media_id}/streams → get_media_streams
///   POST /{catalog_type}/{media_id}/streams/{stream_id}/report → report_stream
use std::{collections::HashMap, sync::Arc};

use axum::{
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::Utc;
use hmac::{Hmac, KeyInit, Mac};
use serde::Deserialize;
use serde_json::json;
use sha2::Sha256;

use crate::{
    cache,
    db::{MediaType, StreamType, TorrentType},
    models::user_data::UserData,
    parser::{
        cap_streams, compare_sort_keys, filter_streams_by_preferences, torrent_sort_key,
        FilterContext,
    },
    routes::{
        content::stream_rows::{
            format_size, BrowseStreamRow, STREAM_BASE_COLS, STREAM_LINK_AGG_COLS,
        },
        user_library::extract_streaming_providers,
    },
    state::AppState,
};

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

// ─── Helpers ──────────────────────────────────────────────────────────────────

fn bool_opt_from_str<'de, D: serde::Deserializer<'de>>(d: D) -> Result<Option<bool>, D::Error> {
    let s: Option<String> = Option::deserialize(d)?;
    Ok(s.map(|v| !matches!(v.to_lowercase().as_str(), "false" | "0" | "no" | "")))
}

const VALID_CATALOG_TYPES: &[&str] = &["movie", "series", "tv", "events"];

fn unauthorized() -> Response {
    (
        StatusCode::UNAUTHORIZED,
        Json(json!({"detail": "Unauthorized"})),
    )
        .into_response()
}

fn bad_request(msg: &str) -> Response {
    (StatusCode::BAD_REQUEST, Json(json!({"detail": msg}))).into_response()
}

// ─── Query param structs ──────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct GenresQuery {
    pub catalog_type: Option<String>,
}

#[derive(Deserialize)]
pub struct SearchQuery {
    pub q: Option<String>,
    pub page: Option<i64>,
    pub page_size: Option<i64>,
}

#[derive(Deserialize)]
pub struct BrowseParams {
    pub catalog: Option<String>,
    pub genre: Option<String>,
    pub search: Option<String>,
    pub external_id: Option<String>,
    pub sort: Option<String>,
    pub sort_dir: Option<String>,
    pub page: Option<i64>,
    pub page_size: Option<i64>,
    #[serde(default, deserialize_with = "bool_opt_from_str")]
    pub has_streams: Option<bool>,
}

#[derive(Deserialize)]
pub struct StreamsQuery {
    pub season: Option<i32>,
    pub episode: Option<i32>,
    pub profile_id: Option<i32>,
    pub profile_uuid: Option<String>,
    pub provider: Option<String>,
    /// When set, the response always includes this stream (if compatible with the selected provider)
    /// and, for series, season/episode are resolved from the stream's file links when omitted.
    pub stream_id: Option<i32>,
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// GET /api/v1/catalog/available
pub async fn get_available_catalogs(State(state): State<Arc<AppState>>) -> Response {
    const CACHE_KEY: &str = "catalog:available";
    const CACHE_TTL: u64 = 600; // 10 minutes

    if let Some(cached) = cache::get_json(&state.redis, CACHE_KEY).await {
        return Json(cached).into_response();
    }

    let rows: Vec<(String, String, MediaType)> = sqlx::query_as(
        r#"SELECT DISTINCT c.name, COALESCE(c.display_name, c.name) as display_name, m.type
           FROM catalog c
           JOIN media_catalog_link mcl ON mcl.catalog_id = c.id
           JOIN media m ON m.id = mcl.media_id
           ORDER BY display_name"#,
    )
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    let mut movies: Vec<serde_json::Value> = Vec::new();
    let mut series: Vec<serde_json::Value> = Vec::new();
    let mut tv: Vec<serde_json::Value> = Vec::new();
    let mut sports: Vec<serde_json::Value> = Vec::new();

    for (name, display_name, media_type) in rows {
        let entry = json!({"name": name, "display_name": display_name});
        match media_type {
            MediaType::Movie => movies.push(entry),
            MediaType::Series => series.push(entry),
            MediaType::Tv => tv.push(entry),
            MediaType::Events => sports.push(entry),
        }
    }

    let result = json!({
        "movies": movies,
        "series": series,
        "tv": tv,
        "sports": sports,
    });
    cache::set_json(&state.redis, CACHE_KEY, &result, CACHE_TTL).await;
    Json(result).into_response()
}

/// GET /api/v1/catalog/genres
pub async fn get_genres(
    State(state): State<Arc<AppState>>,
    Query(params): Query<GenresQuery>,
) -> Response {
    let catalog_type = params
        .catalog_type
        .as_deref()
        .unwrap_or("movie")
        .to_uppercase();
    let cache_key = format!("genres:{catalog_type}");

    if let Some(cached) = cache::get_json(&state.redis, &cache_key).await {
        return Json(cached).into_response();
    }

    let media_type_wire = catalog_type.to_ascii_lowercase();
    if crate::db::MediaType::from_wire(&media_type_wire).is_none() {
        return Json(json!([])).into_response();
    }

    // Read directly from genre_media_type — no media scan required.
    // is_hidden = false is the single filter (replaces the old ADULT_GENRE_NAMES list).
    let rows: Vec<(i32, String)> = sqlx::query_as(
        "SELECT g.id, g.name
         FROM   genre_media_type gmt
         JOIN   genre g ON g.id = gmt.genre_id
         WHERE  gmt.media_type = $1 AND gmt.is_hidden = false
         ORDER  BY g.name",
    )
    .bind(&media_type_wire)
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    let genres: Vec<serde_json::Value> = rows
        .into_iter()
        .map(|(id, name)| json!({"id": id, "name": name}))
        .collect();

    let result = json!(genres);
    cache::set_json(&state.redis, &cache_key, &result, 3600).await;
    Json(result).into_response()
}

/// GET /api/v1/catalog/search
pub async fn search_catalog(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<SearchQuery>,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return unauthorized();
    }

    let q = match params.q {
        Some(ref s) if !s.trim().is_empty() => format!("%{}%", s.trim()),
        _ => return bad_request("q parameter is required"),
    };

    let page = params.page.unwrap_or(1).max(1);
    let page_size = params.page_size.unwrap_or(20).clamp(1, 100);
    let offset = (page - 1) * page_size;

    let kf = { state.keyword_filters.read().unwrap().clone() };
    let kf_frag = kf.keyword_title_block_fragment();

    let count_sql = format!(
        "SELECT COUNT(*) FROM media m WHERE (m.title ILIKE $1) AND m.adult = false{kf_frag}"
    );
    let total: i64 = sqlx::query_scalar::<_, i64>(&count_sql)
        .bind(&q)
        .fetch_one(&state.pool_ro)
        .await
        .unwrap_or(0);

    let list_sql = format!(
        r#"SELECT m.id, m.title, m.type, m.year
           FROM media m
           WHERE (m.title ILIKE $1) AND m.adult = false{kf_frag}
           ORDER BY m.title
           LIMIT $2 OFFSET $3"#
    );
    let list_q = sqlx::query_as::<_, (i32, String, MediaType, Option<i32>)>(&list_sql)
        .bind(&q)
        .bind(page_size)
        .bind(offset);
    let rows: Vec<(i32, String, MediaType, Option<i32>)> =
        list_q.fetch_all(&state.pool_ro).await.unwrap_or_default();

    let items: Vec<serde_json::Value> = rows
        .into_iter()
        .map(|(id, title, mtype, year)| {
            json!({
                "id": id,
                "title": title,
                "type": mtype.as_wire(),
                "year": year,
                "poster": null,
                "background": null,
                "description": null,
                "genres": [],
                "imdb_rating": null,
                "external_ids": {},
            })
        })
        .collect();

    Json(json!({
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": (offset + page_size) < total,
    }))
    .into_response()
}

/// GET /api/v1/catalog/{catalog_type}
pub async fn browse_catalog(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(catalog_type): Path<String>,
    Query(params): Query<BrowseParams>,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return unauthorized();
    }

    if !VALID_CATALOG_TYPES.contains(&catalog_type.as_str()) {
        return bad_request("Invalid catalog_type. Must be one of: movie, series, tv, events");
    }

    let page = params.page.unwrap_or(1).max(1);
    let page_size = params.page_size.unwrap_or(20).clamp(1, 100);

    // Resolve external_id filter to a media_id (matches Python catalog browse)
    let external_id_media: Option<crate::db::MediaId> = if let Some(ref eid) = params.external_id {
        match crate::db::get_media_id_by_external_id(&state.pool_ro, eid, Some(&catalog_type)).await
        {
            Ok(v) => v,
            Err(e) => {
                tracing::error!("browse_catalog external_id lookup: {e}");
                return (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({"detail": "Failed to resolve external ID"})),
                )
                    .into_response();
            }
        }
    } else {
        None
    };

    if params.external_id.is_some() && external_id_media.is_none() {
        return Json(json!({
            "items": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "has_more": false,
        }))
        .into_response();
    }

    // Read keyword filter once (before any await, clone out to drop the lock).
    let kf = { state.keyword_filters.read().unwrap().clone() };
    let kf_ver = kf.version_tag();

    // Full-response cache (2 min TTL) — browse pages rarely change within a session.
    // Embed keyword-filter version so keyword changes invalidate cached pages.
    let browse_cache_key = format!(
        "catalog:browse:{}:{}:{}:{}:{}:{}:{}:{}:{}:{}:kf{}",
        catalog_type,
        params.sort.as_deref().unwrap_or("latest"),
        params.sort_dir.as_deref().unwrap_or("desc"),
        page,
        page_size,
        params.genre.as_deref().unwrap_or(""),
        params.catalog.as_deref().unwrap_or(""),
        params.has_streams.unwrap_or(true),
        params.search.as_deref().unwrap_or(""),
        params.external_id.as_deref().unwrap_or(""),
        kf_ver,
    );
    if let Some(cached) = cache::get_json(&state.redis, &browse_cache_key).await {
        return Json(cached).into_response();
    }
    let offset = (page - 1) * page_size;

    // Build ORDER BY clause matching Python's sort logic exactly
    let sort = params.sort.as_deref().unwrap_or("latest");
    let sort_dir = match params.sort_dir.as_deref().unwrap_or("desc") {
        "asc" => "ASC",
        _ => "DESC",
    };
    let nulls = if sort_dir == "ASC" {
        "NULLS FIRST"
    } else {
        "NULLS LAST"
    };
    let order_clause = match sort {
        "popular" => format!(
            "m.popularity {sort_dir} {nulls}, m.total_streams {sort_dir} {nulls}, m.last_stream_added {sort_dir} {nulls}, m.id ASC"
        ),
        "rating" => format!(
            "(SELECT mr2.rating FROM media_rating mr2 JOIN rating_provider rp2 ON rp2.id = mr2.rating_provider_id WHERE mr2.media_id = m.id AND rp2.name = 'imdb' LIMIT 1) {sort_dir} {nulls}, m.total_streams {sort_dir} {nulls}, m.id ASC"
        ),
        "year" => format!("m.year {sort_dir} {nulls}, m.id ASC"),
        "title" => format!("m.title {sort_dir}, m.id ASC"),
        "release_date" => format!("COALESCE(m.release_date, m.end_date) {sort_dir} {nulls}, m.id ASC"),
        _ => format!("m.last_stream_added {sort_dir} {nulls}, m.id ASC"), // latest
    };

    // Parse catalog_type into a native enum — VALID_CATALOG_TYPES guard ran above, so
    // from_wire should always succeed here; early-return empty on any unexpected value.
    let Some(catalog_media_type) = MediaType::from_wire(&catalog_type) else {
        return Json(json!({
            "items": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "has_more": false,
        }))
        .into_response();
    };

    // Use EXISTS subqueries for catalog/genre filtering to avoid DISTINCT + ORDER BY conflicts.
    // $1 is always the catalog media type enum bind.
    let mut where_parts: Vec<String> = vec![
        "m.type = $1".to_string(),
        "m.adult = false".to_string(),
        "m.is_blocked = false".to_string(),
    ];

    // Default: only released content (matches Python's include_upcoming=false default)
    if catalog_type != "tv" {
        where_parts.push(
            "(m.release_date <= CURRENT_DATE OR m.status = 'released' OR (m.release_date IS NULL AND (m.year IS NULL OR m.year <= EXTRACT(YEAR FROM CURRENT_DATE)::int)))".to_string()
        );
    }

    // Default: only media with available streams (matches Python's has_streams=true default)
    if params.has_streams.unwrap_or(true) {
        where_parts.push("m.total_streams > 0".to_string());
    }
    // bind_idx starts at 1 — $1 is already reserved for catalog_media_type above.
    let mut bind_idx: i32 = 1;

    let mut catalog_name_bind: Option<String> = None;
    let mut genre_name_bind: Option<String> = None;
    let mut search_bind: Option<String> = None;

    if let Some(ref cat) = params.catalog {
        bind_idx += 1;
        where_parts.push(format!(
            "EXISTS (SELECT 1 FROM media_catalog_link mcl JOIN catalog c ON c.id = mcl.catalog_id WHERE mcl.media_id = m.id AND c.name = ${bind_idx})"
        ));
        catalog_name_bind = Some(cat.clone());
    }

    if let Some(ref genre) = params.genre {
        bind_idx += 1;
        // Also require that the (genre, media_type) pairing is not hidden so a crafted URL
        // cannot browse a genre that was hidden by an admin.
        where_parts.push(format!(
            "EXISTS (\
                SELECT 1 FROM media_genre_link mgl \
                JOIN genre g ON g.id = mgl.genre_id \
                JOIN genre_media_type gmt ON gmt.genre_id = g.id \
                    AND gmt.media_type = lower(m.type::text) \
                    AND gmt.is_hidden = false \
                WHERE mgl.media_id = m.id AND g.name = ${bind_idx}\
            )"
        ));
        genre_name_bind = Some(genre.clone());
    }

    if let Some(ref search) = params.search {
        bind_idx += 1;
        where_parts.push(format!("(m.title ILIKE ${bind_idx})"));
        search_bind = Some(format!("%{}%", search));
    }

    let mut external_id_media_bind: Option<crate::db::MediaId> = None;
    if let Some(mid) = external_id_media {
        bind_idx += 1;
        where_parts.push(format!("m.id = ${bind_idx}"));
        external_id_media_bind = Some(mid);
    }

    let kw_frag = kf.keyword_title_block_fragment();
    if !kw_frag.is_empty() {
        where_parts.push("m.is_keyword_blocked = false".to_string());
    }

    let where_clause = where_parts.join(" AND ");

    let limit_idx = bind_idx + 1;
    let offset_idx = bind_idx + 2;

    let count_sql = format!("SELECT COUNT(*) FROM media m WHERE {where_clause}");

    let list_sql = format!(
        r#"SELECT m.id, m.title, m.type, m.year, m.description,
               (SELECT url FROM media_image WHERE media_id = m.id AND image_type = 'poster' AND is_primary = true LIMIT 1) as poster,
               (SELECT url FROM media_image WHERE media_id = m.id AND image_type = 'background' AND is_primary = true LIMIT 1) as background,
               (SELECT mr.rating FROM media_rating mr JOIN rating_provider rp ON rp.id = mr.rating_provider_id WHERE mr.media_id = m.id AND rp.name = 'imdb' LIMIT 1) as imdb_rating,
               m.last_stream_added
           FROM media m
           WHERE {where_clause}
           ORDER BY {order_clause}
           LIMIT ${limit_idx} OFFSET ${offset_idx}"#
    );

    // COUNT result cache — keyed on the full filter tuple (changes rarely).
    // Include keyword-filter version so count cache is invalidated on keyword changes.
    let count_cache_key = format!(
        "catalog:count:{}:{}:{}:{}:{}:{}:kf{}",
        catalog_type,
        genre_name_bind.as_deref().unwrap_or(""),
        catalog_name_bind.as_deref().unwrap_or(""),
        search_bind.as_deref().unwrap_or(""),
        params.has_streams.unwrap_or(true),
        external_id_media_bind
            .map(|id| id.to_string())
            .unwrap_or_default(),
        kf_ver,
    );

    let total: i64 = if let Some(cached_count) = cache::get_json(&state.redis, &count_cache_key)
        .await
        .and_then(|v| v.as_i64())
    {
        cached_count
    } else {
        let mut count_q = sqlx::query_scalar::<_, i64>(&count_sql);
        count_q = count_q.bind(catalog_media_type);
        if let Some(ref v) = catalog_name_bind {
            count_q = count_q.bind(v.clone());
        }
        if let Some(ref v) = genre_name_bind {
            count_q = count_q.bind(v.clone());
        }
        if let Some(ref v) = search_bind {
            count_q = count_q.bind(v.clone());
        }
        if let Some(mid) = external_id_media_bind {
            count_q = count_q.bind(mid);
        }
        let n = count_q.fetch_one(&state.pool_ro).await.unwrap_or(0);
        // Cache count for 5 minutes — counts change slowly
        cache::set_json(
            &state.redis,
            &count_cache_key,
            &serde_json::Value::Number(n.into()),
            300,
        )
        .await;
        n
    };

    // Build and execute list query
    let mut list_q = sqlx::query_as::<
        _,
        (
            i32,
            String,
            MediaType,
            Option<i32>,
            Option<String>,
            Option<String>,
            Option<String>,
            Option<f64>,
            Option<chrono::DateTime<chrono::Utc>>,
        ),
    >(&list_sql);
    list_q = list_q.bind(catalog_media_type);
    if let Some(ref v) = catalog_name_bind {
        list_q = list_q.bind(v.clone());
    }
    if let Some(ref v) = genre_name_bind {
        list_q = list_q.bind(v.clone());
    }
    if let Some(ref v) = search_bind {
        list_q = list_q.bind(v.clone());
    }
    if let Some(mid) = external_id_media_bind {
        list_q = list_q.bind(mid);
    }
    list_q = list_q.bind(page_size).bind(offset);

    let rows = list_q.fetch_all(&state.pool_ro).await.unwrap_or_default();

    if rows.is_empty() {
        let empty_val = json!({
            "items": [],
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_more": false,
        });
        cache::set_json(&state.redis, &browse_cache_key, &empty_val, 120).await;
        return Json(empty_val).into_response();
    }

    // Load genres and external_ids for all fetched items in one batch each
    let media_ids: Vec<i32> = rows.iter().map(|r| r.0).collect();
    #[allow(clippy::type_complexity)]
    let (genre_rows, ext_id_rows): (Vec<(i32, String)>, Vec<(i32, String, String)>) = tokio::join!(
        async {
            sqlx::query_as(
                "SELECT mgl.media_id, g.name FROM genre g JOIN media_genre_link mgl ON mgl.genre_id = g.id WHERE mgl.media_id = ANY($1)",
            )
            .bind(&media_ids)
            .fetch_all(&state.pool_ro)
            .await
            .unwrap_or_default()
        },
        async {
            sqlx::query_as(
                "SELECT media_id, provider, external_id FROM media_external_id WHERE media_id = ANY($1)",
            )
            .bind(&media_ids)
            .fetch_all(&state.pool_ro)
            .await
            .unwrap_or_default()
        }
    );

    let mut genres_map: std::collections::HashMap<i32, Vec<String>> =
        std::collections::HashMap::new();
    for (mid, gname) in genre_rows {
        genres_map.entry(mid).or_default().push(gname);
    }

    let mut ext_ids_map: std::collections::HashMap<
        i32,
        serde_json::Map<String, serde_json::Value>,
    > = std::collections::HashMap::new();
    for (mid, provider, ext_id) in ext_id_rows {
        ext_ids_map
            .entry(mid)
            .or_default()
            .insert(provider, serde_json::Value::String(ext_id));
    }

    let items: Vec<serde_json::Value> = rows
        .into_iter()
        .map(
            |(
                id,
                title,
                mtype,
                year,
                description,
                poster,
                background,
                imdb_rating,
                _last_stream_added,
            )| {
                let genres = genres_map.get(&id).cloned().unwrap_or_default();
                let external_ids = ext_ids_map.get(&id).cloned().unwrap_or_default();
                json!({
                    "id": id,
                    "title": title,
                    "type": mtype.as_wire(),
                    "year": year,
                    "poster": poster,
                    "background": background,
                    "description": description,
                    "genres": genres,
                    "imdb_rating": imdb_rating,
                    "external_ids": external_ids,
                })
            },
        )
        .collect();

    let response_val = json!({
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": (offset + page_size) < total,
    });
    cache::set_json(&state.redis, &browse_cache_key, &response_val, 120).await;
    Json(response_val).into_response()
}

/// GET /api/v1/catalog/{catalog_type}/{media_id}
#[allow(clippy::type_complexity)]
pub async fn get_media_detail(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path((catalog_type, media_id)): Path<(String, i32)>,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return unauthorized();
    }

    if !VALID_CATALOG_TYPES.contains(&catalog_type.as_str()) {
        return bad_request("Invalid catalog_type");
    }

    // Fetch main media row
    let media_row: Option<(
        i32,
        String,
        MediaType,
        Option<i32>,
        Option<String>,
        Option<String>,
        Option<String>,
        Option<String>,
        bool,
        Option<String>,
        Option<String>,
        Option<String>,
        bool,
        Option<String>,
    )> = sqlx::query_as(
        r#"SELECT m.id, m.title, m.type, m.year, m.description, m.status,
                      m.runtime_minutes::text, m.original_language, m.adult,
                      m.release_date::text, m.end_date::text, m.tagline,
                      m.is_blocked, m.block_reason
               FROM media m WHERE m.id = $1"#,
    )
    .bind(media_id)
    .fetch_optional(&state.pool_ro)
    .await
    .unwrap_or(None);

    let row = match media_row {
        Some(r) => r,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Media not found"})),
            )
                .into_response();
        }
    };

    let (
        id,
        title,
        mtype,
        year,
        description,
        status,
        runtime_minutes,
        original_language,
        _adult,
        release_date,
        end_date,
        tagline,
        is_blocked,
        block_reason,
    ) = row;

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

    // Parallel queries
    let (genres, catalogs, ext_ids, poster, background, imdb_rating) = tokio::join!(
        async {
            sqlx::query_as::<_, (String,)>(
                "SELECT g.name FROM genre g JOIN media_genre_link mgl ON mgl.genre_id = g.id WHERE mgl.media_id = $1 ORDER BY g.name",
            )
            .bind(media_id)
            .fetch_all(&state.pool_ro)
            .await
            .unwrap_or_default()
        },
        async {
            sqlx::query_as::<_, (String,)>(
                "SELECT c.name FROM catalog c JOIN media_catalog_link mcl ON mcl.catalog_id = c.id WHERE mcl.media_id = $1",
            )
            .bind(media_id)
            .fetch_all(&state.pool_ro)
            .await
            .unwrap_or_default()
        },
        async {
            sqlx::query_as::<_, (String, String)>(
                "SELECT provider, external_id FROM media_external_id WHERE media_id = $1",
            )
            .bind(media_id)
            .fetch_all(&state.pool_ro)
            .await
            .unwrap_or_default()
        },
        async {
            sqlx::query_scalar::<_, String>(
                "SELECT url FROM media_image WHERE media_id = $1 AND image_type = 'poster' AND is_primary = true LIMIT 1",
            )
            .bind(media_id)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None)
        },
        async {
            sqlx::query_scalar::<_, String>(
                "SELECT url FROM media_image WHERE media_id = $1 AND image_type = 'background' AND is_primary = true LIMIT 1",
            )
            .bind(media_id)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None)
        },
        async {
            sqlx::query_scalar::<_, f64>(
                "SELECT mr.rating FROM media_rating mr JOIN rating_provider rp ON rp.id = mr.rating_provider_id WHERE mr.media_id = $1 AND rp.name = 'imdb' LIMIT 1",
            )
            .bind(media_id)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None)
        },
    );

    let genre_list: Vec<String> = genres.into_iter().map(|(g,)| g).collect();
    let catalog_list: Vec<String> = catalogs.into_iter().map(|(c,)| c).collect();

    let mut external_ids_map = serde_json::Map::new();
    for (provider, external_id) in ext_ids {
        external_ids_map.insert(provider, json!(external_id));
    }

    // For series, load seasons and episodes
    let seasons_value: serde_json::Value = if mtype == MediaType::Series {
        // Fetch all seasons ordered by season_number
        let season_rows: Vec<(i32, i32)> = sqlx::query_as(
            r#"SELECT sn.id, sn.season_number
               FROM series_metadata sm
               JOIN season sn ON sn.series_id = sm.id
               WHERE sm.media_id = $1
               ORDER BY sn.season_number"#,
        )
        .bind(media_id)
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default();

        if season_rows.is_empty() {
            json!([])
        } else {
            // Fetch all episodes + thumbnails for all seasons in one query
            let season_ids: Vec<i32> = season_rows.iter().map(|(id, _)| *id).collect();
            let episode_rows: Vec<(i32, i32, i32, String, Option<String>, Option<String>, bool, bool, Option<String>)> =
                sqlx::query_as(
                    r#"SELECT e.id, e.season_id, e.episode_number, e.title, e.overview,
                              e.air_date::text,
                              e.is_user_created, e.is_user_addition,
                              (SELECT ei.url FROM episode_image ei WHERE ei.episode_id = e.id AND ei.is_primary = true LIMIT 1) AS thumbnail
                       FROM episode e
                       WHERE e.season_id = ANY($1)
                       ORDER BY e.season_id, e.episode_number"#,
                )
                .bind(&season_ids)
                .fetch_all(&state.pool_ro)
                .await
                .unwrap_or_default();

            // Group episodes by season_id
            let mut eps_by_season: std::collections::HashMap<i32, Vec<serde_json::Value>> =
                std::collections::HashMap::new();
            for (
                ep_id,
                season_id,
                ep_num,
                ep_title,
                overview,
                air_date,
                is_user_created,
                is_user_addition,
                thumbnail,
            ) in episode_rows
            {
                eps_by_season.entry(season_id).or_default().push(json!({
                    "id": ep_id,
                    "episode_number": ep_num,
                    "title": ep_title,
                    "overview": overview,
                    "released": air_date,
                    "thumbnail": thumbnail,
                    "is_user_created": is_user_created,
                    "is_user_addition": is_user_addition,
                }));
            }

            let seasons: Vec<serde_json::Value> = season_rows
                .into_iter()
                .map(|(sn_id, sn_num)| {
                    let episodes = eps_by_season.remove(&sn_id).unwrap_or_default();
                    json!({
                        "season_number": sn_num,
                        "episodes": episodes,
                    })
                })
                .collect();
            json!(seasons)
        }
    } else {
        json!(null)
    };

    Json(json!({
        "id": id,
        "title": title,
        "type": mtype.as_wire(),
        "year": year,
        "description": description,
        "status": status,
        "runtime_minutes": runtime_minutes,
        "original_language": original_language,
        "tagline": tagline,
        "release_date": release_date,
        "end_date": end_date,
        "poster": poster,
        "background": background,
        "genres": genre_list,
        "catalogs": catalog_list,
        "external_ids": external_ids_map,
        "imdb_rating": imdb_rating,
        "seasons": seasons_value,
        "is_blocked": is_blocked,
        "block_reason": block_reason,
    }))
    .into_response()
}

/// GET /api/v1/catalog/{catalog_type}/{media_id}/streams
pub async fn get_media_streams(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path((catalog_type, media_id)): Path<(String, i32)>,
    Query(params): Query<StreamsQuery>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            tracing::debug!("get_media_streams: auth failed for media_id={media_id}");
            return unauthorized();
        }
    };

    if !VALID_CATALOG_TYPES.contains(&catalog_type.as_str()) {
        return bad_request("Invalid catalog_type");
    }

    let mut effective_season = params.season;
    let mut effective_episode = params.episode;

    if catalog_type == "series" {
        if let Some(sid) = params.stream_id {
            let ep_row: Option<(i32, i32)> = sqlx::query_as(
                r#"SELECT fml.season_number, fml.episode_number
                   FROM stream_file sf
                   JOIN file_media_link fml ON fml.file_id = sf.id
                   WHERE sf.stream_id = $1 AND fml.media_id = $2
                     AND fml.season_number IS NOT NULL
                     AND fml.episode_number IS NOT NULL
                   ORDER BY fml.season_number, fml.episode_number
                   LIMIT 1"#,
            )
            .bind(sid)
            .bind(media_id)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None);
            if let Some((s, e)) = ep_row {
                effective_season = Some(s);
                effective_episode = Some(e);
            }
        }
        if effective_season.is_none() || effective_episode.is_none() {
            return bad_request("Season and episode parameters are required for series");
        }
    }

    // Load streaming providers from the user's profile
    type ProfileRecord = (i32, Option<serde_json::Value>, Option<String>);
    let profile_row: Option<ProfileRecord> = if let Some(pid) = params.profile_id {
        match sqlx::query_as::<_, ProfileRecord>(
            "SELECT id, config, encrypted_secrets FROM user_profiles WHERE id = $1 AND user_id = $2",
        )
        .bind(pid)
        .bind(user_id as i32)
        .fetch_optional(&state.pool_ro)
        .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("get_media_streams profile fetch pid={pid} user={user_id}: {e}");
                None
            }
        }
    } else {
        match sqlx::query_as::<_, ProfileRecord>(
            "SELECT id, config, encrypted_secrets FROM user_profiles WHERE user_id = $1 AND is_default = true LIMIT 1",
        )
        .bind(user_id as i32)
        .fetch_optional(&state.pool_ro)
        .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("get_media_streams default profile fetch user={user_id}: {e}");
                None
            }
        }
    };

    let default_title_tmpl = "{addon.name} {if stream.type = torrent}🧲 {service.shortName} {if service.cached}⚡️{else}⏳{/if}{elif stream.type = usenet}📰 {service.shortName}{elif stream.type = telegram}📱{elif stream.type = youtube}▶️{elif stream.type = http}🌐{else}🔗{/if} {if stream.resolution}{stream.resolution}{/if}";
    let default_desc_tmpl = "{if stream.hdr_formats}🎨 {stream.hdr_formats|join('|')} {/if}{if stream.quality}📺 {stream.quality} {/if}{if stream.codec}🎞️ {stream.codec} {/if}{if stream.audio_formats}🎵 {stream.audio_formats|join('|')} {/if}{if stream.channels}🔊 {stream.channels|join(' ')}{/if}\n{if stream.size > 0}📦 {stream.size|bytes} {/if}{if stream.seeders > 0}👤 {stream.seeders}{/if}\n{if stream.languages}🌐 {stream.languages|join(' + ')}{/if}\n🔗 {stream.source}{if stream.uploader} | 🧑‍💻 {stream.uploader}{/if}";

    let mut profile_user_data: Option<UserData> = None;

    let (
        profile_id_val,
        streaming_providers,
        selected_provider,
        provider_token,
        selected_stremthru_store,
        title_tmpl,
        desc_tmpl,
    ) = if let Some((pid, cfg, enc)) = profile_row {
        let mut config = cfg.unwrap_or(json!({}));
        // Decrypt secrets and merge provider tokens into config
        if let Some(enc_str) = enc.as_deref().filter(|s| !s.is_empty()) {
            let secrets =
                crate::crypto::profile::decrypt_secrets(enc_str, &state.config.secret_key);
            crate::crypto::profile::merge_secrets(&mut config, &secrets);
        }
        profile_user_data = serde_json::from_value::<UserData>(config.clone()).ok();
        let providers = extract_streaming_providers(&config);
        tracing::debug!(
            "get_media_streams profile={pid} providers_count={}",
            providers.len()
        );
        let selected = params
            .provider
            .as_deref()
            .filter(|p| {
                providers
                    .iter()
                    .any(|sp| sp.get("service").and_then(|v| v.as_str()) == Some(p))
            })
            .map(str::to_string)
            .or_else(|| {
                providers.first().and_then(|sp| {
                    sp.get("service")
                        .and_then(|v| v.as_str())
                        .map(str::to_string)
                })
            });
        // Extract token for selected provider from merged config
        let token = selected.as_deref().and_then(|svc| {
            config
                .get("sps")
                .or_else(|| config.get("streaming_providers"))
                .and_then(|v| v.as_array())
                .and_then(|arr| {
                    arr.iter().find(|sp| {
                        sp.get("sv")
                            .or_else(|| sp.get("service"))
                            .and_then(|v| v.as_str())
                            == Some(svc)
                    })
                })
                .and_then(|sp| {
                    sp.get("tk")
                        .or_else(|| sp.get("token"))
                        .and_then(|v| v.as_str())
                        .map(str::to_string)
                })
        });
        let selected_stremthru_store = selected.as_deref().and_then(|svc| {
            config
                .get("sps")
                .or_else(|| config.get("streaming_providers"))
                .and_then(|v| v.as_array())
                .and_then(|arr| {
                    arr.iter().find(|sp| {
                        sp.get("sv")
                            .or_else(|| sp.get("service"))
                            .and_then(|v| v.as_str())
                            == Some(svc)
                    })
                })
                .and_then(|sp| {
                    sp.get("stsn")
                        .or_else(|| sp.get("stremthru_store_name"))
                        .and_then(|v| v.as_str())
                        .map(str::to_string)
                })
        });
        // Read stream_template (alias "st") from profile config
        let st = config.get("st").or_else(|| config.get("stream_template"));
        let title = st
            .and_then(|t| t.get("t").or_else(|| t.get("title")))
            .and_then(|v| v.as_str())
            .map(str::to_string)
            .unwrap_or_else(|| default_title_tmpl.to_string());
        let desc = st
            .and_then(|t| t.get("d").or_else(|| t.get("description")))
            .and_then(|v| v.as_str())
            .map(str::to_string)
            .unwrap_or_else(|| default_desc_tmpl.to_string());
        (
            pid,
            providers,
            selected,
            token,
            selected_stremthru_store,
            title,
            desc,
        )
    } else {
        tracing::debug!(
            "get_media_streams: profile not found for user={user_id} profile_id={:?}",
            params.profile_id
        );
        (
            0i32,
            vec![],
            params.provider.clone(),
            None,
            None,
            default_title_tmpl.to_string(),
            default_desc_tmpl.to_string(),
        )
    };

    // Build the Stremio-compatible secret_str from the UUID supplied by the frontend.
    // Format is "U-{uuid}" — same as the manifest URL route uses.
    let secret_str: String = params
        .profile_uuid
        .as_deref()
        .filter(|u| !u.is_empty())
        .map(|u| format!("U-{u}"))
        .unwrap_or_default();

    // Extract sort preferences from profile UserData (or use defaults)
    let ud = profile_user_data.unwrap_or_default();
    let sorting_priority = ud.sorting_priority();
    let language_sorting = ud.language_sorting_list();
    let selected_resolutions = ud.effective_selected_resolutions();
    let quality_filter = if ud.quality_filter.is_empty() {
        crate::parser::default_quality_filter_groups()
    } else {
        ud.quality_filter.clone()
    };
    let season = effective_season;
    let episode = effective_episode;

    // Guard: don't serve streams for blocked or keyword-blocked media.
    {
        let blocked: bool = sqlx::query_scalar::<_, bool>(
            "SELECT (is_blocked OR is_keyword_blocked) FROM media WHERE id = $1",
        )
        .bind(media_id)
        .fetch_optional(&state.pool_ro)
        .await
        .unwrap_or(None)
        .unwrap_or(false);

        if blocked {
            return Json(json!({ "streams": [] })).into_response();
        }
    }

    tracing::debug!(
        "get_media_streams: fetching streams for {catalog_type}/{media_id} season={season:?} episode={episode:?} stream_id={:?}",
        params.stream_id
    );

    let stream_rows: Vec<BrowseStreamRow> = if catalog_type == "series" {
        let season = season.unwrap();
        let episode = episode.unwrap();
        sqlx::query_as(&format!(
            r#"SELECT DISTINCT ON (s.id)
            {STREAM_BASE_COLS},
            sf.filename,
            COALESCE(ts.total_size, sf.size) AS file_size,
            ts.info_hash,
            ys.video_id AS yt_id,
            {STREAM_LINK_AGG_COLS},
            ts.created_at
           FROM stream s
           JOIN stream_file sf ON sf.stream_id = s.id
           JOIN file_media_link fml ON fml.file_id = sf.id
              AND fml.media_id = $1
              AND fml.season_number = $2
              AND fml.episode_number = $3
           LEFT JOIN torrent_stream ts ON ts.stream_id = s.id
           LEFT JOIN youtube_stream ys ON ys.stream_id = s.id
           WHERE s.is_active = true
             AND s.is_blocked = false
           ORDER BY s.id"#
        ))
        .bind(media_id)
        .bind(season)
        .bind(episode)
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_else(|e| {
            tracing::error!(
                "get_media_streams series query failed for media_id={media_id} S{season}E{episode}: {e}"
            );
            vec![]
        })
    } else {
        sqlx::query_as(&format!(
            r#"SELECT
            {STREAM_BASE_COLS},
            (SELECT sf.filename FROM stream_file sf WHERE sf.stream_id = s.id LIMIT 1) AS filename,
            COALESCE(ts.total_size, sml.file_size) AS file_size,
            ts.info_hash,
            ys.video_id AS yt_id,
            {STREAM_LINK_AGG_COLS},
            ts.created_at
           FROM stream s
           JOIN stream_media_link sml ON sml.stream_id = s.id
           LEFT JOIN torrent_stream ts ON ts.stream_id = s.id
           LEFT JOIN youtube_stream ys ON ys.stream_id = s.id
           WHERE sml.media_id = $1
             AND s.is_active = true
             AND s.is_blocked = false"#
        ))
        .bind(media_id)
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_else(|e| {
            tracing::error!("get_media_streams query failed for media_id={media_id}: {e}");
            vec![]
        })
    };

    tracing::debug!(
        "get_media_streams: found {} rows for media_id={media_id}",
        stream_rows.len()
    );

    // Step 1: check Redis debrid_cache:{service} (global per service)
    let mut cached_hashes: HashMap<String, bool> = if let Some(ref svc) = selected_provider {
        let cache_service = crate::providers::torrents::cache_federation::cache_service_name(
            svc,
            selected_stremthru_store.as_deref(),
        );
        let hashes: Vec<String> = stream_rows
            .iter()
            .filter_map(|r| r.info_hash.clone())
            .collect();
        cache::get_debrid_cache_status_federated(
            &state.redis,
            Some(&state.http),
            &cache_service,
            svc,
            &hashes,
            state.config.sync_debrid_cache_streams,
            &state.config.mediafusion_url,
        )
        .await
    } else {
        HashMap::new()
    };

    // Step 2: for hashes not found in Redis, call the live provider API
    if let (Some(ref svc), Some(ref tok)) = (&selected_provider, &provider_token) {
        let cache_service = crate::providers::torrents::cache_federation::cache_service_name(
            svc,
            selected_stremthru_store.as_deref(),
        );
        let uncached: Vec<String> = stream_rows
            .iter()
            .filter_map(|r| r.info_hash.as_deref())
            .filter(|h| !cached_hashes.get(*h).copied().unwrap_or(false))
            .map(str::to_string)
            .collect();
        if !uncached.is_empty() {
            let live = crate::providers::torrents::cache::live_check(
                &state.http,
                &state.redis,
                svc,
                &cache_service,
                tok,
                &uncached,
                media_id,
                state.config.store_stremthru_magnet_cache,
            )
            .await;
            for (hash, is_cached) in live {
                if is_cached {
                    cached_hashes.insert(hash, true);
                }
            }
        }
    }

    let addon_name = &state.config.addon_name;

    // Determine service context for selected provider
    let service_short_names: std::collections::HashMap<&str, &str> = [
        ("realdebrid", "RD"),
        ("alldebrid", "AD"),
        ("premiumize", "PM"),
        ("debridlink", "DL"),
        ("torbox", "TB"),
        ("offcloud", "OC"),
        ("seedr", "SR"),
        ("stremthru", "ST"),
        ("pikpak", "PP"),
        ("easydebrid", "ED"),
    ]
    .into_iter()
    .collect();

    let service_name = selected_provider.as_deref().unwrap_or("p2p");
    let service_short = service_short_names
        .get(service_name)
        .copied()
        .unwrap_or("P2P");

    // Build (sort_ctx, output_json) pairs so we can sort before returning
    let mut stream_pairs: Vec<(serde_json::Value, serde_json::Value)> = stream_rows
        .into_iter()
        .map(|r| {
            let stream_type = r.stream_type.as_wire().to_lowercase();

            let is_cached = r.info_hash.as_deref()
                .and_then(|h| cached_hashes.get(h).copied())
                .unwrap_or(false);

            let audio_arr: Vec<serde_json::Value> = r.audio_formats.as_deref()
                .map(|s| s.split('|').map(|x| json!(x)).collect())
                .unwrap_or_default();
            let channels_arr: Vec<serde_json::Value> = r.channels.as_deref()
                .map(|s| s.split('|').map(|x| json!(x)).collect())
                .unwrap_or_default();
            let hdr_arr: Vec<serde_json::Value> = r.hdr_formats.as_deref()
                .map(|s| s.split('|').map(|x| json!(x)).collect())
                .unwrap_or_default();
            let lang_arr_vals: Vec<serde_json::Value> = r.languages.as_deref()
                .map(|s| s.split(" + ").map(|x| json!(x)).collect())
                .unwrap_or_default();

            let resolution_upper = r.resolution.as_deref().map(|s| s.to_uppercase()).unwrap_or_default();
            let file_size_val = r.file_size.unwrap_or(0);
            let seeders_val = r.seeders.unwrap_or(0);

            // Sort context: fields torrent_sort_key reads (size as numeric, resolution original case)
            let mut sort_ctx = json!({
                "_id": r.id,
                "name": r.name,
                "resolution": r.resolution,
                "quality": r.quality,
                "size": file_size_val,
                "file_size": file_size_val,
                "seeders": seeders_val,
                "languages": lang_arr_vals,
                "hdr_formats": hdr_arr,
                "cached": is_cached,
                "created_at": r.created_at.map(|dt| dt.to_rfc3339()),
            });
            if r.stream_type == StreamType::Torrent {
                if let Some(ref h) = r.info_hash {
                    sort_ctx["info_hash"] = json!(h);
                    sort_ctx["torrent_type"] = json!(TorrentType::Public.as_wire());
                }
            }

            // Template context for name/description rendering
            let stream_ctx = json!({
                "name": r.name,
                "filename": r.filename,
                "type": stream_type,
                "resolution": if resolution_upper.is_empty() { json!(null) } else { json!(resolution_upper) },
                "quality": r.quality,
                "codec": r.codec,
                "bit_depth": r.bit_depth,
                "audio_formats": audio_arr,
                "channels": channels_arr,
                "hdr_formats": hdr_arr,
                "languages": lang_arr_vals,
                "size": file_size_val,
                "seeders": seeders_val,
                "source": r.source,
                "release_group": r.release_group,
                "uploader": r.uploader,
                "cached": is_cached,
                "folderSize": file_size_val,
            });

            let service_ctx = json!({
                "name": service_name,
                "shortName": service_short,
                "cached": is_cached,
            });

            let addon_ctx = json!({ "name": addon_name });

            let ctx = json!({
                "stream": stream_ctx,
                "service": service_ctx,
                "addon": addon_ctx,
            });

            let display_name = crate::template::render(&title_tmpl, &ctx);
            let description = crate::template::render(&desc_tmpl, &ctx);
            let size_str = if file_size_val > 0 { format_size(file_size_val) } else { String::new() };

            let audio_out = if audio_arr.is_empty() { json!(null) } else { json!(audio_arr) };
            let channels_out = if channels_arr.is_empty() { json!(null) } else { json!(channels_arr) };
            let hdr_out = if hdr_arr.is_empty() { json!(null) } else { json!(hdr_arr) };
            let lang_out = if lang_arr_vals.is_empty() { json!([]) } else { json!(lang_arr_vals) };

            let rd_blocked = selected_provider.as_deref() == Some("realdebrid")
                && r.stream_type == StreamType::Torrent
                && {
                    let check = r
                        .filename
                        .as_deref()
                        .filter(|s| !s.is_empty())
                        .unwrap_or(r.name.as_str());
                    crate::routes::stream::is_rd_blocked_filename(
                        check,
                        &state.config.rd_blocked_substrings,
                        &state.config.rd_blocked_dot_pairs,
                    )
                };

            // Build playback URL for torrent streams when a provider is configured
            let playback_url = if rd_blocked {
                None
            } else if !secret_str.is_empty() {
                if let (Some(ref svc), Some(ref hash)) = (&selected_provider, &r.info_hash) {
                    if r.stream_type == StreamType::Torrent {
                        let filename = r.filename.as_deref().unwrap_or("");
                        let base = match (params.season, params.episode) {
                            (Some(s), Some(e)) => format!(
                                "{}/streaming_provider/{}/playback/{}/{}/{}/{}",
                                state.config.host_url, secret_str, svc, hash, s, e
                            ),
                            _ => format!(
                                "{}/streaming_provider/{}/playback/{}/{}",
                                state.config.host_url, secret_str, svc, hash
                            ),
                        };
                        if filename.is_empty() {
                            Some(base)
                        } else {
                            Some(format!("{}/{}", base, urlencoding::encode(filename)))
                        }
                    } else {
                        None
                    }
                } else {
                    None
                }
            } else {
                None
            };

            let output = json!({
                "id": r.id,
                "info_hash": r.info_hash,
                "yt_id": r.yt_id,
                "ytId": r.yt_id,
                "url": playback_url,
                "name": display_name,
                "description": description,
                "stream_name": r.name,
                "stream_type": stream_type,
                "resolution": r.resolution,
                "quality": r.quality,
                "codec": r.codec,
                "bit_depth": r.bit_depth,
                "audio_formats": audio_out,
                "channels": channels_out,
                "hdr_formats": hdr_out,
                "source": r.source,
                "languages": lang_out,
                "size": size_str,
                "size_bytes": r.file_size,
                "seeders": r.seeders,
                "uploader": r.uploader,
                "release_group": r.release_group,
                "cached": is_cached,
                "rd_blocked": rd_blocked,
                "is_remastered": r.is_remastered,
                "is_upscaled": r.is_upscaled,
                "is_proper": r.is_proper,
                "is_repack": r.is_repack,
                "is_extended": r.is_extended,
                "is_complete": r.is_complete,
                "is_dubbed": r.is_dubbed,
                "is_subbed": r.is_subbed,
                "filename": r.filename,
                "duration_seconds": serde_json::Value::Null,
                "votes": serde_json::Value::Null,
                "episode_links": serde_json::Value::Null,
            });

            (sort_ctx, output)
        })
        .collect();

    // Filter by what the selected provider can actually handle.
    // Rules:
    //   torrent  → only if provider is torrent-capable or p2p (no provider)
    //   usenet   → only if provider is usenet-capable
    //   other    → always shown (http, telegram, youtube, acestream, etc.)
    let svc = selected_provider.as_deref().unwrap_or("p2p");
    let can_torrent = svc == "p2p" || crate::routes::stream::TORRENT_CAPABLE.contains(&svc);
    let can_usenet = crate::routes::stream::USENET_CAPABLE.contains(&svc);

    if !can_torrent || !can_usenet {
        stream_pairs.retain(|(_, out)| {
            match out
                .get("stream_type")
                .and_then(|v| v.as_str())
                .unwrap_or("")
            {
                "torrent" => can_torrent,
                "usenet" => can_usenet,
                _ => true,
            }
        });
    }

    // Keep provider-compatible outputs for stream_id deep-link pinning (bypasses preference/cap only).
    let provider_compatible_outputs: std::collections::HashMap<i32, serde_json::Value> =
        stream_pairs
            .iter()
            .filter_map(|(ctx, out)| {
                ctx.get("_id")
                    .and_then(|x| x.as_i64())
                    .map(|id| (id as i32, out.clone()))
            })
            .collect();

    let allow_public_usenet = state.config.is_scrap_from_public_usenet_indexers;
    let kf = { state.keyword_filters.read().map(|g| g.clone()).unwrap_or_default() };
    let filter_ctx = FilterContext {
        user_data: &ud,
        season,
        episode,
        primary_provider: ud.get_primary_provider(),
        is_usenet: false,
        allow_public_usenet,
        keyword_filters: &kf,
    };

    let sort_rows: Vec<serde_json::Value> = stream_pairs.iter().map(|(a, _)| a.clone()).collect();
    let filtered_sort = filter_streams_by_preferences(sort_rows, &filter_ctx);
    let capped_sort = cap_streams(
        filtered_sort,
        ud.max_streams_per_resolution,
        ud.effective_max_streams(),
    );
    let keep_ids: std::collections::HashSet<i32> = capped_sort
        .iter()
        .filter_map(|v| v.get("_id").and_then(|x| x.as_i64()).map(|i| i as i32))
        .collect();
    stream_pairs.retain(|(ctx, _)| {
        ctx.get("_id")
            .and_then(|x| x.as_i64())
            .is_some_and(|id| keep_ids.contains(&(id as i32)))
    });

    if !sorting_priority.is_empty() {
        stream_pairs.sort_by(|(a, _), (b, _)| {
            let ka = torrent_sort_key(
                a,
                &sorting_priority,
                &selected_resolutions,
                &quality_filter,
                &language_sorting,
                &cached_hashes,
                season,
                episode,
            );
            let kb = torrent_sort_key(
                b,
                &sorting_priority,
                &selected_resolutions,
                &quality_filter,
                &language_sorting,
                &cached_hashes,
                season,
                episode,
            );
            compare_sort_keys(&ka, &kb)
        });
    }

    let mut streams: Vec<serde_json::Value> =
        stream_pairs.into_iter().map(|(_, out)| out).collect();

    if let Some(pin_id) = params.stream_id {
        let already_present = streams
            .iter()
            .any(|s| s.get("id").and_then(|v| v.as_i64()) == Some(pin_id as i64));
        if !already_present {
            if let Some(pinned) = provider_compatible_outputs.get(&pin_id) {
                streams.insert(0, pinned.clone());
            }
        }
    }

    Json(json!({
        "streams": streams,
        "season": season,
        "episode": episode,
        "resolved_season": if catalog_type == "series" { season } else { None },
        "resolved_episode": if catalog_type == "series" { episode } else { None },
        "web_playback_enabled": true,
        "streaming_providers": streaming_providers,
        "selected_provider": selected_provider,
        "profile_id": profile_id_val,
    }))
    .into_response()
}

/// POST /api/v1/catalog/{catalog_type}/{media_id}/streams/{stream_id}/report
pub async fn report_stream(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path((_catalog_type, _media_id, _stream_id)): Path<(String, i32, i32)>,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return unauthorized();
    }

    Json(json!({"message": "Report received"})).into_response()
}
