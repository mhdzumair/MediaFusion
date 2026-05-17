use std::sync::Arc;

use axum::{
    body::Body,
    extract::{Path, State},
    http::{header, StatusCode},
    response::{IntoResponse, Response},
};
use tracing::warn;

use crate::{cache, poster::AnnotateParams, state::AppState};

pub async fn handler(
    Path((media_type, id_jpg)): Path<(String, String)>,
    State(state): State<Arc<AppState>>,
) -> Response {
    let id = id_jpg.trim_end_matches(".jpg");
    let cache_key = format!("{media_type}_{id}.jpg");

    // Warm path: serve cached bytes from Redis
    if let Some(bytes) = cache::get_bytes(&state.redis, &cache_key).await {
        return jpeg_response(bytes);
    }

    // Events are stored in Redis, not in Postgres
    if media_type == "events" {
        return serve_event_poster(&state, id, &cache_key).await;
    }

    // Cold path: DB lookup → optional sports fallback → fetch → annotate → cache
    if let Some(meta) = resolve_poster_meta(&state, id, &media_type).await {
        let poster_url = match meta.poster_url {
            Some(ref u) if !u.is_empty() => Some(u.clone()),
            // is_add_title = true: use full sports fallback (includes Other Sports catchall).
            _ if meta.is_add_title => resolve_sports_poster_url(&state, id).await,
            // No stored poster: try a strict genre-matched sports poster (no catchall)
            // so items with sports genres get a relevant poster even without is_add_title.
            None => resolve_sports_poster_strict(&state, id).await,
            _ => None,
        };

        if let Some(url) = poster_url {
            return fetch_annotate_cache(&state, &cache_key, &url, meta).await;
        }
    }

    StatusCode::NOT_FOUND.into_response()
}

// ─── Events (Redis-stored) ────────────────────────────────────────────────────

async fn serve_event_poster(state: &AppState, event_id: &str, cache_key: &str) -> Response {
    let redis_key = format!("events:{event_id}");
    let Some(data) = cache::get_json(&state.redis, &redis_key).await else {
        return StatusCode::NOT_FOUND.into_response();
    };

    let poster_url = data
        .get("poster")
        .and_then(|v| v.as_str())
        .map(str::to_string);
    let title = data
        .get("title")
        .and_then(|v| v.as_str())
        .map(str::to_string);
    let imdb_rating = data
        .get("imdb_rating")
        .and_then(|v| v.as_f64())
        .map(|f| f as f32);
    let is_add_title = data
        .get("is_add_title_to_poster")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);

    let Some(url) = poster_url else {
        return StatusCode::NOT_FOUND.into_response();
    };

    let meta = PosterMeta {
        poster_url: Some(url.clone()),
        imdb_rating,
        title,
        is_add_title,
    };
    fetch_annotate_cache(state, cache_key, &url, meta).await
}

// ─── DB resolution ────────────────────────────────────────────────────────────

struct PosterMeta {
    poster_url: Option<String>,
    imdb_rating: Option<f32>,
    title: Option<String>,
    is_add_title: bool,
}

async fn resolve_poster_meta(state: &AppState, id: &str, media_type: &str) -> Option<PosterMeta> {
    type Row = (Option<String>, Option<f64>, Option<String>, Option<bool>);

    let row: Option<Row> = if let Some(num_str) = id.strip_prefix("mf") {
        let internal_id: i32 = num_str.parse().ok()?;
        sqlx::query_as(
            r#"
            SELECT
                mi.url,
                mr.rating,
                m.title,
                m.is_add_title_to_poster
            FROM media m
            LEFT JOIN LATERAL (
                SELECT url FROM media_image
                WHERE media_id = m.id AND image_type = 'poster' AND is_primary = true
                LIMIT 1
            ) mi ON true
            LEFT JOIN LATERAL (
                SELECT r.rating FROM media_rating r
                JOIN rating_provider rp ON rp.id = r.rating_provider_id
                WHERE r.media_id = m.id AND rp.name = 'imdb'
                LIMIT 1
            ) mr ON true
            WHERE m.id = $1 AND m.type = upper($2)::mediatype
            LIMIT 1
            "#,
        )
        .bind(internal_id)
        .bind(media_type)
        .fetch_optional(&state.pool_ro)
        .await
        .unwrap_or_else(|e| {
            warn!("poster meta mf{internal_id}: {e}");
            None
        })
    } else {
        sqlx::query_as(
            r#"
            SELECT
                mi.url,
                mr.rating,
                m.title,
                m.is_add_title_to_poster
            FROM media m
            JOIN media_external_id meid ON meid.media_id = m.id
            LEFT JOIN LATERAL (
                SELECT url FROM media_image
                WHERE media_id = m.id AND image_type = 'poster' AND is_primary = true
                LIMIT 1
            ) mi ON true
            LEFT JOIN LATERAL (
                SELECT r.rating FROM media_rating r
                JOIN rating_provider rp ON rp.id = r.rating_provider_id
                WHERE r.media_id = m.id AND rp.name = 'imdb'
                LIMIT 1
            ) mr ON true
            WHERE meid.external_id = $1
              AND m.type = upper($2)::mediatype
            LIMIT 1
            "#,
        )
        .bind(id)
        .bind(media_type)
        .fetch_optional(&state.pool_ro)
        .await
        .unwrap_or_else(|e| {
            warn!("poster meta {id}: {e}");
            None
        })
    };

    row.map(|(url, rating, title, add_title)| PosterMeta {
        poster_url: url,
        imdb_rating: rating.map(|r| r as f32),
        title,
        is_add_title: add_title.unwrap_or(false),
    })
}

async fn fetch_genres(state: &AppState, id: &str) -> Vec<String> {
    if let Some(num_str) = id.strip_prefix("mf") {
        let Ok(internal_id) = num_str.parse::<i32>() else {
            return vec![];
        };
        sqlx::query_scalar(
            "SELECT g.name FROM genre g \
             JOIN media_genre_link mgl ON mgl.genre_id = g.id \
             WHERE mgl.media_id = $1",
        )
        .bind(internal_id)
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default()
    } else {
        sqlx::query_scalar(
            "SELECT g.name FROM genre g \
             JOIN media_genre_link mgl ON mgl.genre_id = g.id \
             JOIN media_external_id meid ON meid.media_id = mgl.media_id \
             WHERE meid.external_id = $1",
        )
        .bind(id)
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default()
    }
}

/// Full sports poster: genre-matched with "Other Sports"/"Sports" catchall.
/// Used when `is_add_title_to_poster = true`.
async fn resolve_sports_poster_url(state: &AppState, id: &str) -> Option<String> {
    let genres = fetch_genres(state, id).await;
    crate::poster::sports::random_sports_poster(&genres)
}

/// Strict sports poster: only matches if the item has an explicit sports genre.
/// No catchall — non-sports items receive `None` and fall through to 404.
async fn resolve_sports_poster_strict(state: &AppState, id: &str) -> Option<String> {
    let genres = fetch_genres(state, id).await;
    crate::poster::sports::random_sports_poster_strict(&genres)
}

// ─── Fetch + annotate + cache ─────────────────────────────────────────────────

async fn fetch_annotate_cache(
    state: &AppState,
    cache_key: &str,
    url: &str,
    meta: PosterMeta,
) -> Response {
    let resp = state
        .http
        .get(url)
        .header(header::USER_AGENT, "MediaFusion/1.0")
        .send()
        .await;

    let raw_bytes = match resp {
        Ok(r) if r.status().is_success() => match r.bytes().await {
            Ok(b) => b,
            Err(e) => {
                warn!("poster fetch bytes {url}: {e}");
                return StatusCode::BAD_GATEWAY.into_response();
            }
        },
        Ok(r) => {
            warn!("poster upstream {url}: HTTP {}", r.status());
            return StatusCode::NOT_FOUND.into_response();
        }
        Err(e) => {
            warn!("poster fetch {url}: {e}");
            return StatusCode::BAD_GATEWAY.into_response();
        }
    };

    let params = AnnotateParams {
        imdb_rating: meta.imdb_rating,
        title: meta.title,
        is_add_title: meta.is_add_title,
    };
    let raw_bytes_clone = raw_bytes.to_vec();
    let annotated =
        tokio::task::spawn_blocking(move || crate::poster::annotate(&raw_bytes_clone, &params))
            .await;

    let final_bytes: Vec<u8> = match annotated {
        Ok(Ok(b)) => b,
        Ok(Err(e)) => {
            warn!("poster annotate failed ({e}), serving unannotated");
            raw_bytes.to_vec()
        }
        Err(e) => {
            warn!("poster annotate task panicked: {e}");
            raw_bytes.to_vec()
        }
    };

    cache::set_bytes(&state.redis, cache_key, &final_bytes, 86400).await;
    jpeg_response(final_bytes)
}

fn jpeg_response(bytes: Vec<u8>) -> Response {
    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "image/jpeg")
        .header(header::CACHE_CONTROL, "public, max-age=86400")
        .body(Body::from(bytes))
        .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
}
