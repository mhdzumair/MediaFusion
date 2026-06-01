use async_trait::async_trait;
use serde::Deserialize;
use tracing::{info, warn};

use crate::{
    db::MediaType,
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
};

pub struct DiscoverPrewarm;

/// TMDB `/trending/{type}/week` response shape (only the fields we need).
#[derive(Debug, Deserialize)]
struct TmdbTrendingResponse {
    results: Vec<TmdbEntry>,
}

#[derive(Debug, Deserialize)]
struct TmdbEntry {
    id: i64,
    /// Present on movies.
    title: Option<String>,
    /// Present on TV shows.
    name: Option<String>,
    /// Present on movies.
    release_date: Option<String>,
    /// Present on TV shows.
    first_air_date: Option<String>,
}

impl TmdbEntry {
    fn display_title(&self) -> String {
        self.title
            .clone()
            .or_else(|| self.name.clone())
            .unwrap_or_default()
    }

    fn year(&self) -> Option<i32> {
        let date = self
            .release_date
            .as_deref()
            .or(self.first_air_date.as_deref())?;
        date.split('-').next()?.parse().ok()
    }
}

/// Fetch trending content from TMDB for a given media type ("movie" or "tv").
async fn fetch_trending(
    http: &reqwest::Client,
    media_type: &str,
    api_key: &str,
) -> Result<Vec<TmdbEntry>, JobError> {
    let url = format!("https://api.themoviedb.org/3/trending/{media_type}/week");

    let resp = http
        .get(&url)
        .query(&[("api_key", api_key), ("page", "1")])
        .send()
        .await?
        .error_for_status()?
        .json::<TmdbTrendingResponse>()
        .await?;

    Ok(resp.results)
}

/// Ensure a media row for the TMDB id exists; returns its DB `media.id`.
async fn upsert_media(
    pool: &sqlx::PgPool,
    tmdb_id: i64,
    db_type: &str, // "movie" or "series"
    title: &str,
    year: Option<i32>,
) -> Result<i32, sqlx::Error> {
    // Check if the external id mapping already exists.
    let existing: Option<(i32,)> = sqlx::query_as(
        r#"
        SELECT m.id
        FROM media_external_id mei
        JOIN media m ON m.id = mei.media_id
        WHERE mei.provider = 'tmdb'
          AND mei.external_id = $1
        LIMIT 1
        "#,
    )
    .bind(tmdb_id.to_string())
    .fetch_optional(pool)
    .await?;

    if let Some((id,)) = existing {
        return Ok(id);
    }

    // Create a minimal Media row.
    let (media_id,): (i32,) = sqlx::query_as(
        r#"
        INSERT INTO media (
            type, title, year,
            is_public, is_user_created, adult, is_blocked,
            total_streams, created_at
        ) VALUES (
            $1, $2, $3,
            true, false, false, false,
            0, NOW()
        )
        RETURNING id
        "#,
    )
    .bind(MediaType::from_wire(db_type).unwrap_or(MediaType::Movie))
    .bind(title)
    .bind(year)
    .fetch_one(pool)
    .await?;

    // Record the external ID.
    sqlx::query(
        r#"
        INSERT INTO media_external_id (media_id, provider, external_id)
        VALUES ($1, 'tmdb', $2)
        ON CONFLICT DO NOTHING
        "#,
    )
    .bind(media_id)
    .bind(tmdb_id.to_string())
    .execute(pool)
    .await?;

    Ok(media_id)
}

/// Link a media row to a catalog by name, creating the catalog row if it doesn't exist.
async fn link_to_catalog(
    pool: &sqlx::PgPool,
    media_id: i32,
    catalog_name: &str,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        "INSERT INTO catalog (name, display_name, is_system, display_order) \
         VALUES ($1, $1, true, 0) ON CONFLICT (name) DO NOTHING",
    )
    .bind(catalog_name)
    .execute(pool)
    .await?;

    let catalog_id: Option<(i32,)> = sqlx::query_as("SELECT id FROM catalog WHERE name = $1")
        .bind(catalog_name)
        .fetch_optional(pool)
        .await?;

    let Some((catalog_id,)) = catalog_id else {
        warn!(
            "discover_prewarm: catalog '{}' not found after upsert — skipping",
            catalog_name
        );
        return Ok(());
    };

    sqlx::query(
        r#"
        INSERT INTO media_catalog_link (media_id, catalog_id)
        VALUES ($1, $2)
        ON CONFLICT DO NOTHING
        "#,
    )
    .bind(media_id)
    .bind(catalog_id)
    .execute(pool)
    .await?;

    Ok(())
}

#[async_trait]
impl JobHandler for DiscoverPrewarm {
    const QUEUE: &'static str = "discover_prewarm";
    const CONCURRENCY: usize = 1;
    type Args = serde_json::Value;

    async fn run(&self, _args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        let config = &ctx.state.config;

        if !config.discover_enabled {
            info!("discover_prewarm: discover not enabled — skipping");
            return Ok(());
        }

        let api_key = match config.tmdb_api_key.as_deref() {
            Some(k) => k.to_string(),
            None => {
                info!("discover_prewarm: TMDB API key not configured — skipping");
                return Ok(());
            }
        };

        // (tmdb_media_type, db_media_type, catalog_name)
        // DB mediatype enum uses uppercase values: 'MOVIE', 'SERIES'
        let media_types = [
            ("movie", "MOVIE", "discover_pinned_movies"),
            ("tv", "SERIES", "discover_pinned_series"),
        ];

        for (tmdb_type, db_type, catalog_name) in &media_types {
            if ctx.is_cancelled() {
                return Err(JobError::Cancelled);
            }

            info!("discover_prewarm: fetching TMDB trending {tmdb_type}");

            let entries = match fetch_trending(&ctx.state.http, tmdb_type, &api_key).await {
                Ok(e) => e,
                Err(e) => {
                    warn!("discover_prewarm: TMDB fetch failed for {tmdb_type}: {e}");
                    continue;
                }
            };

            let top20: Vec<&TmdbEntry> = entries.iter().take(20).collect();
            info!(
                "discover_prewarm: {tmdb_type} — {} results from TMDB",
                top20.len()
            );

            let mut linked = 0usize;
            let mut created = 0usize;

            for entry in top20 {
                if ctx.is_cancelled() {
                    return Err(JobError::Cancelled);
                }

                let title = entry.display_title();
                let year = entry.year();

                let media_id =
                    match upsert_media(&ctx.state.pool, entry.id, db_type, &title, year).await {
                        Ok(id) => id,
                        Err(e) => {
                            warn!(
                            "discover_prewarm: upsert_media failed for tmdb_id={} '{title}': {e}",
                            entry.id
                        );
                            continue;
                        }
                    };

                created += 1;

                if let Err(e) = link_to_catalog(&ctx.state.pool, media_id, catalog_name).await {
                    warn!("discover_prewarm: link_to_catalog failed for media_id={media_id}: {e}");
                } else {
                    linked += 1;
                }
            }

            info!(
                "discover_prewarm: {tmdb_type} done — upserted={created} linked={linked} catalog='{catalog_name}'"
            );
        }

        info!("discover_prewarm: complete");
        Ok(())
    }
}
