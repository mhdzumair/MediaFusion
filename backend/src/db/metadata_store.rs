use chrono::NaiveDate;
use sqlx::PgPool;
use tracing::warn;

use super::metadata_model::{
    NormalizedCastMember, NormalizedCrewMember, NormalizedMetadata, NormalizedRating,
    NormalizedSeason, NormalizedTrailer, StoreMediaOpts,
};
use super::types::{MediaId, MediaType};

/// Idempotent find-or-create media + linked rows. On existing media, COALESCE-updates
/// scalar fields and upserts children (refresh mode uses the same path).
pub async fn store_media(
    pool: &PgPool,
    meta: &NormalizedMetadata,
    opts: StoreMediaOpts,
) -> Result<MediaId, sqlx::Error> {
    let media_id = if let Some(id) = opts.existing_media_id {
        id
    } else {
        resolve_existing_media(pool, meta)
            .await?
            .unwrap_or(MediaId(0))
    };

    let media_id = if media_id.0 > 0 {
        upsert_media_row(pool, media_id, meta, &opts).await?;
        media_id
    } else {
        insert_media_row(pool, meta, &opts).await?
    };

    upsert_external_ids(pool, media_id, &meta.external_ids).await;
    upsert_genres(pool, media_id.0, &meta.genres, meta.media_type).await;
    upsert_catalogs(pool, media_id.0, &meta.catalogs).await;
    upsert_images(pool, media_id.0, meta).await;
    upsert_cast(pool, media_id.0, &meta.cast).await;
    upsert_crew(pool, media_id.0, &meta.crew).await;
    upsert_trailers(pool, media_id.0, &meta.trailers).await;
    upsert_aka_titles(pool, media_id.0, &meta.title, &meta.aka_titles).await;
    upsert_keywords(pool, media_id.0, &meta.keywords).await;
    upsert_ratings(pool, media_id.0, &meta.ratings).await;
    upsert_certificates(pool, media_id.0, &meta.certificates).await;
    upsert_country(pool, media_id, meta.country.as_deref()).await;

    if meta.media_type == MediaType::Series {
        upsert_series(
            pool,
            media_id,
            &meta.seasons,
            opts.is_user_created,
            meta.network.as_deref(),
        )
        .await?;
    } else if meta.media_type == MediaType::Movie {
        upsert_movie_metadata(pool, media_id, meta.budget, meta.revenue).await?;
    }

    Ok(media_id)
}

/// Resolve an existing media row via external ids, then exact/fuzzy title match.
pub async fn find_existing_media(
    pool: &PgPool,
    media_type: MediaType,
    title: &str,
    year: Option<i32>,
) -> Option<MediaId> {
    let wire = media_type.as_wire();

    let row: Option<(i32,)> = if let Some(y) = year {
        sqlx::query_as(
            "SELECT id FROM media \
             WHERE LOWER(title) = LOWER($1) AND type = $2 \
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
        sqlx::query_as("SELECT id FROM media WHERE LOWER(title) = LOWER($1) AND type = $2 LIMIT 1")
            .bind(title)
            .bind(media_type)
            .fetch_optional(pool)
            .await
            .ok()
            .flatten()
    };

    if let Some((id,)) = row {
        return Some(MediaId(id));
    }

    let fuzzy: Option<(i32, String)> = sqlx::query_as(
        "SELECT id, title FROM media \
         WHERE type = $1 AND title % $2 \
         ORDER BY similarity(title, $2) DESC LIMIT 1",
    )
    .bind(media_type)
    .bind(title)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten();

    if let Some((id, existing_title)) = fuzzy {
        if crate::parser::similarity_ratio(title, &existing_title) >= 70 {
            return Some(MediaId(id));
        }
    }

    let _ = wire;
    None
}

async fn resolve_existing_media(
    pool: &PgPool,
    meta: &NormalizedMetadata,
) -> Result<Option<MediaId>, sqlx::Error> {
    let wire = meta.media_type.as_wire();
    for (provider, ext_id) in &meta.external_ids {
        if let Some(id) = crate::db::get_media_id_by_external_id(
            pool,
            &format!("{provider}:{ext_id}"),
            Some(wire),
        )
        .await?
        {
            return Ok(Some(id));
        }
        if provider == "imdb" {
            if let Some(id) =
                crate::db::get_media_id_by_external_id(pool, ext_id, Some(wire)).await?
            {
                return Ok(Some(id));
            }
        }
    }

    Ok(find_existing_media(pool, meta.media_type, &meta.title, meta.year).await)
}

async fn insert_media_row(
    pool: &PgPool,
    meta: &NormalizedMetadata,
    opts: &StoreMediaOpts,
) -> Result<MediaId, sqlx::Error> {
    let release_date = parse_release_date(meta.release_date.as_deref());
    let end_date = parse_release_date(meta.end_date.as_deref());
    let id: i32 = sqlx::query_scalar(
        r#"
        INSERT INTO media (
            type, title, original_title, year, release_date, end_date,
            description, tagline, runtime_minutes, status, original_language, website,
            adult, popularity, is_blocked, is_public, is_user_created, created_by_user_id,
            is_add_title_to_poster, nudity_status, total_streams, created_at
        )
        VALUES (
            $1, $2, $3, $4, $5, $6,
            $7, $8, $9, $10, $11, $12,
            $13, $14, false, $15, $16, $17,
            $18, $19, 0, NOW()
        )
        RETURNING id
        "#,
    )
    .bind(meta.media_type)
    .bind(&meta.title)
    .bind(&meta.original_title)
    .bind(meta.year)
    .bind(release_date)
    .bind(end_date)
    .bind(&meta.description)
    .bind(&meta.tagline)
    .bind(meta.runtime_minutes)
    .bind(&meta.status)
    .bind(&meta.original_language)
    .bind(&meta.website)
    .bind(meta.adult)
    .bind(meta.popularity)
    .bind(opts.is_public)
    .bind(opts.is_user_created)
    .bind(opts.created_by_user_id)
    .bind(opts.is_add_title_to_poster)
    .bind(meta.nudity_status)
    .fetch_one(pool)
    .await?;

    Ok(MediaId(id))
}

async fn upsert_media_row(
    pool: &PgPool,
    media_id: MediaId,
    meta: &NormalizedMetadata,
    opts: &StoreMediaOpts,
) -> Result<(), sqlx::Error> {
    let release_date = parse_release_date(meta.release_date.as_deref());
    let end_date = parse_release_date(meta.end_date.as_deref());
    sqlx::query(
        r#"
        UPDATE media SET
            title = $2,
            original_title = COALESCE($3, original_title),
            year = COALESCE($4, year),
            release_date = COALESCE($5, release_date),
            end_date = COALESCE($6, end_date),
            description = COALESCE($7, description),
            tagline = COALESCE($8, tagline),
            runtime_minutes = COALESCE($9, runtime_minutes),
            status = COALESCE($10, status),
            original_language = COALESCE($11, original_language),
            website = COALESCE($12, website),
            adult = CASE WHEN $13 THEN true ELSE media.adult END,
            popularity = COALESCE($14, popularity),
            nudity_status = CASE WHEN $15 <> 'UNKNOWN'::nuditystatus THEN $15 ELSE media.nudity_status END,
            is_user_created = CASE WHEN $16 THEN true ELSE is_user_created END,
            created_by_user_id = COALESCE($17, created_by_user_id),
            is_add_title_to_poster = CASE WHEN $18 THEN true ELSE is_add_title_to_poster END,
            is_public = COALESCE($19, is_public),
            last_scraped_at = NOW(),
            updated_at = NOW()
        WHERE id = $1
        "#,
    )
    .bind(media_id)
    .bind(&meta.title)
    .bind(&meta.original_title)
    .bind(meta.year)
    .bind(release_date)
    .bind(end_date)
    .bind(&meta.description)
    .bind(&meta.tagline)
    .bind(meta.runtime_minutes)
    .bind(&meta.status)
    .bind(&meta.original_language)
    .bind(&meta.website)
    .bind(meta.adult)
    .bind(meta.popularity)
    .bind(meta.nudity_status)
    .bind(opts.is_user_created)
    .bind(opts.created_by_user_id)
    .bind(opts.is_add_title_to_poster)
    .bind(opts.is_public)
    .execute(pool)
    .await?;
    Ok(())
}

fn parse_release_date(raw: Option<&str>) -> Option<NaiveDate> {
    raw.and_then(|d| NaiveDate::parse_from_str(d, "%Y-%m-%d").ok())
}

pub async fn store_external_id(pool: &PgPool, media_id: i32, provider: &str, external_id: &str) {
    if external_id.is_empty() {
        return;
    }
    if let Err(e) = sqlx::query(
        "INSERT INTO media_external_id (media_id, provider, external_id, created_at) \
         VALUES ($1, $2, $3, NOW()) \
         ON CONFLICT (provider, external_id) DO NOTHING",
    )
    .bind(media_id)
    .bind(provider)
    .bind(external_id)
    .execute(pool)
    .await
    {
        warn!("store_external_id({provider}, media_id={media_id}): {e}");
    }
}

async fn upsert_external_ids(pool: &PgPool, media_id: MediaId, external_ids: &[(String, String)]) {
    for (provider, ext_id) in external_ids {
        store_external_id(pool, media_id.0, provider, ext_id).await;
    }
}

pub async fn link_genre(pool: &PgPool, media_id: i32, genre_name: &str, media_type: MediaType) {
    // INSERT ... DO NOTHING avoids the hot-row lock that DO UPDATE SET name=EXCLUDED.name
    // causes on high-concurrency ingests.  If the row already exists we fall through to SELECT.
    let genre_id: Option<i32> = sqlx::query_scalar(
        "INSERT INTO genre (name) VALUES ($1) ON CONFLICT (name) DO NOTHING RETURNING id",
    )
    .bind(genre_name)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten();

    let genre_id = match genre_id {
        Some(id) => id,
        None => match sqlx::query_scalar("SELECT id FROM genre WHERE name = $1")
            .bind(genre_name)
            .fetch_optional(pool)
            .await
        {
            Ok(Some(id)) => id,
            _ => return,
        },
    };

    let _ = sqlx::query(
        "INSERT INTO media_genre_link (media_id, genre_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
    )
    .bind(media_id)
    .bind(genre_id)
    .execute(pool)
    .await;

    // Register (genre, media_type) pairing. DO NOTHING preserves any admin is_hidden edits.
    let _ = sqlx::query(
        "INSERT INTO genre_media_type (genre_id, media_type) VALUES ($1, $2) \
         ON CONFLICT (genre_id, media_type) DO NOTHING",
    )
    .bind(genre_id)
    .bind(media_type.as_wire())
    .execute(pool)
    .await;
}

async fn upsert_genres(pool: &PgPool, media_id: i32, genres: &[String], media_type: MediaType) {
    for genre in genres {
        if !genre.is_empty() {
            link_genre(pool, media_id, genre, media_type).await;
        }
    }
}

pub async fn link_to_catalogs(pool: &PgPool, media_id: i32, catalog_ids: &[&str]) {
    for catalog_name in catalog_ids {
        if catalog_name.is_empty() {
            continue;
        }
        let _ = sqlx::query(
            "INSERT INTO catalog (name, display_name, is_system, display_order) \
             VALUES ($1, $1, true, 0) ON CONFLICT (name) DO NOTHING",
        )
        .bind(catalog_name)
        .execute(pool)
        .await;

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

async fn upsert_catalogs(pool: &PgPool, media_id: i32, catalogs: &[String]) {
    for name in catalogs {
        if name.is_empty() {
            continue;
        }
        let _ = sqlx::query(
            "INSERT INTO catalog (name, display_name, is_system, display_order) \
             VALUES ($1, $1, true, 0) ON CONFLICT (name) DO NOTHING",
        )
        .bind(name)
        .execute(pool)
        .await;

        let _ = sqlx::query(
            "INSERT INTO media_catalog_link (media_id, catalog_id) \
             SELECT $1, c.id FROM catalog c WHERE c.name = $2 \
             ON CONFLICT DO NOTHING",
        )
        .bind(media_id)
        .bind(name)
        .execute(pool)
        .await;
    }
}

pub async fn upsert_primary_image(pool: &PgPool, media_id: i32, image_type: &str, url: &str) {
    if url.is_empty() {
        return;
    }
    if let Err(e) = sqlx::query(
        "INSERT INTO media_image \
         (media_id, provider_id, image_type, url, is_primary, display_order) \
         VALUES ($1, 1, $2, $3, true, 0) \
         ON CONFLICT (media_id, provider_id, image_type, url) DO NOTHING",
    )
    .bind(media_id)
    .bind(image_type)
    .bind(url)
    .execute(pool)
    .await
    {
        warn!("upsert_primary_image({image_type}, media_id={media_id}): {e}");
    }
}

async fn upsert_images(pool: &PgPool, media_id: i32, meta: &NormalizedMetadata) {
    if let Some(ref url) = meta.poster_url {
        upsert_primary_image(pool, media_id, "poster", url).await;
    }
    if let Some(ref url) = meta.backdrop_url {
        upsert_primary_image(pool, media_id, "background", url).await;
    }
    if let Some(ref url) = meta.logo_url {
        upsert_primary_image(pool, media_id, "logo", url).await;
    }
}

async fn upsert_series(
    pool: &PgPool,
    media_id: MediaId,
    seasons: &[NormalizedSeason],
    user_created_episodes: bool,
    network: Option<&str>,
) -> Result<(), sqlx::Error> {
    let total_seasons = seasons.len() as i32;
    let total_episodes: i32 = seasons.iter().map(|s| s.episodes.len() as i32).sum();

    let series_id: i32 = sqlx::query_scalar(
        r#"
        INSERT INTO series_metadata (media_id, total_seasons, total_episodes, network, created_at)
        VALUES ($1, $2, $3, $4, NOW())
        ON CONFLICT (media_id) DO UPDATE SET
            total_seasons = GREATEST(series_metadata.total_seasons, EXCLUDED.total_seasons),
            total_episodes = GREATEST(series_metadata.total_episodes, EXCLUDED.total_episodes),
            network = COALESCE(EXCLUDED.network, series_metadata.network),
            updated_at = NOW()
        RETURNING id
        "#,
    )
    .bind(media_id)
    .bind(total_seasons)
    .bind(total_episodes)
    .bind(network)
    .fetch_one(pool)
    .await?;

    for season in seasons {
        upsert_season(pool, series_id, season, user_created_episodes).await?;
    }

    sqlx::query(
        r#"
        UPDATE series_metadata SET
            total_seasons = (SELECT COUNT(*)::int FROM season WHERE series_id = $1),
            total_episodes = (
                SELECT COUNT(*)::int FROM episode e
                JOIN season s ON e.season_id = s.id
                WHERE s.series_id = $1
            ),
            updated_at = NOW()
        WHERE id = $1
        "#,
    )
    .bind(series_id)
    .execute(pool)
    .await?;

    Ok(())
}

async fn upsert_season(
    pool: &PgPool,
    series_id: i32,
    season: &NormalizedSeason,
    user_created_episodes: bool,
) -> Result<(), sqlx::Error> {
    let air_date = parse_release_date(season.air_date.as_deref());
    let episode_count = season.episodes.len() as i32;

    let season_id: i32 = sqlx::query_scalar(
        r#"
        INSERT INTO season (series_id, season_number, name, overview, air_date, episode_count)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (series_id, season_number) DO UPDATE SET
            name = COALESCE(EXCLUDED.name, season.name),
            overview = COALESCE(EXCLUDED.overview, season.overview),
            air_date = COALESCE(EXCLUDED.air_date, season.air_date),
            episode_count = GREATEST(season.episode_count, EXCLUDED.episode_count)
        RETURNING id
        "#,
    )
    .bind(series_id)
    .bind(season.season_number)
    .bind(&season.name)
    .bind(&season.overview)
    .bind(air_date)
    .bind(episode_count)
    .fetch_one(pool)
    .await?;

    for ep in &season.episodes {
        upsert_episode(pool, season_id, ep, user_created_episodes).await?;
    }

    sqlx::query(
        "UPDATE season SET episode_count = \
         (SELECT COUNT(*)::int FROM episode e WHERE e.season_id = $1) \
         WHERE id = $1",
    )
    .bind(season_id)
    .execute(pool)
    .await?;

    Ok(())
}

async fn upsert_episode(
    pool: &PgPool,
    season_id: i32,
    ep: &super::metadata_model::NormalizedEpisode,
    user_created: bool,
) -> Result<(), sqlx::Error> {
    let air_date = parse_release_date(ep.air_date.as_deref());
    let title = if ep.title.is_empty() {
        format!("Episode {}", ep.episode_number)
    } else {
        ep.title.clone()
    };

    let episode_id: i32 = sqlx::query_scalar(
        r#"
        INSERT INTO episode (
            season_id, episode_number, title, overview, air_date, runtime_minutes,
            imdb_id, tmdb_id, tvdb_id,
            is_user_created, is_user_addition, created_at, updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, false, NOW(), NOW())
        ON CONFLICT (season_id, episode_number) DO UPDATE SET
            title = CASE WHEN episode.is_user_created THEN episode.title ELSE EXCLUDED.title END,
            overview = COALESCE(EXCLUDED.overview, episode.overview),
            air_date = COALESCE(EXCLUDED.air_date, episode.air_date),
            runtime_minutes = COALESCE(EXCLUDED.runtime_minutes, episode.runtime_minutes),
            imdb_id = COALESCE(episode.imdb_id, EXCLUDED.imdb_id),
            tmdb_id = COALESCE(episode.tmdb_id, EXCLUDED.tmdb_id),
            tvdb_id = COALESCE(episode.tvdb_id, EXCLUDED.tvdb_id),
            updated_at = NOW()
        RETURNING id
        "#,
    )
    .bind(season_id)
    .bind(ep.episode_number)
    .bind(&title)
    .bind(&ep.overview)
    .bind(air_date)
    .bind(ep.runtime_minutes)
    .bind(&ep.imdb_id)
    .bind(ep.tmdb_id)
    .bind(ep.tvdb_id)
    .bind(user_created)
    .fetch_one(pool)
    .await?;

    if let Some(ref still) = ep.still_url {
        let _ = sqlx::query(
            "INSERT INTO episode_image \
             (episode_id, provider_id, image_type, url, is_primary) \
             VALUES ($1, 1, 'still', $2, true) \
             ON CONFLICT (episode_id, provider_id, image_type, url) DO NOTHING",
        )
        .bind(episode_id)
        .bind(still)
        .execute(pool)
        .await;
    }

    Ok(())
}

async fn upsert_cast(pool: &PgPool, media_id: i32, cast: &[NormalizedCastMember]) {
    if cast.is_empty() {
        return;
    }
    if let Err(e) = sqlx::query("DELETE FROM media_cast WHERE media_id = $1")
        .bind(media_id)
        .execute(pool)
        .await
    {
        warn!("upsert_cast delete media_id={media_id}: {e}");
        return;
    }

    for member in cast {
        let person_id = resolve_person_id(pool, member).await;
        let Some(person_id) = person_id else {
            continue;
        };
        let _ = sqlx::query(
            "INSERT INTO media_cast (media_id, person_id, character, display_order) \
             VALUES ($1, $2, $3, $4)",
        )
        .bind(media_id)
        .bind(person_id)
        .bind(&member.character)
        .bind(member.order)
        .execute(pool)
        .await;
    }
}

async fn resolve_person_id(pool: &PgPool, member: &NormalizedCastMember) -> Option<i32> {
    if let Some(tmdb_id) = member.tmdb_id {
        return sqlx::query_scalar(
            r#"
            INSERT INTO person (name, tmdb_id, profile_url, created_at, updated_at)
            VALUES ($1, $2, $3, NOW(), NOW())
            ON CONFLICT (tmdb_id) DO UPDATE SET
                name = EXCLUDED.name,
                profile_url = COALESCE(EXCLUDED.profile_url, person.profile_url),
                updated_at = NOW()
            RETURNING id
            "#,
        )
        .bind(&member.name)
        .bind(tmdb_id)
        .bind(&member.profile_url)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten();
    }

    if let Ok(Some(id)) = sqlx::query_scalar(
        "SELECT id FROM person WHERE lower(name) = lower($1) ORDER BY id LIMIT 1",
    )
    .bind(&member.name)
    .fetch_optional(pool)
    .await
    {
        return Some(id);
    }

    sqlx::query_scalar(
        r#"
        INSERT INTO person (name, profile_url, created_at, updated_at)
        VALUES ($1, $2, NOW(), NOW())
        RETURNING id
        "#,
    )
    .bind(&member.name)
    .bind(&member.profile_url)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten()
}

async fn upsert_trailers(pool: &PgPool, media_id: i32, trailers: &[NormalizedTrailer]) {
    if trailers.is_empty() {
        return;
    }
    let _ = sqlx::query("DELETE FROM media_trailer WHERE media_id = $1")
        .bind(media_id)
        .execute(pool)
        .await;

    for trailer in trailers {
        let _ = sqlx::query(
            r#"
            INSERT INTO media_trailer (
                media_id, video_key, site, name, trailer_type,
                is_official, is_primary, size, created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW(), NOW())
            ON CONFLICT (media_id, video_key, site) DO UPDATE SET
                name = COALESCE(EXCLUDED.name, media_trailer.name),
                trailer_type = EXCLUDED.trailer_type,
                is_official = EXCLUDED.is_official,
                is_primary = EXCLUDED.is_primary,
                updated_at = NOW()
            "#,
        )
        .bind(media_id)
        .bind(&trailer.video_key)
        .bind(&trailer.site)
        .bind(&trailer.name)
        .bind(&trailer.trailer_type)
        .bind(trailer.is_official)
        .bind(trailer.is_primary)
        .bind(trailer.size)
        .execute(pool)
        .await;
    }
}

async fn upsert_country(pool: &PgPool, media_id: MediaId, country: Option<&str>) {
    let Some(country) = country.filter(|c| !c.is_empty()) else {
        return;
    };
    let _ = sqlx::query(
        r#"
        INSERT INTO tv_metadata (media_id, country, created_at)
        VALUES ($1, $2, NOW())
        ON CONFLICT (media_id) DO UPDATE SET
            country = COALESCE(EXCLUDED.country, tv_metadata.country),
            updated_at = NOW()
        "#,
    )
    .bind(media_id)
    .bind(country)
    .execute(pool)
    .await;
}

async fn upsert_movie_metadata(
    pool: &PgPool,
    media_id: MediaId,
    budget: Option<i64>,
    revenue: Option<i64>,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r#"
        INSERT INTO movie_metadata (media_id, budget, revenue, created_at)
        VALUES ($1, $2, $3, NOW())
        ON CONFLICT (media_id) DO UPDATE SET
            budget = COALESCE(EXCLUDED.budget, movie_metadata.budget),
            revenue = COALESCE(EXCLUDED.revenue, movie_metadata.revenue),
            updated_at = NOW()
        "#,
    )
    .bind(media_id)
    .bind(budget)
    .bind(revenue)
    .execute(pool)
    .await?;
    Ok(())
}

async fn upsert_aka_titles(
    pool: &PgPool,
    media_id: i32,
    primary_title: &str,
    aka_titles: &[super::metadata_model::NormalizedAkaTitle],
) {
    if aka_titles.is_empty() {
        return;
    }
    let _ = sqlx::query("DELETE FROM aka_title WHERE media_id = $1")
        .bind(media_id)
        .execute(pool)
        .await;

    let primary = primary_title.to_ascii_lowercase();
    for aka in aka_titles {
        if aka.title.is_empty() || aka.title.to_ascii_lowercase() == primary {
            continue;
        }
        let _ = sqlx::query(
            "INSERT INTO aka_title (media_id, title, language_code) \
             VALUES ($1, $2, $3) ON CONFLICT (media_id, title) DO NOTHING",
        )
        .bind(media_id)
        .bind(&aka.title)
        .bind(&aka.language_code)
        .execute(pool)
        .await;
    }
}

async fn upsert_keywords(pool: &PgPool, media_id: i32, keywords: &[String]) {
    if keywords.is_empty() {
        return;
    }
    let _ = sqlx::query("DELETE FROM media_keyword_link WHERE media_id = $1")
        .bind(media_id)
        .execute(pool)
        .await;

    for keyword in keywords {
        if keyword.is_empty() {
            continue;
        }
        let keyword_id: Option<i32> = sqlx::query_scalar(
            "INSERT INTO keyword (name) VALUES ($1) \
             ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name \
             RETURNING id",
        )
        .bind(keyword)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten();

        if let Some(kid) = keyword_id {
            let _ = sqlx::query(
                "INSERT INTO media_keyword_link (media_id, keyword_id) \
                 VALUES ($1, $2) ON CONFLICT DO NOTHING",
            )
            .bind(media_id)
            .bind(kid)
            .execute(pool)
            .await;
        }
    }
}

async fn ensure_rating_provider(pool: &PgPool, name: &str, max_rating: f64) -> Option<i32> {
    sqlx::query_scalar(
        r#"
        INSERT INTO rating_provider (name, display_name, max_rating, is_percentage, is_active, display_order)
        VALUES ($1, $1, $2, false, true, 0)
        ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
        "#,
    )
    .bind(name)
    .bind(max_rating)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten()
}

async fn upsert_ratings(pool: &PgPool, media_id: i32, ratings: &[NormalizedRating]) {
    for rating in ratings {
        if rating.rating <= 0.0 {
            continue;
        }
        let max = match rating.provider.as_str() {
            "mal" | "kitsu" | "anilist" => 10.0,
            _ => 10.0,
        };
        let Some(provider_id) = ensure_rating_provider(pool, &rating.provider, max).await else {
            continue;
        };
        let _ = sqlx::query(
            r#"
            INSERT INTO media_rating (
                media_id, rating_provider_id, rating, vote_count, rating_type,
                fetched_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, NOW(), NOW())
            ON CONFLICT (media_id, rating_provider_id, rating_type) DO UPDATE SET
                rating = EXCLUDED.rating,
                vote_count = COALESCE(EXCLUDED.vote_count, media_rating.vote_count),
                updated_at = NOW()
            "#,
        )
        .bind(media_id)
        .bind(provider_id)
        .bind(rating.rating)
        .bind(rating.vote_count)
        .bind(&rating.rating_type)
        .execute(pool)
        .await;
    }
}

async fn upsert_certificates(pool: &PgPool, media_id: i32, certificates: &[String]) {
    if certificates.is_empty() {
        return;
    }
    let _ = sqlx::query("DELETE FROM media_parental_certificate_link WHERE media_id = $1")
        .bind(media_id)
        .execute(pool)
        .await;

    for cert in certificates {
        if cert.is_empty() {
            continue;
        }
        let cert_id: Option<i32> = sqlx::query_scalar(
            "INSERT INTO parental_certificate (name) VALUES ($1) \
             ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name \
             RETURNING id",
        )
        .bind(cert)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten();

        if let Some(cid) = cert_id {
            let _ = sqlx::query(
                "INSERT INTO media_parental_certificate_link (media_id, certificate_id) \
                 VALUES ($1, $2) ON CONFLICT DO NOTHING",
            )
            .bind(media_id)
            .bind(cid)
            .execute(pool)
            .await;
        }
    }
}

async fn upsert_crew(pool: &PgPool, media_id: i32, crew: &[NormalizedCrewMember]) {
    if crew.is_empty() {
        return;
    }
    if let Err(e) = sqlx::query("DELETE FROM media_crew WHERE media_id = $1")
        .bind(media_id)
        .execute(pool)
        .await
    {
        warn!("upsert_crew delete media_id={media_id}: {e}");
        return;
    }

    for member in crew {
        let person_id = resolve_crew_person_id(pool, member).await;
        let Some(person_id) = person_id else {
            continue;
        };
        let _ = sqlx::query(
            "INSERT INTO media_crew (media_id, person_id, department, job) \
             VALUES ($1, $2, $3, $4)",
        )
        .bind(media_id)
        .bind(person_id)
        .bind(&member.department)
        .bind(&member.job)
        .execute(pool)
        .await;
    }
}

async fn resolve_crew_person_id(pool: &PgPool, member: &NormalizedCrewMember) -> Option<i32> {
    if let Some(tmdb_id) = member.tmdb_id {
        return sqlx::query_scalar(
            r#"
            INSERT INTO person (name, tmdb_id, profile_url, created_at, updated_at)
            VALUES ($1, $2, $3, NOW(), NOW())
            ON CONFLICT (tmdb_id) DO UPDATE SET
                name = EXCLUDED.name,
                profile_url = COALESCE(EXCLUDED.profile_url, person.profile_url),
                updated_at = NOW()
            RETURNING id
            "#,
        )
        .bind(&member.name)
        .bind(tmdb_id)
        .bind(&member.profile_url)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten();
    }

    if let Some(ref imdb_id) = member.imdb_id {
        if let Ok(Some(id)) = sqlx::query_scalar("SELECT id FROM person WHERE imdb_id = $1 LIMIT 1")
            .bind(imdb_id)
            .fetch_optional(pool)
            .await
        {
            return Some(id);
        }
    }

    if let Ok(Some(id)) = sqlx::query_scalar(
        "SELECT id FROM person WHERE lower(name) = lower($1) ORDER BY id LIMIT 1",
    )
    .bind(&member.name)
    .fetch_optional(pool)
    .await
    {
        return Some(id);
    }

    sqlx::query_scalar(
        r#"
        INSERT INTO person (name, profile_url, created_at, updated_at)
        VALUES ($1, $2, NOW(), NOW())
        RETURNING id
        "#,
    )
    .bind(&member.name)
    .bind(&member.profile_url)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten()
}
