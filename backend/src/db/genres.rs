use std::collections::{HashMap, HashSet};

use sqlx::PgPool;
use tracing::{info, warn};

pub const GENRES_CACHE_KEY: &str = "genres:all_by_type:rs";
/// Genres change rarely; long TTL avoids repeated heavy queries on large DBs.
pub const GENRES_CACHE_TTL_SECS: u64 = 86_400;

pub const ADULT_GENRE_NAMES: &[&str] = &["adult", "18+"];

#[derive(sqlx::FromRow)]
struct GenreByTypeRow {
    media_type: String,
    name: String,
}

/// Distinct genres per media type (Python `get_all_genres_by_type` parity).
///
/// Uses three indexed `EXISTS` probes (one per manifest media type) over the ~450-row
/// `genre` table instead of scanning all `media` / `media_genre_link` rows.
pub async fn get_all_genres_by_type(pool: &PgPool) -> HashMap<String, Vec<String>> {
    let rows = match sqlx::query_as::<_, GenreByTypeRow>(
        r#"
        SELECT 'movie' AS media_type, g.name
        FROM genre g
        WHERE EXISTS (
            SELECT 1
            FROM media_genre_link mgl
            INNER JOIN media m ON m.id = mgl.media_id AND m.type = 'MOVIE'
            WHERE mgl.genre_id = g.id
        )
        UNION ALL
        SELECT 'series' AS media_type, g.name
        FROM genre g
        WHERE EXISTS (
            SELECT 1
            FROM media_genre_link mgl
            INNER JOIN media m ON m.id = mgl.media_id AND m.type = 'SERIES'
            WHERE mgl.genre_id = g.id
        )
        UNION ALL
        SELECT 'tv' AS media_type, g.name
        FROM genre g
        WHERE EXISTS (
            SELECT 1
            FROM media_genre_link mgl
            INNER JOIN media m ON m.id = mgl.media_id AND m.type = 'TV'
            WHERE mgl.genre_id = g.id
        )
        ORDER BY media_type, name
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
        let lower = row.name.to_ascii_lowercase();
        if ADULT_GENRE_NAMES.contains(&lower.as_str()) {
            continue;
        }
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

/// Fire-and-forget cache warm (API startup).
pub fn spawn_genres_cache_warm(pool: PgPool, redis: fred::clients::Client) {
    tokio::spawn(async move {
        let _ = load_genres_cached(&pool, &redis).await;
    });
}
