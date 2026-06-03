use sqlx::PgPool;
use tracing::warn;

use super::types::{MediaId, MediaType};

const ADULT_GENRES: &[&str] = &[
    "adult",
    "18+",
    "xxx",
    "erotic",
    "erotica",
    "pornography",
    "porn",
];

#[derive(sqlx::FromRow, Debug)]
pub struct MediaMetaRow {
    pub media_id: MediaId,
    pub media_type: MediaType,
    pub title: String,
    pub year: Option<i32>,
    pub end_year: Option<i32>,
    pub description: Option<String>,
    pub runtime_minutes: Option<i32>,
    pub website: Option<String>,
    pub imdb_id: Option<String>,
    pub tmdb_id: Option<String>,
    pub poster_url: Option<String>,
    pub background_url: Option<String>,
    pub logo_url: Option<String>,
    pub imdb_rating: Option<f64>,
    pub language: Option<String>,
    pub country: Option<String>,
}

#[derive(sqlx::FromRow, Debug)]
pub struct EpisodeRow {
    pub season_number: i32,
    pub episode_number: i32,
    pub ep_title: Option<String>,
    pub overview: Option<String>,
    pub air_date: Option<chrono::NaiveDate>,
    pub thumbnail_url: Option<String>,
    pub media_id: Option<MediaId>,
}

/// Resolve a Stremio meta_id to an internal media_id.
/// Handles both "tt1234567" (imdb) and "mf12345" (internal) formats.
fn parse_meta_id(meta_id: &str) -> MetaIdKind<'_> {
    if let Some(num) = meta_id.strip_prefix("mf") {
        if let Ok(id) = num.parse::<i32>() {
            return MetaIdKind::Internal(MediaId(id));
        }
    }
    MetaIdKind::External(meta_id)
}

enum MetaIdKind<'a> {
    Internal(MediaId),
    External(&'a str),
}

pub async fn get_media_meta(
    pool: &PgPool,
    meta_id: &str,
    media_type: &str,
) -> Option<MediaMetaRow> {
    let media_type = MediaType::from_wire(media_type)?;

    let media_id = match parse_meta_id(meta_id) {
        MetaIdKind::Internal(id) => id,
        MetaIdKind::External(ext_id) => {
            crate::db::get_media_id_by_external_id(pool, ext_id, Some(media_type.as_wire()))
                .await
                .ok()
                .flatten()?
        }
    };

    fetch_media_meta_by_id(pool, media_id, media_type).await
}

async fn fetch_media_meta_by_id(
    pool: &PgPool,
    media_id: MediaId,
    media_type: MediaType,
) -> Option<MediaMetaRow> {
    sqlx::query_as::<_, MediaMetaRow>(
        r#"
        SELECT
            m.id AS media_id,
            m.type AS media_type,
            m.title,
            m.year,
            EXTRACT(YEAR FROM m.end_date)::int AS end_year,
            m.description,
            m.runtime_minutes,
            m.website,
            m.original_language AS language,
            tv.country,
            mei_imdb.external_id AS imdb_id,
            mei_tmdb.external_id AS tmdb_id,
            mi_poster.url AS poster_url,
            mi_bg.url AS background_url,
            mi_logo.url AS logo_url,
            mr.rating AS imdb_rating
        FROM media m
        LEFT JOIN media_external_id mei_imdb
            ON mei_imdb.media_id = m.id AND mei_imdb.provider = 'imdb'
        LEFT JOIN media_external_id mei_tmdb
            ON mei_tmdb.media_id = m.id AND mei_tmdb.provider = 'tmdb'
        LEFT JOIN tv_metadata tv ON tv.media_id = m.id
        LEFT JOIN LATERAL (
            SELECT url FROM media_image
            WHERE media_id = m.id AND image_type = 'poster' AND is_primary = true
            LIMIT 1
        ) mi_poster ON true
        LEFT JOIN LATERAL (
            SELECT url FROM media_image
            WHERE media_id = m.id AND image_type = 'background' AND is_primary = true
            LIMIT 1
        ) mi_bg ON true
        LEFT JOIN LATERAL (
            SELECT url FROM media_image
            WHERE media_id = m.id AND image_type = 'logo' AND is_primary = true
            LIMIT 1
        ) mi_logo ON true
        LEFT JOIN LATERAL (
            SELECT r.rating FROM media_rating r
            JOIN rating_provider rp ON rp.id = r.rating_provider_id
            WHERE r.media_id = m.id AND lower(rp.name) IN ('imdb', 'tmdb')
            ORDER BY CASE lower(rp.name) WHEN 'imdb' THEN 0 ELSE 1 END
            LIMIT 1
        ) mr ON true
        WHERE m.id = $1 AND m.type = $2
        LIMIT 1
        "#,
    )
    .bind(media_id)
    .bind(media_type)
    .fetch_optional(pool)
    .await
    .unwrap_or_else(|e| {
        warn!("meta query [media_id={media_id}]: {e}");
        None
    })
}

pub async fn get_genres(pool: &PgPool, media_id: MediaId) -> Vec<String> {
    sqlx::query_scalar!(
        r#"
        SELECT g.name
        FROM genre g
        JOIN media_genre_link mgl ON mgl.genre_id = g.id
        WHERE mgl.media_id = $1
          AND lower(g.name) <> ALL($2)
        ORDER BY g.name
        "#,
        media_id as MediaId,
        ADULT_GENRES as &[&str],
    )
    .fetch_all(pool)
    .await
    .unwrap_or_else(|e| {
        warn!("genres for media {media_id}: {e}");
        vec![]
    })
}

pub async fn get_cast(pool: &PgPool, media_id: MediaId) -> Vec<String> {
    sqlx::query_scalar!(
        r#"
        SELECT p.name
        FROM person p
        JOIN media_cast mc ON mc.person_id = p.id
        WHERE mc.media_id = $1
        ORDER BY mc.display_order
        LIMIT 10
        "#,
        media_id as MediaId,
    )
    .fetch_all(pool)
    .await
    .unwrap_or_else(|e| {
        warn!("cast for media {media_id}: {e}");
        vec![]
    })
}

pub async fn get_episodes(pool: &PgPool, media_id: MediaId) -> Vec<EpisodeRow> {
    // Use non-macro query_as so LEFT JOIN LATERAL (thumbnail_url) and LEFT JOIN
    // (media_id) columns decode as nullable via the struct's Option<T> field types.
    sqlx::query_as::<_, EpisodeRow>(
        r#"
        SELECT
            s.season_number,
            e.episode_number,
            e.title AS ep_title,
            e.overview,
            e.air_date,
            ei.url AS thumbnail_url,
            fml.media_id AS media_id
        FROM series_metadata sm
        JOIN season s ON s.series_id = sm.id
        JOIN episode e ON e.season_id = s.id
        LEFT JOIN LATERAL (
            SELECT url FROM episode_image
            WHERE episode_id = e.id AND image_type = 'still' AND is_primary = true
            LIMIT 1
        ) ei ON true
        LEFT JOIN file_media_link fml
            ON fml.media_id = $1
            AND fml.season_number = s.season_number
            AND fml.episode_number = e.episode_number
        WHERE sm.media_id = $1
        ORDER BY s.season_number, e.episode_number
        "#,
    )
    .bind(media_id)
    .fetch_all(pool)
    .await
    .unwrap_or_else(|e| {
        warn!("episodes for media {media_id}: {e}");
        vec![]
    })
}
