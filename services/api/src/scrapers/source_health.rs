/// Public indexer source health tracking.
///
/// Mirrors workers/scrapers/source_health.py — records per-source success/timeout
/// outcomes in Redis and gates scraping when a source's success rate falls below
/// configured thresholds.
use std::collections::HashMap;

use fred::prelude::{HashesInterface, KeysInterface};

const METRICS_KEY_PREFIX: &str = "public_indexer_source_health:";
const DEFAULT_HEALTH_BUCKET: &str = "general";
const SCRAPER_METRICS_LATEST_KEY: &str = "scraper_metrics_latest:";
const SCRAPER_METRICS_HISTORY_KEY: &str = "scraper_metrics_history:";
const SCRAPER_METRICS_HISTORY_MAX: i64 = 100;
const SCRAPER_METRICS_TTL: i64 = 86400 * 7;

// ─── Config ───────────────────────────────────────────────────────────────────

/// Carries all health-gate config needed by the public-indexer scraper.
/// Cheap to clone (all primitives + one `fred::clients::Client` arc clone).
#[derive(Clone)]
pub struct HealthGateConfig {
    pub redis: fred::clients::Client,
    pub enabled: bool,
    pub min_samples: i64,
    pub min_success_rate: f64,
    pub max_timeout_rate: f64,
    pub counter_soft_cap: i64,
    pub decay_factor: f64,
    pub recovery_success_streak: i64,
    pub scope_mode: String,
    pub scope_override: String,
    pub metrics_ttl_seconds: i64,
}

// ─── Snapshot ────────────────────────────────────────────────────────────────

pub struct SourceHealthSnapshot {
    pub source_key: String,
    pub total: i64,
    pub success: i64,
    pub timeout: i64,
    pub challenge_solved: i64,
    pub consecutive_success: i64,
}

impl SourceHealthSnapshot {
    pub fn success_rate(&self) -> f64 {
        if self.total <= 0 {
            0.0
        } else {
            self.success as f64 / self.total as f64
        }
    }

    pub fn timeout_rate(&self) -> f64 {
        if self.total <= 0 {
            0.0
        } else {
            self.timeout as f64 / self.total as f64
        }
    }

    pub fn challenge_solve_rate(&self) -> f64 {
        if self.total <= 0 {
            0.0
        } else {
            self.challenge_solved as f64 / self.total as f64
        }
    }

    pub fn gate_status(
        &self,
        min_samples: i64,
        min_success_rate: f64,
        max_timeout_rate: f64,
    ) -> &'static str {
        if self.total < min_samples.max(1) {
            return "warming";
        }
        if self.success_rate() >= min_success_rate && self.timeout_rate() <= max_timeout_rate {
            "allowed"
        } else {
            "blocked"
        }
    }
}

// ─── Key helpers ─────────────────────────────────────────────────────────────

fn sanitize_scope_component(raw: &str) -> String {
    let lower = raw.trim().to_lowercase();
    let sanitized: String = lower
        .chars()
        .map(|c| {
            if c.is_alphanumeric() || c == '_' || c == '-' {
                c
            } else {
                '-'
            }
        })
        .collect();
    sanitized.trim_matches('-').to_string()
}

fn resolve_scope_key(scope_mode: &str, scope_override: &str) -> Option<String> {
    match scope_mode.trim().to_lowercase().as_str() {
        "global" | "" => None,
        "pod" => {
            let raw = std::env::var("PUBLIC_INDEXERS_SOURCE_HEALTH_SCOPE")
                .or_else(|_| std::env::var("POD_NAME"))
                .or_else(|_| std::env::var("HOSTNAME"))
                .unwrap_or_default();
            let s = sanitize_scope_component(&raw);
            Some(if s.is_empty() { "default".into() } else { s })
        }
        "custom" => {
            let s = sanitize_scope_component(scope_override);
            Some(if s.is_empty() { "default".into() } else { s })
        }
        _ => None,
    }
}

pub fn metrics_key(
    source_key: &str,
    health_bucket: &str,
    scope_mode: &str,
    scope_override: &str,
) -> String {
    let normalized_key = source_key.trim().to_lowercase();
    let bucket = {
        let b = sanitize_scope_component(health_bucket);
        if b.is_empty() {
            DEFAULT_HEALTH_BUCKET.to_string()
        } else {
            b
        }
    };
    match resolve_scope_key(scope_mode, scope_override) {
        None => format!("{METRICS_KEY_PREFIX}{bucket}:{normalized_key}"),
        Some(scope) => format!("{METRICS_KEY_PREFIX}{scope}:{bucket}:{normalized_key}"),
    }
}

// ─── Read / write ─────────────────────────────────────────────────────────────

pub async fn get_source_health(
    redis: &fred::clients::Client,
    source_key: &str,
    health_bucket: &str,
    scope_mode: &str,
    scope_override: &str,
) -> SourceHealthSnapshot {
    let key = metrics_key(source_key, health_bucket, scope_mode, scope_override);
    let raw: HashMap<String, String> = redis.hgetall(&key).await.unwrap_or_default();
    let get = |field: &str| -> i64 {
        raw.get(field)
            .and_then(|v| v.parse().ok())
            .unwrap_or(0)
    };
    if raw.is_empty() {
        return SourceHealthSnapshot {
            source_key: source_key.to_string(),
            total: 0,
            success: 0,
            timeout: 0,
            challenge_solved: 0,
            consecutive_success: 0,
        };
    }
    SourceHealthSnapshot {
        source_key: source_key.to_string(),
        total: get("total"),
        success: get("success"),
        timeout: get("timeout"),
        challenge_solved: get("challenge_solved"),
        consecutive_success: get("consecutive_success"),
    }
}

pub async fn record_source_outcome(
    redis: &fred::clients::Client,
    source_key: &str,
    success: bool,
    timed_out: bool,
    challenge_solved: bool,
    health_bucket: &str,
    scope_mode: &str,
    scope_override: &str,
    soft_cap: i64,
    decay_factor: f64,
    ttl_seconds: i64,
) {
    let key = metrics_key(source_key, health_bucket, scope_mode, scope_override);
    let total: i64 = redis
        .hincrby::<i64, _, _>(&key, "total", 1)
        .await
        .unwrap_or(0);
    if success {
        let _: Result<i64, _> = redis.hincrby(&key, "success", 1).await;
    }
    if timed_out {
        let _: Result<i64, _> = redis.hincrby(&key, "timeout", 1).await;
    }
    if challenge_solved {
        let _: Result<i64, _> = redis.hincrby(&key, "challenge_solved", 1).await;
    }
    if success && !timed_out {
        let _: Result<i64, _> = redis.hincrby(&key, "consecutive_success", 1).await;
    } else {
        let mut fields = HashMap::new();
        fields.insert("consecutive_success".to_string(), "0".to_string());
        let _: Result<i64, _> = redis.hset(&key, fields).await;
    }

    if total >= soft_cap {
        decay_source_counters(redis, &key, decay_factor).await;
    }

    let _: Result<bool, _> = redis.expire(&key, ttl_seconds, None).await;
}

async fn decay_source_counters(
    redis: &fred::clients::Client,
    key: &str,
    decay_factor: f64,
) {
    let raw: HashMap<String, String> = match redis.hgetall(key).await {
        Ok(m) => m,
        Err(_) => return,
    };
    if raw.is_empty() {
        return;
    }
    let get = |field: &str| -> i64 {
        raw.get(field)
            .and_then(|v| v.parse().ok())
            .unwrap_or(0)
    };
    let current_total = get("total");
    if current_total <= 0 {
        return;
    }
    let decayed_total = ((current_total as f64 * decay_factor).max(1.0)) as i64;
    let decayed_success =
        ((get("success") as f64 * decay_factor) as i64).min(decayed_total).max(0);
    let decayed_timeout =
        ((get("timeout") as f64 * decay_factor) as i64).min(decayed_total).max(0);
    let decayed_challenge =
        ((get("challenge_solved") as f64 * decay_factor) as i64).min(decayed_total).max(0);
    let decayed_streak = get("consecutive_success").min(decayed_total).max(0);

    let mut fields = HashMap::new();
    fields.insert("total".to_string(), decayed_total.to_string());
    fields.insert("success".to_string(), decayed_success.to_string());
    fields.insert("timeout".to_string(), decayed_timeout.to_string());
    fields.insert("challenge_solved".to_string(), decayed_challenge.to_string());
    fields.insert("consecutive_success".to_string(), decayed_streak.to_string());
    let _: Result<i64, _> = redis.hset(key, fields).await;
}

pub async fn is_source_within_budget(
    redis: &fred::clients::Client,
    source_key: &str,
    min_samples: i64,
    min_success_rate: f64,
    max_timeout_rate: f64,
    health_bucket: &str,
    scope_mode: &str,
    scope_override: &str,
) -> bool {
    let snapshot = get_source_health(redis, source_key, health_bucket, scope_mode, scope_override).await;
    if snapshot.total < min_samples.max(1) {
        return true; // not enough samples yet → allow
    }
    snapshot.success_rate() >= min_success_rate && snapshot.timeout_rate() <= max_timeout_rate
}

// ─── Scraper run metrics ──────────────────────────────────────────────────────

/// Save a minimal scraper-run metrics record to Redis so the admin dashboard
/// "Recent Media Search Runs" page shows Rust-server scrape activity.
///
/// Matches the JSON schema that Python's `ScraperMetrics.save_to_redis()` writes.
pub async fn save_scraper_run_metrics(
    redis: &fred::clients::Client,
    scraper_name: &str,
    imdb_id: Option<&str>,
    title: &str,
    season: Option<i32>,
    episode: Option<i32>,
    found: usize,
    processed: usize,
    skipped: usize,
    errors: usize,
    skip_reasons: &HashMap<String, usize>,
    start_ts: &chrono::DateTime<chrono::Utc>,
    end_ts: &chrono::DateTime<chrono::Utc>,
) {
    use fred::prelude::ListInterface;

    let duration_secs = (*end_ts - *start_ts).num_milliseconds() as f64 / 1000.0;

    let mut skip_reasons_json = serde_json::Map::new();
    for (k, v) in skip_reasons {
        skip_reasons_json.insert(k.clone(), serde_json::json!(v));
    }

    // Build the formatted summary matching Python's format_summary()
    let skips_str: String = if skip_reasons.is_empty() {
        "N/A".to_string()
    } else {
        skip_reasons
            .iter()
            .map(|(k, v)| format!("{k}:{v}"))
            .collect::<Vec<_>>()
            .join(", ")
    };

    let formatted_summary = format!(
        "\n{sep}\n{title_line}\n{sep}\n\nMeta ID: {meta_id}\nTitle: {title}\n\nDuration: {dur:.2} seconds\n\nItems:\n  ├─ Found     : {found}\n  ├─ Processed : {processed}\n  ├─ Skipped   : {skipped}\n  └─ Errors    : {errors}\n\nSkip Reasons: {skips}\n{sep}\n",
        sep = "=".repeat(80),
        title_line = format!("{} Scraping Metrics Summary", scraper_name.to_uppercase()).as_str().chars().collect::<String>(),
        meta_id = imdb_id.unwrap_or("unknown"),
        title = title,
        dur = duration_secs,
        found = found,
        processed = processed,
        skipped = skipped,
        errors = errors,
        skips = skips_str,
    );

    let summary = serde_json::json!({
        "scraper_name": scraper_name,
        "timestamp": start_ts.format("%Y-%m-%dT%H:%M:%S%.6f").to_string(),
        "end_timestamp": end_ts.format("%Y-%m-%dT%H:%M:%S%.6f").to_string(),
        "duration_seconds": duration_secs,
        "meta_id": imdb_id,
        "meta_title": title,
        "season": season,
        "episode": episode,
        "skip_scraping": false,
        "total_items": {
            "found": found,
            "processed": processed,
            "skipped": skipped,
            "errors": errors
        },
        "error_counts": {},
        "skip_reasons": skip_reasons_json,
        "quality_distribution": {},
        "source_distribution": {},
        "indexer_stats": {},
        "formatted_summary": formatted_summary,
    });

    let Ok(json_str) = serde_json::to_string(&summary) else {
        return;
    };

    // Save latest
    let latest_key = format!("{SCRAPER_METRICS_LATEST_KEY}{scraper_name}");
    let _: Result<(), _> = redis
        .set(&latest_key, json_str.clone(), Some(fred::types::Expiration::EX(SCRAPER_METRICS_TTL)), None, false)
        .await;

    // Prepend to history list, cap at SCRAPER_METRICS_HISTORY_MAX
    let history_key = format!("{SCRAPER_METRICS_HISTORY_KEY}{scraper_name}");
    let _: Result<i64, _> = redis.lpush(&history_key, json_str).await;
    let _: Result<(), _> = redis
        .ltrim(&history_key, 0, SCRAPER_METRICS_HISTORY_MAX - 1)
        .await;
    let _: Result<bool, _> = redis.expire(&history_key, SCRAPER_METRICS_TTL, None).await;
}
