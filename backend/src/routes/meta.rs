use std::sync::Arc;

use axum::{
    extract::{Path, State},
    http::StatusCode,
    response::{IntoResponse, Json},
};
use serde_json::json;

use crate::{
    cache, crypto,
    db::{self, meta as db_meta},
    models::{
        stremio::{Meta, MetaItem, Video},
        user_data::UserData,
    },
    routes::delete_all_watchlist,
    state::AppState,
};

// ─── Shared builder ───────────────────────────────────────────────────────────

async fn build_meta(state: &AppState, media_type: &str, meta_id: &str) -> Option<Meta> {
    let row = db_meta::get_media_meta(&state.pool_ro, meta_id, media_type).await?;
    let id = row.media_id;

    let (genres, cast) = tokio::join!(
        db_meta::get_genres(&state.pool_ro, id),
        db_meta::get_cast(&state.pool_ro, id),
    );

    let canonical_id = row
        .imdb_id
        .as_deref()
        .filter(|s| !s.is_empty())
        .map(str::to_owned)
        .or_else(|| {
            row.tmdb_id
                .as_deref()
                .filter(|s| !s.is_empty())
                .map(|t| format!("tmdb:{t}"))
        })
        .unwrap_or_else(|| format!("mf{id}"));

    let media_type_wire = row.media_type.as_wire();
    let poster = row.poster_url.or_else(|| {
        Some(format!(
            "{}/poster/{media_type_wire}/{canonical_id}.jpg",
            state.config.poster_host_url
        ))
    });

    let release_info = match (row.media_type, row.year, row.end_year) {
        (db::MediaType::Series, Some(start), Some(end)) => Some(format!("{start}-{end}")),
        (db::MediaType::Series, Some(start), None) => Some(format!("{start}-")),
        (_, Some(y), _) => Some(y.to_string()),
        _ => None,
    };

    let runtime = row.runtime_minutes.map(|r| format!("{r} min"));

    let videos = if row.media_type == db::MediaType::Series {
        let eps = db_meta::get_episodes(&state.pool_ro, id).await;
        eps.into_iter()
            .map(|e| {
                let ep_meta_id = format!("{canonical_id}:{}:{}", e.season_number, e.episode_number);
                let released = e
                    .air_date
                    .map(|d| format!("{d}T00:00:00.000Z"))
                    .unwrap_or_else(|| format!("{}-01-01T00:00:00.000Z", row.year.unwrap_or(2000)));
                Video {
                    id: ep_meta_id,
                    title: e
                        .ep_title
                        .unwrap_or_else(|| format!("Episode {}", e.episode_number)),
                    released,
                    overview: e.overview,
                    thumbnail: e.thumbnail_url,
                    season: e.season_number,
                    episode: e.episode_number,
                }
            })
            .collect()
    } else {
        vec![]
    };

    let imdb_rating = row.imdb_rating.map(|r| r.to_string());

    Some(Meta {
        id: canonical_id,
        media_type: media_type_wire.to_string(),
        name: row.title,
        release_info,
        description: row.description,
        poster,
        background: row.background_url,
        logo: row.logo_url,
        runtime,
        website: row.website,
        language: row.language,
        country: row.country,
        genres,
        cast,
        imdb_rating,
        videos,
        links: None,
    })
}

// ─── Route handlers ───────────────────────────────────────────────────────────

async fn serve_meta(
    state: Arc<AppState>,
    user_data: UserData,
    media_type: &str,
    raw_id: &str,
) -> axum::response::Response {
    let meta_id = raw_id.trim_end_matches(".json");

    if media_type == "movie" {
        if let Some(service) = delete_all_watchlist::parse_service(meta_id) {
            return delete_all_watchlist::delete_all_meta_response(&state, &user_data, service);
        }
    }

    let cache_key = format!("meta:{media_type}:{meta_id}");
    let ttl = state.config.meta_cache_ttl;

    if let Some(cached) = cache::get_json(&state.redis, &cache_key).await {
        return Json(cached).into_response();
    }

    let mut meta = build_meta(&state, media_type, meta_id).await;
    if meta.is_none() {
        if let Some((provider, ext_id)) = crate::scrapers::metadata::parse_import_meta_id(meta_id) {
            let ctx = crate::scrapers::metadata::FetchCtx {
                tmdb_api_key: state.config.tmdb_api_key.as_deref(),
                tvdb_api_key: state.config.tvdb_api_key.as_deref(),
                mdblist_api_key: state.config.mdblist_api_key.as_deref(),
                trakt_client_id: state.config.trakt_client_id.as_deref(),
                trakt_client_secret: state.config.trakt_client_secret.as_deref(),
                cinemeta_fallback: state.config.imdb_cinemeta_fallback_enabled,
            };
            let is_series = media_type == "series";
            if let Some(normalized) = crate::scrapers::metadata::fetch_normalized(
                &state.http,
                &ctx,
                provider,
                &ext_id,
                is_series,
            )
            .await
            {
                if crate::db::store_media(
                    &state.pool,
                    &normalized,
                    crate::db::StoreMediaOpts::default(),
                )
                .await
                .is_ok()
                {
                    meta = build_meta(&state, media_type, meta_id).await;
                }
            }
        }
    }

    let Some(meta) = meta else {
        return StatusCode::NOT_FOUND.into_response();
    };

    let item = MetaItem { meta };
    let v = serde_json::to_value(&item).unwrap_or_else(|_| json!({}));
    cache::set_json(&state.redis, &cache_key, &v, ttl).await;
    Json(v).into_response()
}

pub async fn public_meta(
    Path((media_type, raw_id)): Path<(String, String)>,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    serve_meta(state, UserData::default(), &media_type, &raw_id).await
}

pub async fn user_meta(
    Path((secret_str, media_type, raw_id)): Path<(String, String, String)>,
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
            tracing::debug!("meta: {e}");
            return (
                axum::http::StatusCode::UNPROCESSABLE_ENTITY,
                axum::Json(serde_json::json!({"error": "Invalid user data"})),
            )
                .into_response();
        }
    };
    let user_data = serde_json::from_value::<UserData>(raw).unwrap_or_default();
    serve_meta(state, user_data, &media_type, &raw_id).await.into_response()
}
