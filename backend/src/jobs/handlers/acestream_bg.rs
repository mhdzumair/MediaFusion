/// Background AceStream scraper.
///
/// Fetches AceStream sources configured via the `config_manager` scraper config
/// (key `acestream_background`). Source URLs and search-API endpoints are defined
/// in the operator's YAML/JSON config file rather than in AppConfig env-vars, so
/// this handler reads them at runtime via the `config_manager` utility — mirroring
/// the Python `_fetch_acestream_candidates()` flow.
///
/// For each discovered AceStream content_id the handler:
///   1. Deduplicates via the Redis set `acestream_bg:seen`.
///   2. Inserts a stream row + acestream_stream row if absent.
///   3. Marks the content_id seen in Redis (TTL 7 days).
use async_trait::async_trait;
use fred::prelude::{KeysInterface, SetsInterface};
use once_cell::sync::Lazy;
use regex::Regex;
use tracing::{info, warn};

use crate::jobs::{
    error::JobError,
    handler::{JobCtx, JobHandler},
};

// ─── Regex patterns ───────────────────────────────────────────────────────────

static ACESTREAM_URI_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"acestream://([a-fA-F0-9]{40})").expect("acestream uri regex"));

static ACESTREAM_ANCHOR_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r#"<a[^>]*href=["']acestream://(?P<cid>[a-fA-F0-9]{40})["'][^>]*>(?P<title>[^<]+)</a>"#,
    )
    .expect("acestream anchor regex")
});

static INFOHASH_PARAM_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?:infohash|info_hash)=([a-fA-F0-9]{40})").expect("infohash param regex")
});

// ─── Redis keys / TTL ─────────────────────────────────────────────────────────

const SEEN_KEY: &str = "acestream_bg:seen";
const SEEN_TTL: i64 = 604_800; // 7 days

// ─── Config helpers ───────────────────────────────────────────────────────────

/// A minimal, parsed representation of one source entry from the scraper config.
#[derive(Debug)]
struct AceStreamSource {
    /// Human-readable name used as `source` on the inserted stream row.
    name: String,
    /// List of URLs to fetch in order.
    urls: Vec<String>,
}

/// Read acestream_background sources from the operator config file.
///
/// The config is loaded from `SCRAPER_CONFIG_PATH` (defaults to
/// `config/scraper_config.yaml`) by `config_manager`.  If no config or no
/// sources are present the function returns an empty Vec so the handler exits
/// gracefully.
fn load_sources(config_path: &str) -> Vec<AceStreamSource> {
    let text = match std::fs::read_to_string(config_path) {
        Ok(t) => t,
        Err(_) => return vec![],
    };

    // Parse as JSON. YAML-only configs are not supported here; operators
    // should use JSON or YAML that is also valid JSON.
    let root: serde_json::Value = match serde_json::from_str(&text) {
        Ok(v) => v,
        Err(_) => return vec![],
    };

    let source_items = match root
        .get("acestream_background")
        .and_then(|v| v.get("sources"))
        .and_then(|v| v.as_array())
    {
        Some(arr) => arr.clone(),
        None => return vec![],
    };

    let mut sources = Vec::new();
    for item in source_items {
        if item.get("enabled").and_then(|v| v.as_bool()) == Some(false) {
            continue;
        }

        // Collect URLs from either `urls` (array) or `url` (scalar).
        let urls: Vec<String> = if let Some(arr) = item.get("urls").and_then(|v| v.as_array()) {
            arr.iter()
                .filter_map(|v| v.as_str().map(str::to_string))
                .filter(|s| !s.is_empty())
                .collect()
        } else if let Some(s) = item.get("url").and_then(|v| v.as_str()) {
            if s.is_empty() {
                vec![]
            } else {
                vec![s.to_string()]
            }
        } else {
            vec![]
        };

        if urls.is_empty() {
            continue;
        }

        let name = item
            .get("name")
            .and_then(|v| v.as_str())
            .unwrap_or(&urls[0])
            .to_string();

        sources.push(AceStreamSource { name, urls });
    }

    sources
}

// ─── Candidate extraction ─────────────────────────────────────────────────────

#[derive(Debug)]
struct AceCandidate {
    content_id: Option<String>,
    info_hash: Option<String>,
    title: Option<String>,
}

fn extract_candidates(body: &str, source_name: &str) -> Vec<AceCandidate> {
    let mut candidates: Vec<AceCandidate> = Vec::new();

    // Anchored links: <a href="acestream://…">Title</a>
    for cap in ACESTREAM_ANCHOR_RE.captures_iter(body) {
        let cid = cap.name("cid").map(|m| m.as_str().to_lowercase());
        let title = cap
            .name("title")
            .map(|m| m.as_str().trim().to_string())
            .filter(|s| !s.is_empty());
        if let Some(content_id) = cid {
            candidates.push(AceCandidate {
                content_id: Some(content_id),
                info_hash: None,
                title,
            });
        }
    }

    // Plain acestream:// URIs
    for cap in ACESTREAM_URI_RE.captures_iter(body) {
        let cid = cap[1].to_lowercase();
        // Skip if already captured by anchor pass
        if candidates
            .iter()
            .any(|c| c.content_id.as_deref() == Some(&cid))
        {
            continue;
        }
        candidates.push(AceCandidate {
            content_id: Some(cid),
            info_hash: None,
            title: None,
        });
    }

    // Infohash query params: ?infohash=…
    for cap in INFOHASH_PARAM_RE.captures_iter(body) {
        let ih = cap[1].to_lowercase();
        candidates.push(AceCandidate {
            content_id: None,
            info_hash: Some(ih),
            title: None,
        });
    }

    if candidates.is_empty() {
        tracing::debug!(
            "acestream_bg: no candidates in response from {}",
            source_name
        );
    }

    candidates
}

// ─── DB helpers ───────────────────────────────────────────────────────────────

/// Returns the stream_id if an acestream_stream row already exists for this
/// content_id, otherwise None.
async fn find_existing_stream(
    pool: &sqlx::PgPool,
    content_id: &str,
) -> Result<Option<i64>, sqlx::Error> {
    let row: Option<(i64,)> =
        sqlx::query_as("SELECT stream_id FROM acestream_stream WHERE content_id = $1 LIMIT 1")
            .bind(content_id)
            .fetch_optional(pool)
            .await?;
    Ok(row.map(|(id,)| id))
}

/// Insert a new stream + acestream_stream row.  Returns the new stream_id on
/// success, or None if insertion conflicted / failed.
async fn insert_acestream_stream(
    pool: &sqlx::PgPool,
    name: &str,
    source: &str,
    content_id: &str,
    info_hash: Option<&str>,
) -> Result<Option<i64>, sqlx::Error> {
    let row: Option<(i64,)> = sqlx::query_as(
        r#"INSERT INTO stream (
            stream_type, name, source,
            is_active, is_blocked, is_public, playback_count,
            is_remastered, is_upscaled, is_proper, is_repack,
            is_extended, is_complete, is_dubbed, is_subbed,
            created_at, updated_at
        ) VALUES (
            'ACESTREAM'::streamtype, $1, $2,
            true, false, true, 0,
            false, false, false, false,
            false, false, false, false,
            NOW(), NOW()
        ) RETURNING id"#,
    )
    .bind(name)
    .bind(source)
    .fetch_optional(pool)
    .await?;

    let stream_id = match row {
        Some((id,)) => id,
        None => return Ok(None),
    };

    sqlx::query(
        "INSERT INTO acestream_stream (stream_id, content_id, info_hash)
         VALUES ($1, $2, $3)
         ON CONFLICT (stream_id) DO NOTHING",
    )
    .bind(stream_id)
    .bind(content_id)
    .bind(info_hash)
    .execute(pool)
    .await?;

    Ok(Some(stream_id))
}

// ─── Handler ──────────────────────────────────────────────────────────────────

pub struct AcestreamBgScraper;

#[async_trait]
impl JobHandler for AcestreamBgScraper {
    const QUEUE: &'static str = "acestream_bg";
    const CONCURRENCY: usize = 1;
    type Args = serde_json::Value;

    async fn run(&self, _args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        let sources = load_sources(&ctx.state.config.scraper_config_path);

        if sources.is_empty() {
            info!("acestream_bg: no sources configured, nothing to do");
            return Ok(());
        }

        info!(
            "acestream_bg: processing {} configured source(s)",
            sources.len()
        );

        let pool = &ctx.state.pool;
        let redis = &ctx.state.redis;
        let http = &ctx.state.http;

        let mut total_seen = 0usize;
        let mut total_inserted = 0usize;
        let mut total_skipped = 0usize;

        for source in &sources {
            if ctx.is_cancelled() {
                warn!("acestream_bg: cancellation requested, stopping early");
                return Err(JobError::Cancelled);
            }

            for url in &source.urls {
                let body = match http
                    .get(url)
                    .timeout(std::time::Duration::from_secs(30))
                    .send()
                    .await
                {
                    Ok(r) if r.status().is_success() => match r.text().await {
                        Ok(t) => t,
                        Err(e) => {
                            warn!("acestream_bg: failed to read body from {}: {e}", url);
                            continue;
                        }
                    },
                    Ok(r) => {
                        warn!("acestream_bg: HTTP {} from {}", r.status().as_u16(), url);
                        continue;
                    }
                    Err(e) => {
                        warn!("acestream_bg: fetch error for {}: {e}", url);
                        continue;
                    }
                };

                let candidates = extract_candidates(&body, &source.name);
                total_seen += candidates.len();

                for candidate in candidates {
                    let dedup_key = match &candidate.content_id {
                        Some(cid) => cid.clone(),
                        None => match &candidate.info_hash {
                            Some(ih) => ih.clone(),
                            None => continue,
                        },
                    };

                    // Redis dedup
                    let already_seen: bool = redis
                        .sismember::<bool, _, _>(SEEN_KEY, &dedup_key)
                        .await
                        .unwrap_or(false);
                    if already_seen {
                        total_skipped += 1;
                        continue;
                    }

                    // Use content_id as the lookup key when available
                    if let Some(ref content_id) = candidate.content_id {
                        match find_existing_stream(pool, content_id).await {
                            Ok(Some(_)) => {
                                total_skipped += 1;
                            }
                            Ok(None) => {
                                let name = candidate
                                    .title
                                    .as_deref()
                                    .filter(|s| !s.is_empty())
                                    .unwrap_or("AceStream");

                                match insert_acestream_stream(
                                    pool,
                                    name,
                                    &source.name,
                                    content_id,
                                    candidate.info_hash.as_deref(),
                                )
                                .await
                                {
                                    Ok(Some(_)) => {
                                        total_inserted += 1;
                                    }
                                    Ok(None) => {
                                        total_skipped += 1;
                                    }
                                    Err(e) => {
                                        warn!(
                                            "acestream_bg: DB insert error for {}: {e}",
                                            content_id
                                        );
                                    }
                                }
                            }
                            Err(e) => {
                                warn!("acestream_bg: DB lookup error for {}: {e}", content_id);
                            }
                        }
                    }

                    // Mark seen regardless of DB outcome
                    let _ = redis.sadd::<(), _, _>(SEEN_KEY, dedup_key).await;
                    let _ = redis.expire::<i64, _>(SEEN_KEY, SEEN_TTL, None).await;
                }
            }
        }

        info!(
            "acestream_bg: done — seen={} inserted={} skipped={}",
            total_seen, total_inserted, total_skipped
        );

        Ok(())
    }
}
