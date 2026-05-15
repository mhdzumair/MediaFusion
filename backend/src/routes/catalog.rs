use std::sync::Arc;

use axum::{
    extract::{Path, State},
    http::StatusCode,
    response::{IntoResponse, Json},
};
use serde_json::json;

use crate::{
    cache, crypto,
    db::catalog as db_catalog,
    models::{
        stremio::{MetaPreview, Metas},
        user_data::UserData,
    },
    state::AppState,
};

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

fn preview_poster(
    host_url: &str,
    media_type: &str,
    media_id: i64,
    db_poster: Option<String>,
) -> Option<String> {
    db_poster.or_else(|| Some(format!("{host_url}/poster/{media_type}/mf{media_id}.jpg")))
}

fn rows_to_metas(rows: Vec<db_catalog::CatalogRow>, host_url: &str) -> Metas {
    let metas = rows
        .into_iter()
        .map(|r| {
            let id = r
                .imdb_id
                .filter(|s| !s.is_empty())
                .unwrap_or_else(|| format!("mf{}", r.media_id));
            let poster = preview_poster(host_url, &r.media_type, r.media_id, r.poster_url);
            MetaPreview {
                id,
                media_type: r.media_type,
                name: r.title,
                release_info: r.year.map(|y| y.to_string()),
                poster,
                description: r.description,
            }
        })
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
    let Some((catalog_id, extra)) = parse_catalog_path(rest) else {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error":"invalid catalog path"})),
        )
            .into_response();
    };

    // Watchlist catalogs stay in Python → return 404 so nginx falls back
    if catalog_id.ends_with("_watchlist_movies") || catalog_id.ends_with("_watchlist_series") {
        return StatusCode::NOT_FOUND.into_response();
    }

    let (sort, sort_dir) = user_data.catalog_sort(&catalog_id);
    let nudity_excludes = user_data.nudity_filter.clone();

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
        Some(format!(
            "catalog:{media_type}:{catalog_id}:{}:{}:{}:{}:{}:{}",
            extra.skip, genre_part, search_part, nudity_part, sort, sort_dir,
        ))
    };

    // Check cache
    if let Some(ref key) = cache_key {
        if let Some(cached) = cache::get_json(&state.redis, key).await {
            return Json(cached).into_response();
        }
    }

    let rows = if let Some(ref q) = extra.search {
        db_catalog::search_metadata(&state.pool_ro, media_type, q, extra.skip, &nudity_excludes)
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
                sort: &sort,
                sort_dir: &sort_dir,
                user_id: user_data.user_id,
            },
        )
        .await
    };

    let metas = rows_to_metas(rows, &state.config.host_url);
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
    let user_data = serde_json::from_value::<UserData>(
        crypto::resolve_user_data(
            &secret_str,
            &state.config.secret_key,
            &state.pool,
            &state.redis,
        )
        .await,
    )
    .unwrap_or_default();
    handle_catalog(state, user_data, &media_type, &rest).await
}
