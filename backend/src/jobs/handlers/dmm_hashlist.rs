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
/// GitHub repo settings come from `AppConfig` (`DMM_HASHLIST_*`, optional
/// `DMM_HASHLIST_GITHUB_TOKEN` / `GITHUB_TOKEN` for authenticated API access).
use async_trait::async_trait;
use fred::prelude::{KeysInterface, SetsInterface};
use once_cell::sync::Lazy;
use regex::Regex;
use tracing::{debug, info, warn};

use crate::config::AppConfig;
use crate::db::{MediaId, TorrentType};
use crate::state::KeywordFilterCache;
use crate::{
    jobs::{
        error::JobError,
        handler::{JobCtx, JobHandler},
    },
    parser,
    scrapers::{media_resolve, stream_convert, ScrapedStream, SearchMeta, StreamFile},
};

// ─── Redis key constants (must match Python side) ─────────────────────────────

const LATEST_SHA_KEY: &str = "dmm_hashlist_scraper:latest_commit_sha";
const BACKFILL_SHA_KEY: &str = "dmm_hashlist_scraper:backfill_next_commit_sha";
const PROCESSED_FILES_KEY: &str = "dmm_hashlist_scraper:processed_file_shas";
const STATUS_KEY: &str = "dmm_hashlist:status";

const BACKFILL_DONE: &str = "__done__";
const FULL_INGEST_MAX_ITERATIONS: usize = 200;

// ─── Regex ────────────────────────────────────────────────────────────────────

/// Extracts the base64url-encoded LZString payload from the DMM iframe wrapper.
/// `<iframe src="https://debridmediamanager.com/hashlist#{payload}"></iframe>`
static IFRAME_FRAG_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"<iframe\s+src="https://debridmediamanager\.com/hashlist#([^"]+)"></iframe>"#)
        .expect("iframe fragment regex")
});

static INFO_HASH_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^[0-9a-fA-F]{40}$").expect("info hash regex"));

// ─── GitHub API helpers ───────────────────────────────────────────────────────

/// Parse the X-RateLimit-Reset header and sleep until that epoch second (+ 1s buffer).
async fn wait_for_rate_limit_reset(resp: &reqwest::Response) {
    let reset_epoch = resp
        .headers()
        .get("x-ratelimit-reset")
        .and_then(|v| v.to_str().ok())
        .and_then(|s| s.parse::<i64>().ok());

    let remaining = resp
        .headers()
        .get("x-ratelimit-remaining")
        .and_then(|v| v.to_str().ok())
        .and_then(|s| s.parse::<i64>().ok())
        .unwrap_or(-1);

    if let Some(reset) = reset_epoch {
        let now = chrono::Utc::now().timestamp();
        let wait_secs = (reset - now + 1).clamp(1, 120) as u64;
        warn!(
            "dmm_hashlist: GitHub rate limit hit (remaining={remaining}, reset in {wait_secs}s) — sleeping"
        );
        tokio::time::sleep(std::time::Duration::from_secs(wait_secs)).await;
    } else {
        warn!("dmm_hashlist: GitHub rate limit hit but no reset header, sleeping 60s");
        tokio::time::sleep(std::time::Duration::from_secs(60)).await;
    }
}

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

    let status = resp.status();
    if status == reqwest::StatusCode::FORBIDDEN || status == reqwest::StatusCode::TOO_MANY_REQUESTS
    {
        wait_for_rate_limit_reset(&resp).await;
        return Err(JobError::other(format!(
            "GitHub API rate limited on {url} (HTTP {status}) — will retry next run"
        )));
    }
    if !status.is_success() {
        return Err(JobError::other(format!(
            "GitHub API {url} returned HTTP {status}"
        )));
    }

    // Warn proactively when remaining budget is low so operators notice before hitting 0.
    if let Some(remaining) = resp
        .headers()
        .get("x-ratelimit-remaining")
        .and_then(|v| v.to_str().ok())
        .and_then(|s| s.parse::<i64>().ok())
    {
        if remaining < 10 {
            warn!("dmm_hashlist: GitHub rate limit almost exhausted (remaining={remaining}). Set DMM_HASHLIST_GITHUB_TOKEN to raise the limit.");
        }
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

    let status = resp.status();
    if status == reqwest::StatusCode::FORBIDDEN || status == reqwest::StatusCode::TOO_MANY_REQUESTS
    {
        wait_for_rate_limit_reset(&resp).await;
        return Err(JobError::other(format!(
            "GitHub raw fetch rate limited on {url} (HTTP {status}) — will retry next run"
        )));
    }
    if !status.is_success() {
        return Err(JobError::other(format!(
            "raw fetch {url} returned HTTP {status}"
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

// ─── Stream building ────────────────────────────────────────────────────────────

fn build_series_files(entry: &HashlistEntry, parsed: &parser::ParsedTitle) -> Vec<StreamFile> {
    let mut files = Vec::new();
    if parsed.seasons.is_empty() {
        return files;
    }

    if !parsed.episodes.is_empty() {
        for &episode in &parsed.episodes {
            files.push(StreamFile {
                file_index: 0,
                filename: entry.filename.clone(),
                season_number: parsed.seasons[0],
                episode_number: episode,
            });
        }
    } else {
        for &season in &parsed.seasons {
            files.push(StreamFile {
                file_index: 0,
                filename: entry.filename.clone(),
                season_number: season,
                episode_number: 1,
            });
        }
    }

    files
}

struct ResolvedStream {
    meta: SearchMeta,
    media_type: String,
    parsed: parser::ParsedTitle,
    files: Vec<StreamFile>,
    season: Option<i32>,
    episode: Option<i32>,
    catalog: Option<String>,
}

fn resolve_sports_stream(filename: &str, parsed: &parser::ParsedTitle) -> Option<ResolvedStream> {
    let category = parser::detect_sports_category(filename).unwrap_or("other_sports");

    if let Some(wwe) = parser::classify_wwe_title(filename) {
        let files = vec![StreamFile {
            file_index: 0,
            filename: parser::clean_sports_title(filename),
            season_number: wwe.season_number,
            episode_number: wwe.episode_number,
        }];
        return Some(ResolvedStream {
            meta: SearchMeta {
                media_id: MediaId(0),
                imdb_id: None,
                title: wwe.series_title.to_string(),
                year: None,
            },
            media_type: "series".to_string(),
            parsed: parsed.clone(),
            files,
            season: None,
            episode: None,
            catalog: Some(category.to_string()),
        });
    }

    if matches!(category, "formula_racing" | "motogp_racing") {
        if let Some(racing) = parser::parse_racing_title(filename) {
            let session_src = racing.session.as_deref().unwrap_or(filename);
            if let Some((episode, episode_title)) = parser::racing_session_episode(session_src) {
                let files = vec![StreamFile {
                    file_index: 0,
                    filename: episode_title,
                    season_number: 1,
                    episode_number: episode,
                }];
                return Some(ResolvedStream {
                    meta: SearchMeta {
                        media_id: MediaId(0),
                        imdb_id: None,
                        title: racing.series_title,
                        year: racing.year,
                    },
                    media_type: "series".to_string(),
                    parsed: parsed.clone(),
                    files,
                    season: None,
                    episode: None,
                    catalog: Some(category.to_string()),
                });
            }
        }
    }

    let clean_title = parsed
        .title
        .clone()
        .filter(|t| !t.is_empty())
        .unwrap_or_else(|| parser::clean_sports_title(filename));

    Some(ResolvedStream {
        meta: SearchMeta {
            media_id: MediaId(0),
            imdb_id: None,
            title: clean_title,
            year: parsed.year,
        },
        media_type: "movie".to_string(),
        parsed: parsed.clone(),
        files: vec![],
        season: None,
        episode: None,
        catalog: Some(category.to_string()),
    })
}

// ─── DB helpers ───────────────────────────────────────────────────────────────

/// Parse, resolve, validate, and persist a single hashlist entry.
///
/// Returns true if a new row was inserted, false if skipped or already present.
async fn store_torrent_stream(
    pool: &sqlx::PgPool,
    http: &reqwest::Client,
    entry: &HashlistEntry,
    tmdb_api_key: Option<&str>,
    tvdb_api_key: Option<&str>,
    cinemeta_fallback: bool,
    keyword_filters: &KeywordFilterCache,
) -> Result<bool, sqlx::Error> {
    let existing: Option<(i32,)> =
        sqlx::query_as("SELECT stream_id FROM torrent_stream WHERE info_hash = $1")
            .bind(&entry.info_hash)
            .fetch_optional(pool)
            .await?;

    if existing.is_some() {
        return Ok(false);
    }

    if keyword_filters.matches_blocked_keyword(&entry.filename) {
        return Ok(false);
    }

    let is_sports = parser::is_sports_title(&entry.filename);
    let parsed = if is_sports {
        parser::parse_sports_title(&entry.filename)
    } else {
        parser::parse_title(&entry.filename)
    };

    let resolved = if is_sports {
        let Some(mut sports) = resolve_sports_stream(&entry.filename, &parsed) else {
            return Ok(false);
        };

        let category = sports.catalog.as_deref().unwrap_or("other_sports");
        let stub_type = if sports.media_type == "series" {
            "SERIES"
        } else {
            "MOVIE"
        };

        let media_id = media_resolve::find_or_create_sports_stub(
            pool,
            &sports.meta.title,
            sports.meta.year,
            None,
            stub_type,
        )
        .await
        .unwrap_or(0);

        if media_id <= 0 {
            return Ok(false);
        }

        media_resolve::link_to_catalogs(pool, media_id, &[category]).await;
        sports.meta.media_id = MediaId(media_id);
        sports
    } else {
        let is_series = !parsed.seasons.is_empty() || !parsed.episodes.is_empty();
        let media_type = if is_series { "series" } else { "movie" };
        let title = parsed
            .title
            .as_deref()
            .filter(|t| !t.is_empty())
            .unwrap_or(entry.filename.as_str());

        let Some(meta) = media_resolve::search_meta_for_dmm_hashlist(
            pool,
            http,
            title,
            parsed.year,
            is_series,
            tmdb_api_key,
            tvdb_api_key,
            cinemeta_fallback,
            Some(entry.filename.as_str()),
            parsed.episodes.first().copied(),
        )
        .await
        else {
            return Ok(false);
        };

        let files = if is_series {
            build_series_files(entry, &parsed)
        } else {
            vec![]
        };
        let season = parsed.seasons.first().copied();
        let episode = parsed.episodes.first().copied();

        ResolvedStream {
            meta,
            media_type: media_type.to_string(),
            parsed,
            files,
            season,
            episode,
            catalog: None,
        }
    };

    let stream = ScrapedStream {
        info_hash: entry.info_hash.clone(),
        name: entry.filename.clone(),
        source: "dmm_hashlist".to_string(),
        seeders: Some(0),
        size: Some(entry.size),
        parsed: resolved.parsed,
        files: resolved.files,
        is_cached: false,
        torrent_type: TorrentType::Public,
        torrent_file: None,
        announce_list: vec![],
    };

    stream_convert::write_back_torrents(
        pool,
        &[stream],
        &resolved.meta,
        &resolved.media_type,
        resolved.season,
        resolved.episode,
    )
    .await;

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
    app_cfg: &AppConfig,
    commit_sha: &str,
    tmdb_api_key: Option<&str>,
    tvdb_api_key: Option<&str>,
    cinemeta_fallback: bool,
    github_token: Option<&str>,
    keyword_filters: &KeywordFilterCache,
) -> Result<CommitStats, JobError> {
    let commit_url = format!(
        "https://api.github.com/repos/{}/{}/commits/{}",
        app_cfg.dmm_hashlist_repo_owner, app_cfg.dmm_hashlist_repo_name, commit_sha
    );
    let commit_data = github_get_json(http, &commit_url, github_token).await?;

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
                    app_cfg.dmm_hashlist_repo_owner,
                    app_cfg.dmm_hashlist_repo_name,
                    app_cfg.dmm_hashlist_branch,
                    file_path
                )
            });

        let html = match github_get_text(http, &raw_url, github_token).await {
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
                tvdb_api_key,
                cinemeta_fallback,
                keyword_filters,
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

// ─── Run stats + status ───────────────────────────────────────────────────────

#[derive(Default, Clone)]
struct RunStats {
    incr_commits: usize,
    incr_files: usize,
    incr_streams: usize,
    bf_commits: usize,
    bf_files: usize,
    bf_streams: usize,
}

impl RunStats {
    fn made_progress(&self) -> bool {
        self.incr_commits
            + self.incr_files
            + self.incr_streams
            + self.bf_commits
            + self.bf_files
            + self.bf_streams
            > 0
    }

    fn absorb(&mut self, other: &RunStats) {
        self.incr_commits += other.incr_commits;
        self.incr_files += other.incr_files;
        self.incr_streams += other.incr_streams;
        self.bf_commits += other.bf_commits;
        self.bf_files += other.bf_files;
        self.bf_streams += other.bf_streams;
    }
}

async fn write_dmm_status(
    redis: &fred::clients::Client,
    app_cfg: &AppConfig,
) -> Result<(), JobError> {
    let latest_commit_sha: Option<String> = redis.get(LATEST_SHA_KEY).await.unwrap_or(None);
    let backfill_next_raw: Option<String> = redis.get(BACKFILL_SHA_KEY).await.unwrap_or(None);
    let backfill_complete = backfill_next_raw.as_deref() == Some(BACKFILL_DONE);
    let backfill_next_commit_sha = if backfill_complete {
        None
    } else {
        backfill_next_raw
    };
    let processed_file_sha_count: i64 = redis.scard(PROCESSED_FILES_KEY).await.unwrap_or(0);

    let status = serde_json::json!({
        "enabled": app_cfg.is_scrap_from_dmm_hashlist,
        "scheduler_disabled": app_cfg.disable_dmm_hashlist_scraper,
        "cron_expression": "0 * * * *",
        "repo": format!(
            "{}/{}",
            app_cfg.dmm_hashlist_repo_owner, app_cfg.dmm_hashlist_repo_name
        ),
        "branch": app_cfg.dmm_hashlist_branch,
        "sync_interval_hours": app_cfg.dmm_hashlist_sync_ttl / 3600,
        "commits_per_run": app_cfg.dmm_hashlist_commits_per_run,
        "backfill_commits_per_run": app_cfg.dmm_hashlist_backfill_commits_per_run,
        "latest_commit_sha": latest_commit_sha,
        "backfill_next_commit_sha": backfill_next_commit_sha,
        "backfill_complete": backfill_complete,
        "processed_file_sha_count": processed_file_sha_count,
    });

    let payload =
        serde_json::to_string(&status).map_err(|e| JobError::other(format!("status JSON: {e}")))?;
    let _ = redis
        .set::<(), _, _>(STATUS_KEY, payload.as_str(), None, None, false)
        .await;
    Ok(())
}

async fn reset_checkpoints(redis: &fred::clients::Client) {
    let _ = redis.del::<(), _>(LATEST_SHA_KEY).await;
    let _ = redis.del::<(), _>(BACKFILL_SHA_KEY).await;
    let _ = redis.del::<(), _>(PROCESSED_FILES_KEY).await;
}

async fn run_ingestion(
    http: &reqwest::Client,
    pool: &sqlx::PgPool,
    redis: &fred::clients::Client,
    app_cfg: &AppConfig,
    ctx: &JobCtx,
    tmdb_api_key: Option<&str>,
    tvdb_api_key: Option<&str>,
    cinemeta_fallback: bool,
    keyword_filters: &KeywordFilterCache,
) -> Result<RunStats, JobError> {
    let github_token = app_cfg.dmm_hashlist_github_token.as_deref();
    let mut stats = RunStats::default();

    if app_cfg.dmm_hashlist_commits_per_run == 0
        && app_cfg.dmm_hashlist_backfill_commits_per_run == 0
    {
        info!("dmm_hashlist: both incremental and backfill limits are 0, nothing to do");
        return Ok(stats);
    }

    // ── Incremental pass ────────────────────────────────────────────────

    if app_cfg.dmm_hashlist_commits_per_run > 0 {
        let per_page = app_cfg.dmm_hashlist_commits_per_run.min(100);
        let commits_url = format!(
            "https://api.github.com/repos/{}/{}/commits?sha={}&per_page={}",
            app_cfg.dmm_hashlist_repo_owner,
            app_cfg.dmm_hashlist_repo_name,
            app_cfg.dmm_hashlist_branch,
            per_page
        );

        let commits = github_get_json(http, &commits_url, github_token)
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

            for commit_sha in to_process.into_iter().rev() {
                if ctx.is_cancelled() {
                    return Err(JobError::Cancelled);
                }

                match process_commit(
                    http,
                    pool,
                    redis,
                    app_cfg,
                    &commit_sha,
                    tmdb_api_key,
                    tvdb_api_key,
                    cinemeta_fallback,
                    github_token,
                    keyword_filters,
                )
                .await
                {
                    Ok(commit_stats) => {
                        stats.incr_commits += 1;
                        stats.incr_files += commit_stats.files_processed;
                        stats.incr_streams += commit_stats.streams_created;
                    }
                    Err(e) => {
                        warn!("dmm_hashlist: commit {} failed: {e}", commit_sha);
                    }
                }
            }

            if let Some(ref sha) = head_sha {
                let _ = redis
                    .set::<(), _, _>(LATEST_SHA_KEY, sha.as_str(), None, None, false)
                    .await;
            }
        }
    }

    // ── Backfill pass ────────────────────────────────────────────────────

    if app_cfg.dmm_hashlist_backfill_commits_per_run > 0 {
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
                let latest_sha: Option<String> = redis
                    .get::<Option<String>, _>(LATEST_SHA_KEY)
                    .await
                    .unwrap_or(None);

                if let Some(ref sha) = latest_sha {
                    let commit_url = format!(
                        "https://api.github.com/repos/{}/{}/commits/{}",
                        app_cfg.dmm_hashlist_repo_owner, app_cfg.dmm_hashlist_repo_name, sha
                    );
                    match github_get_json(http, &commit_url, github_token).await {
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

        // Only persist the backfill position when we actually had a starting SHA.
        // If next_sha is None here it means latest_commit_sha hasn't been established
        // yet (first ever run), so we must NOT write __done__ — otherwise backfill
        // would be considered complete before a single commit was processed.
        let had_backfill_start = next_sha.is_some();

        let mut current_sha = next_sha;
        while let Some(ref sha) = current_sha.clone() {
            if ctx.is_cancelled() {
                return Err(JobError::Cancelled);
            }
            if stats.bf_commits >= app_cfg.dmm_hashlist_backfill_commits_per_run {
                break;
            }

            match process_commit(
                http,
                pool,
                redis,
                app_cfg,
                sha,
                tmdb_api_key,
                tvdb_api_key,
                cinemeta_fallback,
                github_token,
                keyword_filters,
            )
            .await
            {
                Ok(commit_stats) => {
                    stats.bf_commits += 1;
                    stats.bf_files += commit_stats.files_processed;
                    stats.bf_streams += commit_stats.streams_created;
                    current_sha = commit_stats.next_parent_sha;
                }
                Err(e) => {
                    warn!("dmm_hashlist: backfill commit {} failed: {e}", sha);
                    break;
                }
            }
        }

        if had_backfill_start {
            let sentinel = current_sha.as_deref().unwrap_or(BACKFILL_DONE);
            let _ = redis
                .set::<(), _, _>(BACKFILL_SHA_KEY, sentinel, None, None, false)
                .await;
        }
    }

    // Refresh the TTL on the processed-file-shas set so it never grows
    // unbounded: if the set goes untouched for 90 days it will be evicted.
    const PROCESSED_FILES_TTL_SECS: i64 = 90 * 24 * 3600; // 90 days
    let _ = redis
        .expire::<i64, _>(PROCESSED_FILES_KEY, PROCESSED_FILES_TTL_SECS, None)
        .await;

    info!(
        "dmm_hashlist: incremental commits={} files={} streams={} | backfill commits={} files={} streams={}",
        stats.incr_commits,
        stats.incr_files,
        stats.incr_streams,
        stats.bf_commits,
        stats.bf_files,
        stats.bf_streams,
    );

    Ok(stats)
}

// ─── Handler ──────────────────────────────────────────────────────────────────

pub struct DmmHashlistScraper;

#[async_trait]
impl JobHandler for DmmHashlistScraper {
    const QUEUE: &'static str = "dmm_hashlist";
    const CONCURRENCY: usize = 1;
    type Args = serde_json::Value;

    async fn run(&self, args: Self::Args, ctx: JobCtx) -> Result<(), JobError> {
        let full = args.get("full").and_then(|v| v.as_bool()).unwrap_or(false);
        let reset = args
            .get("reset_checkpoints")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);

        let app_cfg = &ctx.state.config;
        let http = &ctx.state.http;
        let pool = &ctx.state.pool;
        let redis = &ctx.state.redis;

        if reset {
            reset_checkpoints(redis).await;
        }

        let tmdb_api_key = app_cfg.tmdb_api_key.as_deref();
        let tvdb_api_key = app_cfg.tvdb_api_key.as_deref();
        let cinemeta_fallback = app_cfg.imdb_cinemeta_fallback_enabled;
        let kf = ctx
            .state
            .keyword_filters
            .read()
            .map(|g| g.clone())
            .unwrap_or_default();

        let mut totals = RunStats::default();

        if full {
            for _ in 0..FULL_INGEST_MAX_ITERATIONS {
                if ctx.is_cancelled() {
                    return Err(JobError::Cancelled);
                }

                let iteration_stats = run_ingestion(
                    http,
                    pool,
                    redis,
                    app_cfg,
                    &ctx,
                    tmdb_api_key,
                    tvdb_api_key,
                    cinemeta_fallback,
                    &kf,
                )
                .await?;
                totals.absorb(&iteration_stats);
                write_dmm_status(redis, app_cfg).await?;

                let backfill_sha: Option<String> =
                    redis.get(BACKFILL_SHA_KEY).await.unwrap_or(None);
                if backfill_sha.as_deref() == Some(BACKFILL_DONE) {
                    break;
                }
                if !iteration_stats.made_progress() {
                    break;
                }
            }
        } else {
            let iteration_stats = run_ingestion(
                http,
                pool,
                redis,
                app_cfg,
                &ctx,
                tmdb_api_key,
                tvdb_api_key,
                cinemeta_fallback,
                &kf,
            )
            .await?;
            totals.absorb(&iteration_stats);
            write_dmm_status(redis, app_cfg).await?;
        }

        info!(
            "dmm_hashlist: run complete — incremental commits={} files={} streams={} | backfill commits={} files={} streams={}",
            totals.incr_commits,
            totals.incr_files,
            totals.incr_streams,
            totals.bf_commits,
            totals.bf_files,
            totals.bf_streams,
        );

        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn is_valid_metadata_match_accepts_confident_series_year() {
        assert!(media_resolve::is_valid_dmm_metadata_match(
            "Breaking Bad",
            Some(2010),
            "series",
            "Breaking Bad",
            Some(2008),
            Some(2013),
            None,
        ));
    }

    #[test]
    fn is_valid_metadata_match_rejects_movie_year_mismatch() {
        assert!(!media_resolve::is_valid_dmm_metadata_match(
            "Inception",
            Some(2012),
            "movie",
            "Inception",
            Some(2010),
            None,
            None,
        ));
    }

    #[test]
    fn is_valid_metadata_match_rejects_low_similarity() {
        assert!(!media_resolve::is_valid_dmm_metadata_match(
            "Totally Different Film",
            Some(1999),
            "movie",
            "The Matrix",
            Some(1999),
            None,
            None,
        ));
    }

    #[test]
    fn is_valid_metadata_match_rejects_implausible_episode_for_young_series() {
        assert!(!media_resolve::is_valid_dmm_metadata_match(
            "Running Man",
            Some(2025),
            "series",
            "Running Man",
            Some(2017),
            None,
            Some(751),
        ));
    }

    #[test]
    fn extract_bracket_air_year_parses_yymmdd() {
        assert_eq!(
            media_resolve::extract_bracket_air_year("Running Man - 751 [250504].mp4"),
            Some(2025)
        );
    }

    #[test]
    fn build_series_files_expands_episodes() {
        let entry = HashlistEntry {
            filename: "Show.S01E02.mkv".to_string(),
            info_hash: "a".repeat(40),
            size: 1,
        };
        let parsed = parser::parse_title(&entry.filename);
        let files = build_series_files(&entry, &parsed);
        assert_eq!(files.len(), 1);
        assert_eq!(files[0].season_number, 1);
        assert_eq!(files[0].episode_number, 2);
    }

    #[test]
    fn resolve_sports_stream_wwe_weekly_is_series() {
        let title = "WWE Monday Night Raw 2026 05 23 1080p";
        let parsed = parser::parse_sports_title(title);
        let resolved = resolve_sports_stream(title, &parsed).unwrap();
        assert_eq!(resolved.media_type, "series");
        assert_eq!(resolved.files.len(), 1);
        assert_eq!(resolved.files[0].season_number, 2026);
        assert_eq!(resolved.files[0].episode_number, 523);
    }

    #[test]
    fn resolve_sports_stream_wwe_ppv_is_movie() {
        let title = "WWE WrestleMania 40 2024 1080p";
        let parsed = parser::parse_sports_title(title);
        let resolved = resolve_sports_stream(title, &parsed).unwrap();
        assert_eq!(resolved.media_type, "movie");
        assert!(resolved.files.is_empty());
    }

    #[test]
    fn resolve_sports_stream_racing_session_is_series() {
        let title = "Formula 1 Canadian Grand Prix Qualifying 23.05.2026 1080p";
        let parsed = parser::parse_sports_title(title);
        let resolved = resolve_sports_stream(title, &parsed).unwrap();
        assert_eq!(resolved.media_type, "series");
        assert_eq!(resolved.files.len(), 1);
        assert_eq!(resolved.files[0].episode_number, 4);
    }

    #[test]
    fn adult_keywords_filter_matches() {
        let kf = KeywordFilterCache {
            keywords: vec!["brazzers".to_string()],
            whitelist: vec![],
        };
        assert!(kf.matches_blocked_keyword("Some.Adult.Title.Brazzers.1080p"));
    }
}
