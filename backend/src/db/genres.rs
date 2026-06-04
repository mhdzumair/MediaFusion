use std::collections::{HashMap, HashSet};

use sqlx::PgPool;
use tracing::info;
use tracing::warn;

pub const GENRES_CACHE_KEY: &str = "genres:all_by_type:rs";
/// Genres change rarely; long TTL avoids repeated queries.
pub const GENRES_CACHE_TTL_SECS: u64 = 86_400;

#[derive(sqlx::FromRow)]
struct GenreByTypeRow {
    media_type: String,
    name: String,
}

/// Distinct, visible genres per media type — reads directly from `genre_media_type`
/// (no media scan). `is_hidden = false` at the (genre, type) level is the sole filter;
/// the old hardcoded `ADULT_GENRE_NAMES` list is replaced by seeding those genres with
/// `is_hidden = true` in migration 0017.
pub async fn get_all_genres_by_type(pool: &PgPool) -> HashMap<String, Vec<String>> {
    let rows = match sqlx::query_as::<_, GenreByTypeRow>(
        r#"
        SELECT gmt.media_type, g.name
        FROM   genre_media_type gmt
        JOIN   genre g ON g.id = gmt.genre_id
        WHERE  gmt.is_hidden = false
        ORDER  BY gmt.media_type, g.name
        "#,
    )
    .fetch_all(pool)
    .await
    {
        Ok(rows) => rows,
        Err(e) => {
            warn!("genres query: {e}");
            return HashMap::new();
        }
    };

    let mut by_type: HashMap<String, HashSet<String>> = HashMap::new();
    for row in rows {
        by_type.entry(row.media_type).or_default().insert(row.name);
    }

    by_type
        .into_iter()
        .map(|(media_type, genres)| {
            let mut list: Vec<String> = genres.into_iter().collect();
            list.sort_unstable();
            (media_type, list)
        })
        .collect()
}

/// Load genres from Redis, or compute and cache. Used by manifest and startup warm.
pub async fn load_genres_cached(
    pool: &PgPool,
    redis: &fred::clients::Client,
) -> HashMap<String, Vec<String>> {
    if let Some(v) = crate::cache::get_json(redis, GENRES_CACHE_KEY).await {
        if let Ok(g) = serde_json::from_value(v) {
            return g;
        }
    }

    let started = std::time::Instant::now();
    let genres = get_all_genres_by_type(pool).await;
    let elapsed = started.elapsed();
    if elapsed.as_secs() >= 1 {
        info!(
            elapsed_ms = elapsed.as_millis(),
            movie = genres.get("movie").map(|v| v.len()).unwrap_or(0),
            series = genres.get("series").map(|v| v.len()).unwrap_or(0),
            tv = genres.get("tv").map(|v| v.len()).unwrap_or(0),
            "genres: computed all_by_type from database"
        );
    }

    let gv = serde_json::to_value(&genres).unwrap_or_default();
    crate::cache::set_json(redis, GENRES_CACHE_KEY, &gv, GENRES_CACHE_TTL_SECS).await;
    genres
}

/// Clear all genre-related Redis cache keys.
pub async fn invalidate_genres_cache(redis: &fred::clients::Client) {
    use fred::prelude::KeysInterface;
    let keys: Vec<&str> = vec![
        GENRES_CACHE_KEY,
        "genres:MOVIE",
        "genres:SERIES",
        "genres:TV",
        "genres:EVENTS",
    ];
    let _: Result<i64, _> = redis.del(keys).await;
}

/// Fire-and-forget cache warm (API startup).
pub fn spawn_genres_cache_warm(pool: PgPool, redis: fred::clients::Client) {
    tokio::spawn(async move {
        let _ = load_genres_cached(&pool, &redis).await;
    });
}
