use async_trait::async_trait;
use fred::prelude::{KeysInterface, SetsInterface};
use tracing::{debug, warn};

use crate::{
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    parser,
    scrapers::{persist, prowlarr::build_series_files, ScrapedStream, SearchMeta, StreamFile},
};

pub struct JackettFeedScraper;

// ─── Jackett API response shapes ──────────────────────────────────────────────

#[derive(Debug, serde::Deserialize)]
#[allow(dead_code)]
struct IndexerEntry {
    #[serde(rename = "id")]
    id: String,
}

#[derive(Debug, serde::Deserialize)]
struct JackettResponse {
    #[serde(rename = "Results", default)]
    results: Vec<JackettResult>,
}

#[derive(Debug, serde::Deserialize)]
struct JackettResult {
    #[serde(rename = "Title")]
    title: Option<String>,
    #[serde(rename = "InfoHash")]
    info_hash: Option<String>,
    #[serde(rename = "MagnetUri")]
    magnet_uri: Option<String>,
    #[serde(rename = "Tracker")]
    tracker: Option<String>,
    #[serde(rename = "Seeders")]
    seeders: Option<i32>,
    #[serde(rename = "Size")]
    size: Option<i64>,
    #[serde(rename = "CategoryDesc")]
    category_desc: Option<String>,
}

// ─── Constants ────────────────────────────────────────────────────────────────

const SEEN_KEY: &str = "jackett_feed:seen";
const SEEN_TTL: i64 = 259_200; // 3 days

// ─── Helpers ─────────────────────────────────────────────────────────────────

fn resolve_info_hash_jackett(result: &JackettResult) -> Option<String> {
    result
        .info_hash
        .as_deref()
        .map(|h| h.to_lowercase())
        .filter(|h| h.len() == 40)
        .or_else(|| {
            result
                .magnet_uri
                .as_deref()
                .and_then(parser::extract_info_hash)
        })
}

fn media_type_from_category_desc(desc: Option<&str>) -> &'static str {
    match desc {
        Some(d) => {
            let lower = d.to_lowercase();
            if lower.contains("tv") || lower.contains("series") || lower.contains("episode") {
                "series"
            } else {
                "movie"
            }
        }
        None => "movie",
    }
}

fn build_scraped_stream_jackett(result: &JackettResult) -> Option<ScrapedStream> {
    let title = result.title.as_deref().unwrap_or("").trim().to_string();
    if title.is_empty() {
        return None;
    }

    let info_hash = resolve_info_hash_jackett(result)?;

    let media_type = media_type_from_category_desc(result.category_desc.as_deref());
    let parsed = parser::parse_title(&title);

    let files: Vec<StreamFile> = if media_type == "series" {
        build_series_files(&parsed, None, None)
    } else {
        vec![]
    };

    let source = result
        .tracker
        .clone()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "Jackett".to_string());

    Some(ScrapedStream {
        info_hash,
        name: title,
        source,
        seeders: result.seeders,
        size: result.size,
        parsed,
        files,
        is_cached: false,
    })
}

// ─── Job handler ──────────────────────────────────────────────────────────────

#[async_trait]
impl JobHandler for JackettFeedScraper {
    const QUEUE: &'static str = "jackett_feed";
    const CONCURRENCY: usize = 2;
    type Args = serde_json::Value;

    async fn run(&self, _args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        let config = &ctx.state.config;

        let (base_url, api_key) = match (&config.jackett_url, &config.jackett_api_key) {
            (Some(u), Some(k)) => (u.clone(), k.clone()),
            _ => {
                debug!("jackett_feed: jackett not configured, skipping");
                return Ok(());
            }
        };

        let client = &ctx.state.http;
        let redis = &ctx.state.redis;
        let pool = &ctx.state.pool;

        if ctx.cancel.is_cancelled() {
            return Err(JobError::Cancelled);
        }

        // Use the all-indexers aggregation JSON endpoint
        let resp = match client
            .get(format!("{base_url}/api/v2.0/indexers/all/results"))
            .query(&[
                ("apikey", api_key.as_str()),
                ("t", "search"),
                ("cat", "2000,5000"),
                ("q", ""),
            ])
            .timeout(std::time::Duration::from_secs(60))
            .send()
            .await
        {
            Ok(r) => r,
            Err(e) => {
                warn!("jackett_feed: search request failed: {e}");
                return Ok(());
            }
        };

        let feed: JackettResponse = match resp.json().await {
            Ok(f) => f,
            Err(e) => {
                warn!("jackett_feed: failed to parse response: {e}");
                return Ok(());
            }
        };

        debug!("jackett_feed: received {} results", feed.results.len());

        let mut movie_streams: Vec<ScrapedStream> = Vec::new();
        let mut series_streams: Vec<ScrapedStream> = Vec::new();

        for result in &feed.results {
            if ctx.cancel.is_cancelled() {
                debug!("jackett_feed: cancelled during processing");
                return Err(JobError::Cancelled);
            }

            let info_hash = match resolve_info_hash_jackett(result) {
                Some(h) => h,
                None => continue,
            };

            // Dedup via Redis
            let seen: bool = redis
                .sismember::<bool, _, _>(SEEN_KEY, &info_hash)
                .await
                .unwrap_or(false);
            if seen {
                continue;
            }

            let _ = redis.sadd::<(), _, _>(SEEN_KEY, info_hash.clone()).await;
            let _ = redis.expire::<i64, _>(SEEN_KEY, SEEN_TTL, None).await;

            if let Some(stream) = build_scraped_stream_jackett(result) {
                let media_type = media_type_from_category_desc(result.category_desc.as_deref());
                debug!(
                    "jackett_feed: new item {} ({}): {}",
                    info_hash, media_type, stream.name
                );
                if media_type == "series" {
                    series_streams.push(stream);
                } else {
                    movie_streams.push(stream);
                }
            }
        }

        if !movie_streams.is_empty() {
            let meta = SearchMeta {
                media_id: 0,
                imdb_id: None,
                title: String::new(),
                year: None,
            };
            persist::write_back(&movie_streams, pool, &meta, "movie", None, None).await;
        }
        if !series_streams.is_empty() {
            let meta = SearchMeta {
                media_id: 0,
                imdb_id: None,
                title: String::new(),
                year: None,
            };
            persist::write_back(&series_streams, pool, &meta, "series", None, None).await;
        }

        Ok(())
    }
}
