use sqlx::PgPool;
use tracing::warn;

use crate::scrapers::SearchMeta;

#[derive(sqlx::FromRow)]
pub struct MediaCandidate {
    pub media_id: i64,
    pub title: String,
    pub year: Option<i32>,
    pub imdb_id: Option<String>,
    pub tmdb_id: Option<String>,
    pub tvdb_id: Option<String>,
}

/// Full-text search for media candidates matching `title` and `media_type`.
/// Returns up to 12 candidates ordered by popularity, with all known external IDs.
/// Used for metadata enrichment in the missing-torrent import flow.
pub async fn search_media_candidates(
    pool: &PgPool,
    media_type: &str,
    title: &str,
) -> Vec<MediaCandidate> {
    sqlx::query_as::<_, MediaCandidate>(
        r#"
        SELECT
            m.id::bigint AS media_id,
            m.title,
            m.year,
            MAX(CASE WHEN mei.provider = 'imdb' THEN mei.external_id END) AS imdb_id,
            MAX(CASE WHEN mei.provider = 'tmdb' THEN mei.external_id END) AS tmdb_id,
            MAX(CASE WHEN mei.provider = 'tvdb' THEN mei.external_id END) AS tvdb_id
        FROM media m
        LEFT JOIN media_external_id mei
               ON mei.media_id = m.id
              AND mei.provider IN ('imdb', 'tmdb', 'tvdb')
        WHERE m.type = upper($1)::mediatype
          AND m.title_tsv @@ plainto_tsquery('simple', $2)
        GROUP BY m.id, m.title, m.year
        ORDER BY m.popularity DESC NULLS LAST
        LIMIT 12
        "#,
    )
    .bind(media_type)
    .bind(title)
    .fetch_all(pool)
    .await
    .unwrap_or_else(|e| {
        warn!("search_media_candidates title={title}: {e}");
        vec![]
    })
}

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

/// Resolve a browse/search external ID to an internal `media.id`.
///
/// Supports the same formats as the Python `get_media_by_external_id`:
/// `tt*`, `mf:*`, `mftmdb*`, and `provider:id` (e.g. `tmdb:603`, `tvdb:123`).
pub async fn get_media_id_by_external_id(
    pool: &PgPool,
    external_id: &str,
    media_type: Option<&str>,
) -> Result<Option<i32>, sqlx::Error> {
    let external_id = external_id.trim();
    if external_id.is_empty() {
        return Ok(None);
    }

    if let Some(raw) = external_id
        .strip_prefix("mf:")
        .or_else(|| external_id.strip_prefix("mf"))
    {
        let Ok(internal_id) = raw.parse::<i32>() else {
            return Ok(None);
        };
        if let Some(mt) = media_type {
            return sqlx::query_scalar(
                "SELECT id FROM media WHERE id = $1 AND type = upper($2)::mediatype LIMIT 1",
            )
            .bind(internal_id)
            .bind(mt)
            .fetch_optional(pool)
            .await;
        }
        return sqlx::query_scalar("SELECT id FROM media WHERE id = $1 LIMIT 1")
            .bind(internal_id)
            .fetch_optional(pool)
            .await;
    }

    let (provider, provider_external_id): (String, &str) = if external_id.starts_with("tt") {
        ("imdb".to_string(), external_id)
    } else if let Some(id) = external_id.strip_prefix("mftmdb") {
        ("tmdb".to_string(), id)
    } else if let Some((provider, id)) = external_id.split_once(':') {
        (provider.to_ascii_lowercase(), id)
    } else {
        return Ok(None);
    };

    if let Some(mt) = media_type {
        sqlx::query_scalar(
            r#"
            SELECT m.id FROM media m
            JOIN media_external_id meid ON m.id = meid.media_id
            WHERE meid.provider = $1
              AND meid.external_id = $2
              AND m.type = upper($3)::mediatype
            LIMIT 1
            "#,
        )
        .bind(&provider)
        .bind(provider_external_id)
        .bind(mt)
        .fetch_optional(pool)
        .await
    } else {
        sqlx::query_scalar(
            r#"
            SELECT meid.media_id FROM media_external_id meid
            WHERE meid.provider = $1 AND meid.external_id = $2
            LIMIT 1
            "#,
        )
        .bind(&provider)
        .bind(provider_external_id)
        .fetch_optional(pool)
        .await
    }
}
