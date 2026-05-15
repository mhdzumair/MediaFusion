use std::collections::HashMap;

use sqlx::PgPool;
use tracing::warn;

const ADULT_GENRES: &[&str] = &[
    "adult",
    "18+",
    "xxx",
    "erotic",
    "erotica",
    "pornography",
    "porn",
];

pub async fn get_all_genres_by_type(pool: &PgPool) -> HashMap<String, Vec<String>> {
    let rows: Vec<(String, String)> = sqlx::query_as(
        r#"
        SELECT DISTINCT lower(m.type::text) AS media_type, g.name AS genre_name
        FROM genre g
        JOIN media_genre_link mgl ON mgl.genre_id = g.id
        JOIN media m ON m.id = mgl.media_id
        WHERE lower(g.name) <> ALL($1)
          AND m.total_streams > 0
          AND NOT m.is_blocked
        ORDER BY lower(m.type::text), g.name
        "#,
    )
    .bind(ADULT_GENRES)
    .fetch_all(pool)
    .await
    .unwrap_or_else(|e| {
        warn!("genres query: {e}");
        vec![]
    });

    let mut map: HashMap<String, Vec<String>> = HashMap::new();
    for (media_type, genre_name) in rows {
        map.entry(media_type).or_default().push(genre_name);
    }
    map
}
