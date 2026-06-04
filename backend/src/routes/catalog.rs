use std::sync::Arc;

use axum::{
    extract::{Path, State},
    http::StatusCode,
    response::{IntoResponse, Json},
};
use serde_json::json;

use crate::{
    cache, crypto,
    db::{self, catalog as db_catalog, MediaType},
    models::{
        stremio::{MetaPreview, Metas},
        user_data::{MdbListItem, UserData},
    },
    routes::delete_all_watchlist,
    scrapers::metadata::fetch_all_list_imdb_ids,
    scrapers::rpdb,
    state::AppState,
    util::retry,
};

const MDBLIST_BASE: &str = "https://api.mdblist.com";
const MDBLIST_BATCH_SIZE: i64 = 200;
const MDBLIST_PAGE_LIMIT: usize = 50;

/// Parse Stremio's "extra" path segment(s) from `rest`.
/// rest = "catalog_id.json" or "catalog_id/skip=100.json" etc.
fn parse_catalog_path(rest: &str) -> Option<(String, ExtraParams)> {
    let without_json = rest.trim_end_matches(".json");
    let parts: Vec<&str> = without_json.splitn(2, '/').collect();

    let catalog_id = parts[0].to_string();
    if catalog_id.is_empty() {
        return None;
    }

    let mut params = ExtraParams::default();
    if let Some(extras) = parts.get(1) {
        for segment in extras.split('/') {
            if let Some(v) = segment.strip_prefix("skip=") {
                params.skip = v.parse().unwrap_or(0);
            } else if let Some(v) = segment.strip_prefix("genre=") {
                params.genre = Some(v.to_string());
            } else if let Some(v) = segment.strip_prefix("search=") {
                params.search = Some(v.to_string());
            }
        }
    }
    Some((catalog_id, params))
}

#[derive(Default)]
struct ExtraParams {
    skip: i64,
    genre: Option<String>,
    search: Option<String>,
}

fn format_release_info(
    media_type: MediaType,
    year: Option<i32>,
    end_year: Option<i32>,
) -> Option<String> {
    match (media_type, year, end_year) {
        (MediaType::Series, Some(start), Some(end)) => Some(format!("{start}-{end}")),
        (MediaType::Series, Some(start), None) => Some(format!("{start}-")),
        (_, Some(y), _) => Some(y.to_string()),
        _ => None,
    }
}

fn preview_poster(
    poster_host_url: &str,
    media_type: db::MediaType,
    canonical_id: &str,
    db_poster: Option<String>,
) -> Option<String> {
    db_poster.or_else(|| {
        Some(format!(
            "{poster_host_url}/poster/{}/{}.jpg",
            media_type.as_wire(),
            canonical_id
        ))
    })
}

fn rows_to_metas(rows: Vec<db_catalog::CatalogRow>, poster_host_url: &str) -> Metas {
    let metas = rows
        .into_iter()
        .map(|r| {
            let id = r
                .imdb_id
                .filter(|s| !s.is_empty())
                .unwrap_or_else(|| format!("mf{}", r.media_id));
            let poster = preview_poster(poster_host_url, r.media_type, &id, r.poster_url);
            MetaPreview {
                id,
                media_type: r.media_type.as_wire().to_string(),
                name: r.title,
                release_info: format_release_info(r.media_type, r.year, r.end_year),
                poster,
                background: None,
                description: r.description,
            }
        })
        .collect();
    Metas { metas }
}

fn parse_watchlist_service<'a>(catalog_id: &'a str, media_type: &str) -> Option<&'a str> {
    if let Some(service) = catalog_id.strip_suffix("_watchlist_movies") {
        if media_type == "movie" {
            return Some(service);
        }
    } else if let Some(service) = catalog_id.strip_suffix("_watchlist_series") {
        if media_type == "series" {
            return Some(service);
        }
    }
    None
}

async fn handle_watchlist_catalog(
    state: &AppState,
    user_data: &UserData,
    media_type: &str,
    catalog_id: &str,
    service: &str,
    extra: &ExtraParams,
) -> axum::response::Response {
    let provider = match user_data.get_provider_by_name(service) {
        Some(p) if p.enable_watchlist_catalogs => p,
        _ => return Json(Metas { metas: vec![] }).into_response(),
    };

    let token = match provider.token.as_deref().filter(|t| !t.is_empty()) {
        Some(t) => t,
        None => return Json(Metas { metas: vec![] }).into_response(),
    };

    let hashes: Vec<String> = crate::providers::torrents::cache::get_user_hashes_cached(
        &state.http,
        &state.redis,
        service,
        token,
    )
    .await
    .into_iter()
    .collect();

    if hashes.is_empty() {
        return Json(Metas { metas: vec![] }).into_response();
    }

    let (sort, sort_dir) = user_data.catalog_sort(catalog_id);
    let nudity_excludes = user_data.nudity_filter.clone();
    let cert_excludes: Vec<String> = user_data
        .certification_filter
        .iter()
        .filter(|s| s.as_str() != "Disable")
        .cloned()
        .collect();

    let rows = db_catalog::get_watchlist_items(
        &state.pool_ro,
        media_type,
        &hashes,
        extra.skip,
        &nudity_excludes,
        &cert_excludes,
        &sort,
        &sort_dir,
    )
    .await;

    let mut metas = rows_to_metas(rows, &state.config.poster_host_url).metas;

    if media_type == "movie"
        && delete_all_watchlist::supports_delete_all(service)
        && !metas.is_empty()
    {
        metas.insert(
            0,
            delete_all_watchlist::delete_all_meta_preview(&state.config.poster_host_url, service),
        );
    }

    Json(Metas { metas }).into_response()
}

async fn fetch_mdblist_catalog(
    state: &AppState,
    user_data: &UserData,
    media_type: &str,
    catalog_id: &str,
    list: &MdbListItem,
    extra: &ExtraParams,
) -> Metas {
    if list.catalog_type != media_type {
        return Metas { metas: vec![] };
    }

    let api_key = match user_data.mdblist_api_key() {
        Some(k) => k,
        None => return Metas { metas: vec![] },
    };

    let nudity_excludes = user_data.nudity_filter.clone();
    let cert_excludes: Vec<String> = user_data
        .certification_filter
        .iter()
        .filter(|s| s.as_str() != "Disable")
        .cloned()
        .collect();

    if list.use_filters {
        let Some(mt) = MediaType::from_wire(media_type) else {
            return Metas { metas: vec![] };
        };

        let imdb_ids = fetch_all_list_imdb_ids(
            &state.http,
            &state.redis,
            api_key,
            list,
            extra.genre.as_deref(),
        )
        .await;

        if imdb_ids.is_empty() {
            return Metas { metas: vec![] };
        }

        let rows = db_catalog::get_mdblist_filtered_items(
            &state.pool_ro,
            mt,
            &imdb_ids,
            extra.skip,
            MDBLIST_PAGE_LIMIT as i64,
            &nudity_excludes,
            &cert_excludes,
        )
        .await;

        return rows_to_metas(rows, &state.config.poster_host_url);
    }

    let offset = (extra.skip / MDBLIST_BATCH_SIZE) * MDBLIST_BATCH_SIZE;
    let mut url = format!(
        "{MDBLIST_BASE}/lists/{}/items?apikey={api_key}&limit={MDBLIST_BATCH_SIZE}&offset={offset}&append_to_response=genre&sort={}&order={}",
        list.id, list.sort, list.order
    );
    if let Some(genre) = extra.genre.as_deref().filter(|g| !g.is_empty()) {
        url.push_str(&format!("&filter_genre={genre}"));
    }

    let resp = match retry::with_transport_retry("mdblist catalog", || state.http.get(&url).send())
        .await
    {
        Ok(r) if r.status().is_success() => r,
        Ok(r) if r.status() == reqwest::StatusCode::TOO_MANY_REQUESTS => {
            tracing::warn!("mdblist catalog [{catalog_id}]: HTTP 429 Too Many Requests");
            return Metas {
                metas: vec![MetaPreview {
                    id: "mdblist_rate_limited".to_string(),
                    media_type: media_type.to_string(),
                    name: "\u{26a0} MDBList Rate Limited".to_string(),
                    release_info: None,
                    poster: None,
                    background: None,
                    description: Some(
                        "MDBList API rate limit reached. Please wait a moment before scrolling."
                            .to_string(),
                    ),
                }],
            };
        }
        Ok(r) => {
            tracing::warn!("mdblist catalog [{catalog_id}]: HTTP {}", r.status());
            return Metas { metas: vec![] };
        }
        Err(e) => {
            tracing::warn!(
                error_kind = crate::util::http::transport_error_kind(&e),
                root_cause = crate::util::http::root_cause(&e),
                "mdblist catalog [{catalog_id}]: request failed: {e}"
            );
            return Metas { metas: vec![] };
        }
    };

    let data: serde_json::Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => {
            tracing::warn!("mdblist catalog [{catalog_id}]: parse error: {e}");
            return Metas { metas: vec![] };
        }
    };

    let key = if media_type == "series" {
        "shows"
    } else {
        "movies"
    };
    let items = match data[key].as_array() {
        Some(a) => a,
        None => return Metas { metas: vec![] },
    };

    let poster_host = &state.config.poster_host_url;
    let start_idx = (extra.skip % MDBLIST_BATCH_SIZE) as usize;
    let metas: Vec<MetaPreview> = items
        .iter()
        .filter_map(|item| {
            let imdb_id = item["imdb_id"].as_str()?;
            if !imdb_id.starts_with("tt") {
                return None;
            }
            let title = item["title"].as_str()?.to_string();
            let year = item["release_year"].as_i64().map(|y| y as i32);
            let release_info = format_release_info(
                if media_type == "series" {
                    MediaType::Series
                } else {
                    MediaType::Movie
                },
                year,
                None,
            );
            Some(MetaPreview {
                id: imdb_id.to_string(),
                media_type: media_type.to_string(),
                name: title,
                release_info,
                poster: Some(format!("{poster_host}/poster/{media_type}/{imdb_id}.jpg")),
                background: None,
                description: None,
            })
        })
        .skip(start_idx)
        .take(MDBLIST_PAGE_LIMIT)
        .collect();

    Metas { metas }
}

// ─── Shared dispatch ──────────────────────────────────────────────────────────

async fn handle_catalog(
    state: Arc<AppState>,
    user_data: UserData,
    media_type: &str,
    rest: &str,
) -> axum::response::Response {
    // Normalise legacy Stremio type aliases before hitting the DB enum.
    let media_type = match media_type {
        "shows" | "show" => "series",
        other => other,
    };

    let Some((catalog_id, extra)) = parse_catalog_path(rest) else {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error":"invalid catalog path"})),
        )
            .into_response();
    };

    // Watchlist catalogs: fetch debrid downloads and map to media rows (never cached).
    if let Some(service) = parse_watchlist_service(&catalog_id, media_type) {
        return handle_watchlist_catalog(
            &state,
            &user_data,
            media_type,
            &catalog_id,
            service,
            &extra,
        )
        .await;
    }

    // MDBList catalogs: fetch directly from MDBList API when filters are disabled.
    if catalog_id.starts_with("mdblist_") {
        if let Some(list) = user_data.mdblist_list_for_catalog(&catalog_id) {
            let metas =
                fetch_mdblist_catalog(&state, &user_data, media_type, &catalog_id, &list, &extra)
                    .await;
            return Json(metas).into_response();
        }
        return Json(Metas { metas: vec![] }).into_response();
    }

    let (sort, sort_dir) = user_data.catalog_sort(&catalog_id);
    let nudity_excludes = user_data.nudity_filter.clone();
    // Strip the "Disable" sentinel; an empty slice means no filter.
    let cert_excludes: Vec<String> = user_data
        .certification_filter
        .iter()
        .filter(|s| s.as_str() != "Disable")
        .cloned()
        .collect();

    // Build cache key: user-scoped for personal catalogs, shared for public ones.
    let is_personal = catalog_id.starts_with("my_library_");
    let cache_key: Option<String> = if is_personal {
        user_data.user_id.map(|uid| {
            format!(
                "catalog:{media_type}:{catalog_id}:{}:user:{uid}",
                extra.skip
            )
        })
    } else {
        let genre_part = extra.genre.as_deref().unwrap_or("");
        let search_part = extra.search.as_deref().unwrap_or("");
        let nudity_part = nudity_excludes.join(",");
        let cert_part = cert_excludes.join(",");
        Some(format!(
            "catalog:{media_type}:{catalog_id}:{}:{}:{}:{}:{}:{}:{}",
            extra.skip, genre_part, search_part, nudity_part, cert_part, sort, sort_dir,
        ))
    };

    // Check cache
    if let Some(ref key) = cache_key {
        if let Some(cached) = cache::get_json(&state.redis, key).await {
            if rpdb::needs_rpdb_poster_mutation(&user_data, media_type) {
                if let Ok(mut metas) = serde_json::from_value::<Metas>(cached.clone()) {
                    rpdb::apply_rpdb_posters(&mut metas, &user_data, media_type);
                    return Json(metas).into_response();
                }
            }
            return Json(cached).into_response();
        }
    }

    let rows = if let Some(ref q) = extra.search {
        db_catalog::search_metadata(
            &state.pool_ro,
            media_type,
            q,
            extra.skip,
            &nudity_excludes,
            &cert_excludes,
        )
        .await
    } else {
        db_catalog::get_catalog_items(
            &state.pool_ro,
            db_catalog::CatalogQuery {
                catalog_id: &catalog_id,
                media_type,
                skip: extra.skip,
                genre: extra.genre.as_deref(),
                nudity_excludes: &nudity_excludes,
                cert_excludes: &cert_excludes,
                sort: &sort,
                sort_dir: &sort_dir,
                user_id: user_data.user_id,
            },
        )
        .await
    };

    let mut metas = rows_to_metas(rows, &state.config.poster_host_url);
    rpdb::apply_rpdb_posters(&mut metas, &user_data, media_type);
    let response = serde_json::to_value(&metas).unwrap_or_else(|_| json!({"metas":[]}));

    if let Some(ref key) = cache_key {
        cache::set_json(&state.redis, key, &response, state.config.catalog_cache_ttl).await;
    }

    Json(response).into_response()
}

// ─── Route handlers ───────────────────────────────────────────────────────────

pub async fn public_catalog(
    Path((media_type, rest)): Path<(String, String)>,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    handle_catalog(state, UserData::default(), &media_type, &rest).await
}

pub async fn user_catalog(
    Path((secret_str, media_type, rest)): Path<(String, String, String)>,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    let raw = match crypto::resolve_user_data(
        &secret_str,
        &state.config.secret_key,
        &state.pool,
        &state.redis,
    )
    .await
    {
        Ok(v) => v,
        Err(e) => {
            tracing::debug!("catalog: {e}");
            return (
                axum::http::StatusCode::UNPROCESSABLE_ENTITY,
                axum::Json(serde_json::json!({"error": "Invalid user data"})),
            )
                .into_response();
        }
    };
    let user_data = serde_json::from_value::<UserData>(raw).unwrap_or_default();
    handle_catalog(state, user_data, &media_type, &rest)
        .await
        .into_response()
}
