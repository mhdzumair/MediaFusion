use std::sync::Arc;

use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    response::{IntoResponse, Json},
};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::{routes::stream, state::AppState};

const KODI_PAGE_SIZE: usize = 25;

#[derive(Deserialize)]
pub struct KodiQuery {
    #[serde(default = "default_page")]
    pub page: usize,
}

fn default_page() -> usize {
    1
}

// ─── Public (no secret) ────────────────────────────────────────────────────────

pub async fn movie(
    Path(video_id): Path<String>,
    Query(q): Query<KodiQuery>,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    let imdb_id = video_id.trim_end_matches(".json").to_string();
    dispatch(state, String::new(), imdb_id, "movie", None, None, q.page).await
}

pub async fn series(
    Path(video_id): Path<String>,
    Query(q): Query<KodiQuery>,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    let raw = video_id.trim_end_matches(".json");
    let parts: Vec<&str> = raw.splitn(3, ':').collect();
    if parts.len() != 3 || parts[0].is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "invalid video_id"})),
        )
            .into_response();
    }
    let imdb_id = parts[0].to_string();
    let season: i32 = parts[1].parse().unwrap_or(1);
    let episode: i32 = parts[2].parse().unwrap_or(1);
    dispatch(state, String::new(), imdb_id, "series", Some(season), Some(episode), q.page).await
}

// ─── Authenticated ─────────────────────────────────────────────────────────────

pub async fn user_movie(
    Path((secret_str, video_id)): Path<(String, String)>,
    Query(q): Query<KodiQuery>,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    let imdb_id = video_id.trim_end_matches(".json").to_string();
    dispatch(state, secret_str, imdb_id, "movie", None, None, q.page).await
}

pub async fn user_series(
    Path((secret_str, video_id)): Path<(String, String)>,
    Query(q): Query<KodiQuery>,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    let raw = video_id.trim_end_matches(".json");
    let parts: Vec<&str> = raw.splitn(3, ':').collect();
    if parts.len() != 3 || parts[0].is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "invalid video_id"})),
        )
            .into_response();
    }
    let imdb_id = parts[0].to_string();
    let season: i32 = parts[1].parse().unwrap_or(1);
    let episode: i32 = parts[2].parse().unwrap_or(1);
    dispatch(state, secret_str, imdb_id, "series", Some(season), Some(episode), q.page).await
}

// ─── Core dispatch ─────────────────────────────────────────────────────────────

async fn dispatch(
    state: Arc<AppState>,
    secret_str: String,
    imdb_id: String,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    page: usize,
) -> axum::response::Response {
    let page = page.max(1);
    let offset = (page - 1) * KODI_PAGE_SIZE;

    match stream::resolve(&state, &secret_str, &imdb_id, media_type, season, episode).await {
        Ok(streams) => {
            let total = streams.len();
            let page_streams: Vec<Value> = streams
                .into_iter()
                .skip(offset)
                .take(KODI_PAGE_SIZE)
                .collect();
            Json(json!({
                "streams": page_streams,
                "meta": {
                    "page": page,
                    "total": total,
                }
            }))
            .into_response()
        }
        Err(e) => {
            tracing::warn!("kodi_stream error imdb={imdb_id} type={media_type}: {e}");
            Json(json!({"streams": []})).into_response()
        }
    }
}
