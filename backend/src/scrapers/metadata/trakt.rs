//! Trakt watch-sync helpers — resolve local media via fetch + store when missing.

use sqlx::PgPool;
use tracing::debug;

use crate::db::{MediaId, StoreMediaOpts, store_media};

use super::{FetchCtx, fetch_normalized};

/// Resolve a local media id from external ids, fetching and storing metadata when absent.
pub async fn resolve_or_store_media(
    pool: &PgPool,
    http: &reqwest::Client,
    ctx: &FetchCtx<'_>,
    imdb: Option<&str>,
    tmdb: Option<&str>,
    tvdb: Option<&str>,
    mal: Option<&str>,
    title: &str,
    is_series: bool,
) -> Option<MediaId> {
    for (provider, id) in [("imdb", imdb), ("tmdb", tmdb), ("tvdb", tvdb), ("mal", mal)] {
        if let Some(ext_id) = id
            && let Some(mid) = lookup_external(pool, provider, ext_id).await
        {
            return Some(mid);
        }
    }

    let meta = if let Some(tid) = tmdb {
        fetch_normalized(http, ctx, "tmdb", tid, is_series).await
    } else if let Some(iid) = imdb {
        fetch_normalized(http, ctx, "imdb", iid, is_series).await
    } else if let Some(vid) = tvdb {
        fetch_normalized(http, ctx, "tvdb", vid, is_series).await
    } else if let Some(mid) = mal {
        fetch_normalized(http, ctx, "mal", mid, is_series).await
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
