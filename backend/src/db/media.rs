use sqlx::PgPool;
use tracing::warn;

use crate::scrapers::SearchMeta;

/// Fetch title, year, and imdb_id for a known media_id.
/// Returns None if the row doesn't exist.
pub async fn get_media_meta(
    pool: &PgPool,
    media_id: i64,
    imdb_id: &str,
) -> Result<Option<SearchMeta>, Box<dyn std::error::Error + Send + Sync>> {
    let row: Option<(String, Option<i32>)> =
        sqlx::query_as(r#"SELECT title, year FROM media WHERE id = $1 LIMIT 1"#)
            .bind(media_id as i32)
            .fetch_optional(pool)
            .await
            .map_err(|e| format!("get_media_meta: {e}"))?;

    Ok(row.map(|(title, year)| SearchMeta {
        media_id,
        imdb_id: if imdb_id.is_empty() {
            None
        } else {
            Some(imdb_id.to_string())
        },
        title,
        year,
    }))
}

/// Resolve a Stremio video ID to (primary_media_id, related_media_ids).
/// Handles: tt{imdb}, mf{internal_id}, tmdb:{id}, tvdb:{id}, mal:{id}, dl prefix.
/// Returns (0, []) if not found.
pub async fn resolve_media_ids(
    pool: &PgPool,
    video_id: &str,
    media_type: &str,
) -> Result<(i64, Vec<i64>), Box<dyn std::error::Error + Send + Sync>> {
    // mf{internal_id} — direct media.id lookup, no external ID join needed.
    if let Some(raw) = video_id.strip_prefix("mf") {
        if let Ok(id) = raw.parse::<i32>() {
            let exists: Option<(i32,)> = sqlx::query_as(
                "SELECT id FROM media WHERE id = $1 AND type = upper($2)::mediatype LIMIT 1",
            )
            .bind(id)
            .bind(media_type)
            .fetch_optional(pool)
            .await
            .map_err(|e| format!("mf lookup: {e}"))?;
            return Ok(match exists {
                Some((media_id,)) => (media_id as i64, vec![]),
                None => (0, vec![]),
            });
        }
    }

    // Determine (provider, external_id) pair for external ID types.
    let (provider, external_id) = if video_id.starts_with("tt") {
        ("imdb", video_id)
    } else if let Some(id) = video_id.strip_prefix("tmdb:") {
        ("tmdb", id)
    } else if let Some(id) = video_id.strip_prefix("tvdb:") {
        ("tvdb", id)
    } else if let Some(id) = video_id.strip_prefix("mal:") {
        ("mal", id)
    } else {
        // Unknown prefix — fall back to imdb lookup with the raw value.
        ("imdb", video_id)
    };

    let row: Option<(i32,)> = sqlx::query_as(
        r#"
        SELECT m.id FROM media m
        JOIN media_external_id meid ON m.id = meid.media_id
        WHERE meid.provider = $1
          AND meid.external_id = $2
          AND m.type = upper($3)::mediatype
        LIMIT 1
        "#,
    )
    .bind(provider)
    .bind(external_id)
    .bind(media_type)
    .fetch_optional(pool)
    .await
    .map_err(|e| format!("media lookup: {e}"))?;

    let Some((media_id_i32,)) = row else {
        return Ok((0, vec![]));
    };
    let media_id = media_id_i32 as i64;

    let related: Vec<(i32,)> = sqlx::query_as(
        r#"
        SELECT m.id FROM media m
        JOIN media_external_id meid ON m.id = meid.media_id
        WHERE meid.provider = $1
          AND meid.external_id = $2
          AND m.type = upper($3)::mediatype
          AND m.id != $4
        "#,
    )
    .bind(provider)
    .bind(external_id)
    .bind(media_type)
    .bind(media_id_i32)
    .fetch_all(pool)
    .await
    .unwrap_or_else(|e| {
        warn!("related media lookup: {e}");
        vec![]
    });

    let related_ids = related.into_iter().map(|(id,)| id as i64).collect();
    Ok((media_id, related_ids))
}
