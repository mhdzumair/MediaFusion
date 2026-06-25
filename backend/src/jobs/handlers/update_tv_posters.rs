use std::collections::HashMap;

use async_trait::async_trait;
use regex::Regex;
use tracing::{info, warn};

use crate::jobs::{
    error::JobError,
    handler::{JobCtx, JobHandler},
};

pub struct UpdateTvPosters;

/// A TV media entry that needs a poster.
struct TvMediaRow {
    id: i32,
    title: String,
}

/// A channel entry from the iptv-org channels.json API.
#[derive(Debug, serde::Deserialize)]
struct IptvChannel {
    name: String,
    #[serde(default)]
    alt_names: Vec<String>,
    #[serde(default)]
    logo: Option<String>,
}

/// Fetch TV media rows that have no primary poster or an empty poster URL.
async fn fetch_tv_without_posters(pool: &sqlx::PgPool) -> Result<Vec<TvMediaRow>, JobError> {
    let rows = sqlx::query(
        r#"
        SELECT m.id, m.title FROM media m
        LEFT JOIN media_image mi ON mi.media_id = m.id
            AND mi.image_type = 'poster'
            AND mi.is_primary = true
        WHERE m.type = 'TV'
          AND (mi.id IS NULL OR mi.url IS NULL OR mi.url = '')
        LIMIT 500
        "#,
    )
    .fetch_all(pool)
    .await?;

    let mut result = Vec::with_capacity(rows.len());
    for row in rows {
        use sqlx::Row;
        result.push(TvMediaRow {
            id: row.try_get("id")?,
            title: row.try_get("title")?,
        });
    }
    Ok(result)
}

/// Fetch channels from iptv-org and build a map of lowercase_name -> logo_url.
async fn fetch_channel_map(http: &reqwest::Client) -> Result<HashMap<String, String>, JobError> {
    let url = "https://iptv-org.github.io/api/channels.json";
    let channels: Vec<IptvChannel> = http
        .get(url)
        .timeout(std::time::Duration::from_secs(30))
        .send()
        .await?
        .json()
        .await?;

    let mut map: HashMap<String, String> = HashMap::with_capacity(channels.len() * 2);
    for ch in channels {
        if let Some(ref logo) = ch.logo
            && !logo.is_empty()
        {
            map.entry(ch.name.to_lowercase())
                .or_insert_with(|| logo.clone());
            for alt in &ch.alt_names {
                map.entry(alt.to_lowercase())
                    .or_insert_with(|| logo.clone());
            }
        }
    }
    Ok(map)
}

/// Strip bracketed/parenthesised suffixes from a TV title for cleaner matching.
/// e.g. "BBC News [UK]" -> "BBC News", "CNN (US)" -> "CNN"
fn normalize_title(title: &str) -> String {
    // Compile regexes once per call — these are cheap for short strings.
    let bracket_re = Regex::new(r"\s*\[.*?\]").unwrap();
    let paren_re = Regex::new(r"\s*\(.*?\)").unwrap();
    let s = bracket_re.replace_all(title, "");
    let s = paren_re.replace_all(&s, "");
    s.trim().to_string()
}

/// Find the best-matching logo URL for a normalized TV title from the channel map.
/// Uses Jaro-Winkler similarity; requires score >= 0.85.
fn best_logo_match(
    normalized_title: &str,
    channel_map: &HashMap<String, String>,
) -> Option<String> {
    let query = normalized_title.to_lowercase();
    let mut best_score = 0.85_f64; // minimum threshold
    let mut best_logo: Option<&String> = None;

    for (ch_name, logo) in channel_map {
        let score = strsim::jaro_winkler(&query, ch_name);
        if score > best_score {
            best_score = score;
            best_logo = Some(logo);
        }
    }

    best_logo.cloned()
}

/// Bulk-upsert matched poster images.
async fn bulk_upsert_posters(
    pool: &sqlx::PgPool,
    media_ids: &[i32],
    urls: &[String],
) -> Result<(), JobError> {
    sqlx::query(
        r#"
        INSERT INTO media_image (media_id, provider_id, image_type, url, is_primary, display_order)
        SELECT UNNEST($1::int[]), 1, 'poster', UNNEST($2::text[]), true, 0
        ON CONFLICT (media_id, provider_id, image_type, url) DO NOTHING
        "#,
    )
    .bind(media_ids)
    .bind(urls)
    .execute(pool)
    .await?;
    Ok(())
}

#[async_trait]
impl JobHandler for UpdateTvPosters {
    const QUEUE: &'static str = "update_tv_posters";
    const CONCURRENCY: usize = 1;
    type Args = serde_json::Value;

    async fn run(&self, _args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        // 1. Fetch TV channels without primary posters.
        let rows = fetch_tv_without_posters(&ctx.state.pool_ro).await?;

        if rows.is_empty() {
            info!("update_tv_posters: all TV channels already have posters, nothing to do");
            return Ok(());
        }

        info!(
            "update_tv_posters: {} TV channel(s) need poster updates",
            rows.len()
        );

        // 2. Fetch iptv-org channels data.
        let channel_map = match fetch_channel_map(&ctx.state.http).await {
            Ok(m) => m,
            Err(e) => {
                warn!("update_tv_posters: failed to fetch iptv-org channels: {e}");
                return Err(e);
            }
        };
        info!(
            "update_tv_posters: loaded {} channel entries from iptv-org",
            channel_map.len()
        );

        // 3. Match each TV entry against the channel map.
        let mut matched_ids: Vec<i32> = Vec::new();
        let mut matched_urls: Vec<String> = Vec::new();

        for row in &rows {
            if ctx.is_cancelled() {
                warn!("update_tv_posters: cancellation requested, stopping early");
                return Err(JobError::Cancelled);
            }

            let normalized = normalize_title(&row.title);
            if let Some(logo_url) = best_logo_match(&normalized, &channel_map) {
                matched_ids.push(row.id);
                matched_urls.push(logo_url);
            }
        }

        if matched_ids.is_empty() {
            info!("update_tv_posters: no matches found above threshold (0.85)");
            return Ok(());
        }

        info!(
            "update_tv_posters: matched {}/{} channels, upserting posters",
            matched_ids.len(),
            rows.len()
        );

        // 4. Bulk upsert matched posters.
        bulk_upsert_posters(&ctx.state.pool, &matched_ids, &matched_urls).await?;

        info!(
            "update_tv_posters: done — upserted {} poster(s)",
            matched_ids.len()
        );
        Ok(())
    }
}
