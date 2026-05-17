/// Shared media find-or-create logic used by RSS and spider scrapers.
///
/// Resolution order:
///   1. Exact title + type + year match in `media`
///   2. pg_trgm fuzzy match (similarity > 0.4, then similarity_ratio >= 70)
///   3. External metadata: TMDB (if API key) → Cinemeta/IMDb
///   4. Minimal stub creation so the stream is never lost
use sqlx::PgPool;
use tracing::{debug, info};

pub struct MediaEntry {
    pub id: i32,
    pub title: String,
    pub year: Option<i32>,
}

pub async fn find_or_create_media(
    pool: &PgPool,
    http: &reqwest::Client,
    title: &str,
    year: Option<i32>,
    is_series: bool,
    catalog_ids: &[&str],
    tmdb_api_key: Option<&str>,
) -> Option<MediaEntry> {
    let media_type = if is_series { "SERIES" } else { "MOVIE" };

    // 1. Exact title match (case-insensitive) with ±1 year tolerance.
    let row: Option<(i32, String, Option<i32>)> = if let Some(y) = year {
        sqlx::query_as(
            "SELECT id, title, year FROM media \
             WHERE LOWER(title) = LOWER($1) AND type::text = $2 \
             AND (year = $3 OR year = $4 OR year IS NULL) LIMIT 1",
        )
        .bind(title)
        .bind(media_type)
        .bind(y)
        .bind(y - 1)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten()
    } else {
        sqlx::query_as(
            "SELECT id, title, year FROM media \
             WHERE LOWER(title) = LOWER($1) AND type::text = $2 LIMIT 1",
        )
        .bind(title)
        .bind(media_type)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten()
    };

    if let Some((id, t, y)) = row {
        debug!("media_resolve: found existing media {id} for '{title}'");
        link_to_catalogs(pool, id, catalog_ids).await;
        return Some(MediaEntry {
            id,
            title: t,
            year: y,
        });
    }

    // 2. Fuzzy pg_trgm match.
    // Use the `%` similarity operator (not the function) so the query planner
    // can use the GIN trigram index — the function form causes a seq-scan.
    let fuzzy: Option<(i32, String, Option<i32>)> = sqlx::query_as(
        "SELECT id, title, year FROM media \
         WHERE type::text = $1 AND title % $2 \
         ORDER BY similarity(title, $2) DESC LIMIT 1",
    )
    .bind(media_type)
    .bind(title)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten();

    if let Some((id, t, y)) = fuzzy {
        let sim = crate::parser::similarity_ratio(title, &t);
        if sim >= 70 {
            debug!("media_resolve: fuzzy match {id} (sim={sim}) for '{title}' → '{t}'");
            link_to_catalogs(pool, id, catalog_ids).await;
            return Some(MediaEntry {
                id,
                title: t,
                year: y,
            });
        }
    }

    // 3. External metadata lookup: TMDB → Cinemeta.
    if let Some(meta) =
        crate::scrapers::metadata::search_by_title(http, title, year, is_series, tmdb_api_key).await
    {
        debug!(
            "media_resolve: external match '{}' ({:?}) via {} for '{title}'",
            meta.title, meta.year, meta.provider
        );

        // Check if this external_id is already in the DB (different local title).
        let existing: Option<(i32,)> = sqlx::query_as(
            "SELECT m.id FROM media m JOIN media_external_id mei ON mei.media_id = m.id \
             WHERE mei.provider = $1 AND mei.external_id = $2 AND m.type::text = $3 LIMIT 1",
        )
        .bind(&meta.provider)
        .bind(&meta.external_id)
        .bind(media_type)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten();

        if let Some((media_id,)) = existing {
            debug!("media_resolve: found existing media {media_id} via external_id");
            link_to_catalogs(pool, media_id, catalog_ids).await;
            return Some(MediaEntry {
                id: media_id,
                title: meta.title,
                year: meta.year,
            });
        }

        // Also fetch IMDb ID for TMDB results so Stremio can resolve it.
        let imdb_id = if meta.provider == "tmdb" {
            if let Some(key) = tmdb_api_key {
                crate::scrapers::metadata::imdb_id_from_tmdb(
                    http,
                    &meta.external_id,
                    is_series,
                    key,
                )
                .await
            } else {
                None
            }
        } else {
            Some(meta.external_id.clone()) // already an IMDb ID
        };

        let media_id = insert_media_row(pool, media_type, &meta.title, meta.year).await?;

        store_external_id(pool, media_id, &meta.provider, &meta.external_id).await;
        if let Some(ref iid) = imdb_id {
            if meta.provider != "imdb" {
                store_external_id(pool, media_id, "imdb", iid).await;
            }
        }

        if let Some(ref poster) = meta.poster_url {
            let _ = sqlx::query(
                "INSERT INTO media_image \
                 (media_id, provider_id, image_type, url, is_primary, display_order) \
                 VALUES ($1, 1, 'poster', $2, true, 0) \
                 ON CONFLICT (media_id, provider_id, image_type, url) DO NOTHING",
            )
            .bind(media_id)
            .bind(poster)
            .execute(pool)
            .await;
        }

        link_to_catalogs(pool, media_id, catalog_ids).await;
        info!(
            "media_resolve: created media {media_id} from {} match for '{title}' → '{}'",
            meta.provider, meta.title
        );
        return Some(MediaEntry {
            id: media_id,
            title: meta.title,
            year: meta.year,
        });
    }

    // 4. No external match — create a minimal stub so the stream is not lost.
    let media_id = insert_media_row(pool, media_type, title, year).await?;

    let clean: String = title
        .to_lowercase()
        .chars()
        .map(|c| if c.is_alphanumeric() { c } else { '_' })
        .collect();
    let clean = clean.trim_matches('_');
    let clean: String = clean.chars().take(40).collect();
    let year_str = year.map(|y| format!("_{y}")).unwrap_or_default();
    let mf_prefix = if is_series { "mfs" } else { "mfm" };
    let short_hash = format!("{:x}", md5_short(&format!("{clean}{year_str}")));
    let mf_id = format!("{mf_prefix}_{short_hash}");

    store_external_id(pool, media_id, "mediafusion", &mf_id).await;
    link_to_catalogs(pool, media_id, catalog_ids).await;

    info!("media_resolve: created stub media {media_id} (id={mf_id}) for '{title}'");
    Some(MediaEntry {
        id: media_id,
        title: title.to_string(),
        year,
    })
}

pub async fn insert_media_row(
    pool: &PgPool,
    media_type: &str,
    title: &str,
    year: Option<i32>,
) -> Option<i32> {
    let insert: Option<(i32,)> = sqlx::query_as(
        r#"
        INSERT INTO media (
            type, title, year,
            adult, is_blocked, is_public, is_user_created,
            total_streams, created_at
        )
        VALUES ($1::mediatype, $2, $3, false, false, true, false, 0, NOW())
        ON CONFLICT DO NOTHING
        RETURNING id
        "#,
    )
    .bind(media_type)
    .bind(title)
    .bind(year)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten();

    match insert {
        Some((id,)) => Some(id),
        None => sqlx::query_scalar::<_, i32>(
            "SELECT id FROM media WHERE LOWER(title) = LOWER($1) AND type::text = $2 LIMIT 1",
        )
        .bind(title)
        .bind(media_type)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten(),
    }
}

pub async fn store_external_id(pool: &PgPool, media_id: i32, provider: &str, external_id: &str) {
    let _ = sqlx::query(
        "INSERT INTO media_external_id (media_id, provider, external_id) \
         VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
    )
    .bind(media_id)
    .bind(provider)
    .bind(external_id)
    .execute(pool)
    .await;
}

pub async fn link_to_catalogs(pool: &PgPool, media_id: i32, catalog_ids: &[&str]) {
    for catalog_name in catalog_ids {
        let _ = sqlx::query(
            "INSERT INTO media_catalog_link (media_id, catalog_id) \
             SELECT $1, c.id FROM catalog c WHERE c.name = $2 \
             ON CONFLICT DO NOTHING",
        )
        .bind(media_id)
        .bind(catalog_name)
        .execute(pool)
        .await;
    }
}

/// Find or create a minimal media stub for a sports event, skipping any external
/// metadata lookup. Sports event titles (match replays, highlight clips) are not
/// on TMDB/IMDb, so a remote lookup would be wasted latency.
///
/// Sets `is_add_title_to_poster = true` on newly-created stubs so the poster
/// endpoint auto-selects a genre-matched poster from the bundled sports artifacts.
pub async fn find_or_create_sports_stub(
    pool: &PgPool,
    title: &str,
    year: Option<i32>,
    genre_name: &str,
    poster_url: Option<&str>,
) -> Option<i32> {
    // 1. Exact title match (case-insensitive).
    let row: Option<(i32,)> = if let Some(y) = year {
        sqlx::query_as(
            "SELECT id FROM media WHERE LOWER(title) = LOWER($1) \
             AND type = 'MOVIE'::mediatype AND (year = $2 OR year IS NULL) LIMIT 1",
        )
        .bind(title)
        .bind(y)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten()
    } else {
        sqlx::query_as(
            "SELECT id FROM media WHERE LOWER(title) = LOWER($1) \
             AND type = 'MOVIE'::mediatype LIMIT 1",
        )
        .bind(title)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten()
    };

    if let Some((id,)) = row {
        link_genre(pool, id, genre_name).await;
        return Some(id);
    }

    // 2. Fuzzy pg_trgm match (same threshold as find_or_create_media).
    let fuzzy: Option<(i32, String)> = sqlx::query_as(
        "SELECT id, title FROM media WHERE type = 'MOVIE'::mediatype AND title % $1 \
         ORDER BY similarity(title, $1) DESC LIMIT 1",
    )
    .bind(title)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten();

    if let Some((id, existing_title)) = fuzzy {
        if crate::parser::similarity_ratio(title, &existing_title) >= 70 {
            link_genre(pool, id, genre_name).await;
            return Some(id);
        }
    }

    // 3. Create stub with is_add_title_to_poster = true.
    let insert: Option<(i32,)> = sqlx::query_as(
        r#"
        INSERT INTO media (
            type, title, year,
            adult, is_blocked, is_public, is_user_created,
            is_add_title_to_poster, total_streams, created_at
        )
        VALUES ('MOVIE'::mediatype, $1, $2, false, false, true, false, true, 0, NOW())
        ON CONFLICT DO NOTHING
        RETURNING id
        "#,
    )
    .bind(title)
    .bind(year)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten();

    // Race: another worker may have inserted in between — fall back to SELECT.
    let media_id = match insert {
        Some((id,)) => id,
        None => sqlx::query_scalar::<_, i32>(
            "SELECT id FROM media WHERE LOWER(title) = LOWER($1) \
             AND type = 'MOVIE'::mediatype LIMIT 1",
        )
        .bind(title)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten()?,
    };

    // Store the scraped poster (only if no primary poster already stored).
    if let Some(url) = poster_url {
        let _ = sqlx::query(
            "INSERT INTO media_image \
             (media_id, provider_id, image_type, url, is_primary, display_order) \
             VALUES ($1, 1, 'poster', $2, true, 0) \
             ON CONFLICT (media_id, provider_id, image_type, url) DO NOTHING",
        )
        .bind(media_id)
        .bind(url)
        .execute(pool)
        .await;
    }

    link_genre(pool, media_id, genre_name).await;

    debug!("media_resolve: created sports stub {media_id} for '{title}'");
    Some(media_id)
}

/// Find or insert a genre row by name, then link it to the media item.
pub async fn link_genre(pool: &PgPool, media_id: i32, genre_name: &str) {
    // Upsert the genre row (unique index on name).
    let genre_id: Option<i32> = sqlx::query_scalar(
        "INSERT INTO genre (name) VALUES ($1) \
         ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name \
         RETURNING id",
    )
    .bind(genre_name)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten();

    if let Some(gid) = genre_id {
        let _ = sqlx::query(
            "INSERT INTO media_genre_link (media_id, genre_id) VALUES ($1, $2) \
             ON CONFLICT DO NOTHING",
        )
        .bind(media_id)
        .bind(gid)
        .execute(pool)
        .await;
    }
}

fn md5_short(s: &str) -> u32 {
    use std::hash::{Hash, Hasher};
    let mut h = std::collections::hash_map::DefaultHasher::new();
    s.hash(&mut h);
    h.finish() as u32
}
