//! Trakt watch-sync helpers — resolve local media via fetch + store when missing.

use sqlx::PgPool;
use tracing::debug;

use crate::db::{store_media, MediaId, StoreMediaOpts};

use super::{fetch_normalized, FetchCtx};

/// Resolve a local media id from IMDb/TMDB ids, fetching and storing metadata when absent.
pub async fn resolve_or_store_media(
    pool: &PgPool,
    http: &reqwest::Client,
    ctx: &FetchCtx<'_>,
    imdb: Option<&str>,
    tmdb: Option<&str>,
    title: &str,
    is_series: bool,
) -> Option<MediaId> {
    if let Some(iid) = imdb {
        if let Some(id) = lookup_external(pool, "imdb", iid).await {
            return Some(id);
        }
    }
    if let Some(tid) = tmdb {
        if let Some(id) = lookup_external(pool, "tmdb", tid).await {
            return Some(id);
        }
    }

    let meta = if let Some(iid) = imdb {
        fetch_normalized(http, ctx, "imdb", iid, is_series).await
    } else if let Some(tid) = tmdb {
        fetch_normalized(http, ctx, "tmdb", tid, is_series).await
    } else {
        None
    }?;

    store_media(pool, &meta, StoreMediaOpts::default())
        .await
        .ok()
        .or_else(|| {
            debug!("trakt: failed to store media for '{title}'");
            None
        })
}

async fn lookup_external(pool: &PgPool, provider: &str, external_id: &str) -> Option<MediaId> {
    sqlx::query_scalar(
        "SELECT media_id FROM media_external_id WHERE provider = $1 AND external_id = $2 LIMIT 1",
    )
    .bind(provider)
    .bind(external_id)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten()
    .map(MediaId)
}
