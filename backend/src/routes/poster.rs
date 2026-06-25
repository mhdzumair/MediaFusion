use std::sync::Arc;

use axum::{
    body::Body,
    extract::{Path, State},
    http::{StatusCode, header},
    response::{IntoResponse, Response},
};
use tracing::{debug, warn};

use crate::{cache, db::MediaType, poster::AnnotateParams, state::AppState, util::http};

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

        // Try to fetch and annotate the poster URL if one exists. Returns None
        // on any upstream error (404, unreachable, bad image format) so we can
        // fall through to the placeholder below.
        if let Some(url) = poster_url
            && let Some(bytes) = fetch_annotate_cache(
                &state,
                &cache_key,
                &url,
                meta.imdb_rating,
                meta.is_add_title,
                meta.title.as_deref(),
            )
            .await
            {
                return jpeg_response(bytes);
            }

        // No artwork, or upstream fetch failed: generate a name-based placeholder
        // so the user always sees something instead of a broken image.
        if let Some(title) = meta.title.as_deref().filter(|t| !t.is_empty()) {
            let title = title.to_string();
            let mt = media_type.clone();
            let year = meta.year;
            if let Ok(Ok(bytes)) = tokio::task::spawn_blocking(move || {
                crate::poster::generate_placeholder(&title, &mt, year)
            })
            .await
            {
                cache::set_bytes(&state.redis, &cache_key, &bytes, 86400).await;
                return jpeg_response(bytes);
            }
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

    if let Some(bytes) = fetch_annotate_cache(
        state,
        cache_key,
        &url,
        imdb_rating,
        is_add_title,
        title.as_deref(),
    )
    .await
    {
        return jpeg_response(bytes);
    }
    StatusCode::NOT_FOUND.into_response()
}

// ─── DB resolution ────────────────────────────────────────────────────────────

struct PosterMeta {
    poster_url: Option<String>,
    imdb_rating: Option<f32>,
    title: Option<String>,
    is_add_title: bool,
    year: Option<i32>,
}

async fn resolve_poster_meta(state: &AppState, id: &str, media_type: &str) -> Option<PosterMeta> {
    type Row = (
        Option<String>,
        Option<f64>,
        Option<String>,
        Option<bool>,
        Option<i32>,
    );

    // Frontend uses "mf:{id}", Stremio catalog uses "mf{id}" — accept both.
    let row: Option<Row> = if let Some(num_str) =
        id.strip_prefix("mf:").or_else(|| id.strip_prefix("mf"))
    {
        let internal_id: i32 = num_str.parse().ok()?;
        sqlx::query_as(
            r#"
            SELECT
                mi.url,
                mr.rating,
                m.title,
                m.is_add_title_to_poster,
                m.year
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
            WHERE m.id = $1 AND m.type = $2
            LIMIT 1
            "#,
        )
        .bind(internal_id)
        .bind(MediaType::from_wire(&media_type.to_ascii_lowercase()).unwrap_or(MediaType::Movie))
        .fetch_optional(&state.pool_ro)
        .await
        .unwrap_or_else(|e| {
            if matches!(e, sqlx::Error::PoolTimedOut) {
                tracing::debug!("poster meta mf{internal_id}: pool timeout");
            } else {
                warn!("poster meta mf{internal_id}: {e}");
            }
            None
        })
    } else {
        sqlx::query_as(
            r#"
            SELECT
                mi.url,
                mr.rating,
                m.title,
                m.is_add_title_to_poster,
                m.year
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
              AND m.type = $2
            LIMIT 1
            "#,
        )
        .bind(id)
        .bind(MediaType::from_wire(&media_type.to_ascii_lowercase()).unwrap_or(MediaType::Movie))
        .fetch_optional(&state.pool_ro)
        .await
        .unwrap_or_else(|e| {
            if matches!(e, sqlx::Error::PoolTimedOut) {
                tracing::debug!("poster meta {id}: pool timeout");
            } else {
                warn!("poster meta {id}: {e}");
            }
            None
        })
    };

    row.map(|(url, rating, title, add_title, year)| PosterMeta {
        poster_url: url,
        imdb_rating: rating.map(|r| r as f32),
        title,
        is_add_title: add_title.unwrap_or(false),
        year,
    })
}

async fn fetch_genres(state: &AppState, id: &str) -> Vec<String> {
    if let Some(num_str) = id.strip_prefix("mf:").or_else(|| id.strip_prefix("mf")) {
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

/// Fetch a poster URL, annotate it, cache the result, and return the bytes.
/// Returns `None` on any upstream failure so callers can fall back to a placeholder.
async fn fetch_annotate_cache(
    state: &AppState,
    cache_key: &str,
    url: &str,
    imdb_rating: Option<f32>,
    is_add_title: bool,
    title: Option<&str>,
) -> Option<Vec<u8>> {
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
                return None;
            }
        },
        Ok(r) => {
            debug!("poster upstream {url}: HTTP {}", r.status());
            return None;
        }
        Err(e) => {
            debug!(
                error_kind = http::transport_error_kind(&e),
                "poster fetch {url}: {e}"
            );
            return None;
        }
    };

    let params = AnnotateParams {
        imdb_rating,
        title: title.map(str::to_string),
        is_add_title,
    };
    let raw_bytes_clone = raw_bytes.to_vec();
    let annotated =
        tokio::task::spawn_blocking(move || crate::poster::annotate(&raw_bytes_clone, &params))
            .await;

    let final_bytes: Vec<u8> = match annotated {
        Ok(Ok(b)) => b,
        Ok(Err(e)) => {
            debug!("poster annotate failed ({e}), serving unannotated");
            raw_bytes.to_vec()
        }
        Err(e) => {
            warn!("poster annotate task panicked: {e}");
            raw_bytes.to_vec()
        }
    };

    cache::set_bytes(&state.redis, cache_key, &final_bytes, 86400).await;
    Some(final_bytes)
}

fn jpeg_response(bytes: Vec<u8>) -> Response {
    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "image/jpeg")
        .header(header::CACHE_CONTROL, "public, max-age=86400")
        .body(Body::from(bytes))
        .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
}
