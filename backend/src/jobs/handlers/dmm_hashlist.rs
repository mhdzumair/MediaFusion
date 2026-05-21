/// Incremental DMM hashlist scraper.
///
/// Mirrors the Python `DMMHashlistScraper` in `workers/scrapers/dmm_hashlist.py`.
///
/// Algorithm (incremental pass):
///   1. Fetch the latest N commits from the GitHub repo.
///   2. Stop at the commit SHA stored in Redis under `dmm_hashlist_scraper:latest_commit_sha`.
///   3. For each new commit, iterate its changed HTML files.
///      - Skip file blobs already in `dmm_hashlist_scraper:processed_file_shas`.
///      - Fetch the raw file, extract the DMM iframe hash fragment, decompress
///        the LZString-encoded JSON payload, and store new torrent_stream rows.
///      - Mark blob SHA as processed.
///   4. Update `dmm_hashlist_scraper:latest_commit_sha` to the head SHA.
///
/// Backfill pass (optional, walks parents of the oldest known commit):
///   Uses `dmm_hashlist_scraper:backfill_next_commit_sha`.
///
/// The GitHub repo and branch are read from env vars:
///   DMM_HASHLIST_REPO_OWNER  (default: "debridmediamanager")
///   DMM_HASHLIST_REPO_NAME   (default: "stash")
///   DMM_HASHLIST_BRANCH      (default: "main")
///   DMM_HASHLIST_COMMITS_PER_RUN         (default: 30)
///   DMM_HASHLIST_BACKFILL_COMMITS_PER_RUN (default: 30)
///   GITHUB_TOKEN             (optional, for higher rate-limits)
use async_trait::async_trait;
use fred::prelude::{KeysInterface, SetsInterface};
use once_cell::sync::Lazy;
use regex::Regex;
use tracing::{debug, info, warn};

use crate::{
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    parser,
    scrapers::media_resolve,
};

// ─── Redis key constants (must match Python side) ─────────────────────────────

const LATEST_SHA_KEY: &str = "dmm_hashlist_scraper:latest_commit_sha";
const BACKFILL_SHA_KEY: &str = "dmm_hashlist_scraper:backfill_next_commit_sha";
const PROCESSED_FILES_KEY: &str = "dmm_hashlist_scraper:processed_file_shas";

const BACKFILL_DONE: &str = "__done__";

// ─── Regex ────────────────────────────────────────────────────────────────────

/// Extracts the base64url-encoded LZString payload from the DMM iframe wrapper.
/// `<iframe src="https://debridmediamanager.com/hashlist#{payload}"></iframe>`
static IFRAME_FRAG_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"<iframe\s+src="https://debridmediamanager\.com/hashlist#([^"]+)"></iframe>"#)
        .expect("iframe fragment regex")
});

static INFO_HASH_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^[0-9a-fA-F]{40}$").expect("info hash regex"));

// ─── Config helpers ───────────────────────────────────────────────────────────

struct DmmConfig {
    owner: String,
    repo: String,
    branch: String,
    max_incremental: usize,
    max_backfill: usize,
    github_token: Option<String>,
}

impl DmmConfig {
    fn from_env() -> Self {
        fn env(key: &str) -> Option<String> {
            std::env::var(key).ok().filter(|s| !s.is_empty())
        }
        DmmConfig {
            owner: env("DMM_HASHLIST_REPO_OWNER").unwrap_or_else(|| "debridmediamanager".into()),
            repo: env("DMM_HASHLIST_REPO_NAME").unwrap_or_else(|| "stash".into()),
            branch: env("DMM_HASHLIST_BRANCH").unwrap_or_else(|| "main".into()),
            max_incremental: env("DMM_HASHLIST_COMMITS_PER_RUN")
                .and_then(|v| v.parse().ok())
                .unwrap_or(30),
            max_backfill: env("DMM_HASHLIST_BACKFILL_COMMITS_PER_RUN")
                .and_then(|v| v.parse().ok())
                .unwrap_or(30),
            github_token: env("GITHUB_TOKEN"),
        }
    }
}

// ─── GitHub API helpers ───────────────────────────────────────────────────────

async fn github_get_json(
    http: &reqwest::Client,
    url: &str,
    token: Option<&str>,
) -> Result<serde_json::Value, JobError> {
    let mut req = http
        .get(url)
        .header("User-Agent", "mediafusion")
        .header("Accept", "application/vnd.github+json");
    if let Some(tok) = token {
        req = req.header("Authorization", format!("Bearer {tok}"));
    }
    let resp = req
        .timeout(std::time::Duration::from_secs(40))
        .send()
        .await?;
    if !resp.status().is_success() {
        return Err(JobError::other(format!(
            "GitHub API {} returned HTTP {}",
            url,
            resp.status()
        )));
    }
    let val: serde_json::Value = resp.json().await?;
    Ok(val)
}

async fn github_get_text(
    http: &reqwest::Client,
    url: &str,
    token: Option<&str>,
) -> Result<String, JobError> {
    let mut req = http
        .get(url)
        .header("User-Agent", "mediafusion")
        .timeout(std::time::Duration::from_secs(40));
    if let Some(tok) = token {
        req = req.header("Authorization", format!("Bearer {tok}"));
    }
    let resp = req.send().await?;
    if !resp.status().is_success() {
        return Err(JobError::other(format!(
            "raw fetch {} returned HTTP {}",
            url,
            resp.status()
        )));
    }
    Ok(resp.text().await?)
}

// ─── Payload decoding ─────────────────────────────────────────────────────────

#[derive(Debug)]
struct HashlistEntry {
    filename: String,
    info_hash: String,
    size: i64,
}

fn extract_hash_fragment(html: &str) -> Option<String> {
    let caps = IFRAME_FRAG_RE.captures(html)?;
    Some(caps[1].to_string())
}

fn decode_hashlist_payload(encoded: &str) -> Vec<HashlistEntry> {
    // Decompress using the lz-str crate (LZString.decompressFromEncodedURIComponent)
    let decompressed = match lz_str::decompress_from_encoded_uri_component(encoded) {
        Some(d) => d,
        None => {
            warn!("dmm_hashlist: LZString decompression returned None");
            return vec![];
        }
    };

    // lz_str returns Vec<u16>; convert to UTF-16 string
    let json_str = match String::from_utf16(&decompressed) {
        Ok(s) => s,
        Err(e) => {
            warn!("dmm_hashlist: UTF-16 decode error: {e}");
            return vec![];
        }
    };

    let payload: serde_json::Value = match serde_json::from_str(&json_str) {
        Ok(v) => v,
        Err(e) => {
            warn!("dmm_hashlist: JSON parse error: {e}");
            return vec![];
        }
    };

    let torrent_rows: &Vec<serde_json::Value> = match &payload {
        serde_json::Value::Array(arr) => arr,
        serde_json::Value::Object(obj) => match obj.get("torrents") {
            Some(serde_json::Value::Array(arr)) => arr,
            _ => return vec![],
        },
        _ => return vec![],
    };

    let mut entries = Vec::new();
    for row in torrent_rows {
        let obj = match row.as_object() {
            Some(o) => o,
            None => continue,
        };

        let filename = match obj.get("filename").and_then(|v| v.as_str()) {
            Some(s) if !s.is_empty() => s.to_string(),
            _ => continue,
        };

        let info_hash = match obj.get("hash").and_then(|v| v.as_str()) {
            Some(s) => s.to_lowercase(),
            None => continue,
        };

        if !INFO_HASH_RE.is_match(&info_hash) {
            continue;
        }

        let size = obj
            .get("bytes")
            .and_then(|v| v.as_i64())
            .unwrap_or(0)
            .max(0);

        entries.push(HashlistEntry {
            filename,
            info_hash,
            size,
        });
    }

    // Deduplicate by info_hash (keep first occurrence)
    let mut seen = std::collections::HashSet::new();
    entries.retain(|e| seen.insert(e.info_hash.clone()));

    entries
}

// ─── DB helpers ───────────────────────────────────────────────────────────────

/// Store a single torrent stream with resolved media linking (Python `dmm_hashlist` parity).
///
/// Returns true if a new row was inserted, false if already present.
async fn store_torrent_stream(
    pool: &sqlx::PgPool,
    http: &reqwest::Client,
    entry: &HashlistEntry,
    tmdb_api_key: Option<&str>,
    cinemeta_fallback: bool,
    anime_source_order: &[String],
    metadata_primary_source: &str,
) -> Result<bool, sqlx::Error> {
    // Check existing
    let existing: Option<(i32,)> =
        sqlx::query_as("SELECT stream_id FROM torrent_stream WHERE info_hash = $1")
            .bind(&entry.info_hash)
            .fetch_optional(pool)
            .await?;

    if existing.is_some() {
        return Ok(false);
    }

    // Insert base stream
    let row: Option<(i32,)> = sqlx::query_as(
        r#"INSERT INTO stream (
            stream_type, name, source,
            is_active, is_blocked, is_public, playback_count,
            is_remastered, is_upscaled, is_proper, is_repack,
            is_extended, is_complete, is_dubbed, is_subbed,
            created_at, updated_at
        ) VALUES (
            'TORRENT'::streamtype, $1, 'dmm_hashlist',
            true, false, true, 0,
            false, false, false, false,
            false, false, false, false,
            NOW(), NOW()
        ) RETURNING id"#,
    )
    .bind(&entry.filename)
    .fetch_optional(pool)
    .await?;

    let stream_id = match row {
        Some((id,)) => id,
        None => return Ok(false),
    };

    let ts = sqlx::query(
        r#"INSERT INTO torrent_stream (stream_id, info_hash, total_size, seeders, torrent_type, file_count, created_at)
           VALUES ($1, $2, $3, 0, 'PUBLIC'::torrenttype, 1, NOW())
           ON CONFLICT (info_hash) DO NOTHING"#,
    )
    .bind(stream_id)
    .bind(&entry.info_hash)
    .bind(entry.size)
    .execute(pool)
    .await?;

    if ts.rows_affected() == 0 {
        // Hash raced in between our SELECT and INSERT — clean up orphan stream row
        let _ = sqlx::query("DELETE FROM stream WHERE id = $1")
            .bind(stream_id)
            .execute(pool)
            .await;
        return Ok(false);
    }

    let parsed = parser::parse_title(&entry.filename);
    let is_series = !parsed.seasons.is_empty() || !parsed.episodes.is_empty();
    let title = parsed
        .title
        .as_deref()
        .filter(|t| !t.is_empty())
        .unwrap_or(entry.filename.as_str());
    if let Some(meta) = media_resolve::search_meta_for_title_with_anime(
        pool,
        http,
        title,
        parsed.year,
        is_series,
        tmdb_api_key,
        cinemeta_fallback,
        anime_source_order,
        metadata_primary_source,
    )
    .await
    {
        let _ = media_resolve::link_stream_to_media(pool, stream_id, meta.media_id as i32).await;
    }

    Ok(true)
}

// ─── Commit processing ────────────────────────────────────────────────────────

struct CommitStats {
    files_processed: usize,
    streams_created: usize,
    next_parent_sha: Option<String>,
}

async fn process_commit(
    http: &reqwest::Client,
    pool: &sqlx::PgPool,
    redis: &fred::clients::Client,
    cfg: &DmmConfig,
    commit_sha: &str,
    tmdb_api_key: Option<&str>,
    cinemeta_fallback: bool,
    anime_source_order: &[String],
    metadata_primary_source: &str,
) -> Result<CommitStats, JobError> {
    let commit_url = format!(
        "https://api.github.com/repos/{}/{}/commits/{}",
        cfg.owner, cfg.repo, commit_sha
    );
    let commit_data = github_get_json(http, &commit_url, cfg.github_token.as_deref()).await?;

    let next_parent_sha = commit_data
        .get("parents")
        .and_then(|v| v.as_array())
        .and_then(|arr| arr.first())
        .and_then(|p| p.get("sha"))
        .and_then(|v| v.as_str())
        .map(str::to_string);

    let files = match commit_data.get("files").and_then(|v| v.as_array()) {
        Some(f) => f.clone(),
        None => vec![],
    };

    let mut files_processed = 0usize;
    let mut streams_created = 0usize;

    for file_data in &files {
        let file_path = match file_data.get("filename").and_then(|v| v.as_str()) {
            Some(p) => p,
            None => continue,
        };

        if !file_path.ends_with(".html") {
            continue;
        }

        let blob_sha = file_data.get("sha").and_then(|v| v.as_str()).unwrap_or("");

        // Check if already processed
        if !blob_sha.is_empty() {
            let seen: bool = redis
                .sismember::<bool, _, _>(PROCESSED_FILES_KEY, blob_sha)
                .await
                .unwrap_or(false);
            if seen {
                continue;
            }
        }

        // Prefer raw_url from API response, fall back to constructed URL
        let raw_url = file_data
            .get("raw_url")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .map(str::to_string)
            .unwrap_or_else(|| {
                format!(
                    "https://raw.githubusercontent.com/{}/{}/{}/{}",
                    cfg.owner, cfg.repo, cfg.branch, file_path
                )
            });

        let html = match github_get_text(http, &raw_url, cfg.github_token.as_deref()).await {
            Ok(h) => h,
            Err(e) => {
                warn!("dmm_hashlist: failed to fetch {}: {e}", file_path);
                // Still mark as seen to avoid retrying a permanently broken file
                if !blob_sha.is_empty() {
                    let _ = redis.sadd::<(), _, _>(PROCESSED_FILES_KEY, blob_sha).await;
                }
                continue;
            }
        };

        let encoded_payload = match extract_hash_fragment(&html) {
            Some(p) => p,
            None => {
                debug!("dmm_hashlist: no hash fragment in {}", file_path);
                if !blob_sha.is_empty() {
                    let _ = redis.sadd::<(), _, _>(PROCESSED_FILES_KEY, blob_sha).await;
                }
                continue;
            }
        };

        let entries = decode_hashlist_payload(&encoded_payload);
        let mut file_new = 0usize;

        for entry in &entries {
            match store_torrent_stream(
                pool,
                http,
                entry,
                tmdb_api_key,
                cinemeta_fallback,
                anime_source_order,
                metadata_primary_source,
            )
            .await
            {
                Ok(true) => file_new += 1,
                Ok(false) => {}
                Err(e) => {
                    warn!("dmm_hashlist: DB error storing {}: {e}", entry.info_hash);
                }
            }
        }

        streams_created += file_new;
        files_processed += 1;

        if !blob_sha.is_empty() {
            let _ = redis.sadd::<(), _, _>(PROCESSED_FILES_KEY, blob_sha).await;
        }

        info!(
            "dmm_hashlist: file={} entries={} new={}",
            file_path,
            entries.len(),
            file_new
        );
    }

    Ok(CommitStats {
        files_processed,
        streams_created,
        next_parent_sha,
    })
}

// ─── Handler ──────────────────────────────────────────────────────────────────

pub struct DmmHashlistScraper;

#[async_trait]
impl JobHandler for DmmHashlistScraper {
    const QUEUE: &'static str = "dmm_hashlist";
    const CONCURRENCY: usize = 1;
    type Args = serde_json::Value;

    async fn run(&self, _args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        let cfg = DmmConfig::from_env();

        if cfg.max_incremental == 0 && cfg.max_backfill == 0 {
            info!("dmm_hashlist: both incremental and backfill limits are 0, nothing to do");
            return Ok(());
        }

        let http = &ctx.state.http;
        let pool = &ctx.state.pool;
        let redis = &ctx.state.redis;

        // ── Incremental pass ────────────────────────────────────────────────

        let mut incr_commits = 0usize;
        let mut incr_files = 0usize;
        let mut incr_streams = 0usize;

        if cfg.max_incremental > 0 {
            let per_page = cfg.max_incremental.min(100);
            let commits_url = format!(
                "https://api.github.com/repos/{}/{}/commits?sha={}&per_page={}",
                cfg.owner, cfg.repo, cfg.branch, per_page
            );

            let commits = github_get_json(http, &commits_url, cfg.github_token.as_deref())
                .await
                .unwrap_or(serde_json::Value::Array(vec![]));

            let commit_list = match commits.as_array() {
                Some(arr) => arr.clone(),
                None => vec![],
            };

            if !commit_list.is_empty() {
                let latest_known_sha: Option<String> = redis
                    .get::<Option<String>, _>(LATEST_SHA_KEY)
                    .await
                    .unwrap_or(None);

                let head_sha = commit_list
                    .first()
                    .and_then(|c| c.get("sha"))
                    .and_then(|v| v.as_str())
                    .map(str::to_string);

                // Collect SHAs to process (stop at last known)
                let mut to_process: Vec<String> = Vec::new();
                for commit in &commit_list {
                    let sha = match commit.get("sha").and_then(|v| v.as_str()) {
                        Some(s) => s.to_string(),
                        None => continue,
                    };
                    if let Some(ref known) = latest_known_sha {
                        if sha == *known {
                            break;
                        }
                    }
                    to_process.push(sha);
                }

                // Process oldest-first
                for commit_sha in to_process.into_iter().rev() {
                    if ctx.is_cancelled() {
                        return Err(JobError::Cancelled);
                    }

                    match process_commit(
                        http,
                        pool,
                        redis,
                        &cfg,
                        &commit_sha,
                        ctx.state.config.tmdb_api_key.as_deref(),
                        ctx.state.config.imdb_cinemeta_fallback_enabled,
                        &ctx.state.config.anime_metadata_source_order,
                        &ctx.state.config.metadata_primary_source,
                    )
                    .await
                    {
                        Ok(stats) => {
                            incr_commits += 1;
                            incr_files += stats.files_processed;
                            incr_streams += stats.streams_created;
                        }
                        Err(e) => {
                            warn!("dmm_hashlist: commit {} failed: {e}", commit_sha);
                        }
                    }
                }

                // Update head SHA
                if let Some(ref sha) = head_sha {
                    let _ = redis
                        .set::<(), _, _>(LATEST_SHA_KEY, sha.as_str(), None, None, false)
                        .await;
                }
            }
        }

        // ── Backfill pass ────────────────────────────────────────────────────

        let mut bf_commits = 0usize;
        let mut bf_files = 0usize;
        let mut bf_streams = 0usize;

        if cfg.max_backfill > 0 {
            let backfill_sha: Option<String> = redis
                .get::<Option<String>, _>(BACKFILL_SHA_KEY)
                .await
                .unwrap_or(None);

            let next_sha = match backfill_sha.as_deref() {
                Some(BACKFILL_DONE) => {
                    debug!("dmm_hashlist: backfill already complete");
                    None
                }
                Some(sha) => Some(sha.to_string()),
                None => {
                    // Seed backfill from parent of the latest known SHA
                    let latest_sha: Option<String> = redis
                        .get::<Option<String>, _>(LATEST_SHA_KEY)
                        .await
                        .unwrap_or(None);

                    if let Some(ref sha) = latest_sha {
                        let commit_url = format!(
                            "https://api.github.com/repos/{}/{}/commits/{}",
                            cfg.owner, cfg.repo, sha
                        );
                        match github_get_json(http, &commit_url, cfg.github_token.as_deref()).await
                        {
                            Ok(data) => {
                                let parent = data
                                    .get("parents")
                                    .and_then(|v| v.as_array())
                                    .and_then(|arr| arr.first())
                                    .and_then(|p| p.get("sha"))
                                    .and_then(|v| v.as_str())
                                    .map(str::to_string);

                                if let Some(ref p) = parent {
                                    let _ = redis
                                        .set::<(), _, _>(
                                            BACKFILL_SHA_KEY,
                                            p.as_str(),
                                            None,
                                            None,
                                            false,
                                        )
                                        .await;
                                }
                                parent
                            }
                            Err(e) => {
                                warn!("dmm_hashlist: backfill seed fetch failed: {e}");
                                None
                            }
                        }
                    } else {
                        None
                    }
                }
            };

            let mut current_sha = next_sha;
            while let Some(ref sha) = current_sha.clone() {
                if ctx.is_cancelled() {
                    return Err(JobError::Cancelled);
                }
                if bf_commits >= cfg.max_backfill {
                    break;
                }

                match process_commit(
                    http,
                    pool,
                    redis,
                    &cfg,
                    sha,
                    ctx.state.config.tmdb_api_key.as_deref(),
                    ctx.state.config.imdb_cinemeta_fallback_enabled,
                    &ctx.state.config.anime_metadata_source_order,
                    &ctx.state.config.metadata_primary_source,
                )
                .await
                {
                    Ok(stats) => {
                        bf_commits += 1;
                        bf_files += stats.files_processed;
                        bf_streams += stats.streams_created;
                        current_sha = stats.next_parent_sha;
                    }
                    Err(e) => {
                        warn!("dmm_hashlist: backfill commit {} failed: {e}", sha);
                        break;
                    }
                }
            }

            let sentinel = current_sha.as_deref().unwrap_or(BACKFILL_DONE);
            let _ = redis
                .set::<(), _, _>(BACKFILL_SHA_KEY, sentinel, None, None, false)
                .await;
        }

        info!(
            "dmm_hashlist: incremental commits={} files={} streams={} | backfill commits={} files={} streams={}",
            incr_commits, incr_files, incr_streams,
            bf_commits, bf_files, bf_streams,
        );

        Ok(())
    }
}
