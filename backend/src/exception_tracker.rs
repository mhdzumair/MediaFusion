//! Exception tracking: captures ERROR-level tracing events, fingerprints them,
//! and stores them in Redis for admin review.
//!
//! Key scheme (matches the Python exception_tracker exactly):
//!   exc:{fingerprint}  — Redis hash: type, message, source, traceback, count, first_seen, last_seen
//!   exc:index          — Redis sorted set of fingerprints scored by last_seen Unix timestamp
//!
//! A background tokio task drains the mpsc channel so on_event stays non-blocking.

use std::collections::HashMap;

use fred::prelude::{HashesInterface, KeysInterface, SortedSetsInterface};
use sha2::{Digest, Sha256};
use tokio::sync::mpsc;
use tracing::{Event, Subscriber};
use tracing_subscriber::{layer::Context, Layer};

pub const INDEX_KEY: &str = "exc:index";
pub const KEY_PREFIX: &str = "exc:";

// ─── Event payload ────────────────────────────────────────────────────────────

pub struct ExcEvent {
    pub message: String,
    pub level: &'static str,
    pub file: Option<&'static str>,
    pub line: Option<u32>,
    pub module: Option<&'static str>,
    pub timestamp: f64,
}

// ─── Tracing layer ────────────────────────────────────────────────────────────

pub struct ExceptionTrackerLayer {
    pub tx: mpsc::UnboundedSender<ExcEvent>,
}

impl<S: Subscriber> Layer<S> for ExceptionTrackerLayer {
    fn on_event(&self, event: &Event<'_>, _ctx: Context<'_, S>) {
        if *event.metadata().level() > tracing::Level::WARN {
            return;
        }
        // Only capture errors from application code, not framework crates.
        // tower-http fires ERROR "response failed" for every 5xx; sqlx/reqwest emit their own
        // WARN/ERROR events that are not actionable at the application level.
        let module = event.metadata().module_path().unwrap_or("");
        if !module.starts_with("mediafusion_api") {
            return;
        }
        let mut visitor = MessageVisitor::default();
        event.record(&mut visitor);
        if visitor.message.is_empty() {
            return;
        }
        let ts = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs_f64();
        let level = match *event.metadata().level() {
            tracing::Level::ERROR => "Error",
            _ => "Warning",
        };
        let _ = self.tx.send(ExcEvent {
            message: visitor.message,
            level,
            file: event.metadata().file(),
            line: event.metadata().line(),
            module: event.metadata().module_path(),
            timestamp: ts,
        });
    }
}

// ─── Field visitor ────────────────────────────────────────────────────────────

#[derive(Default)]
struct MessageVisitor {
    message: String,
}

impl tracing::field::Visit for MessageVisitor {
    fn record_str(&mut self, field: &tracing::field::Field, value: &str) {
        if field.name() == "message" {
            self.message = value.to_string();
        } else if self.message.is_empty() {
            self.message = format!("{}: {}", field.name(), value);
        }
    }

    fn record_debug(&mut self, field: &tracing::field::Field, value: &dyn std::fmt::Debug) {
        if field.name() == "message" {
            // fmt::Arguments implements Debug without extra quotes
            self.message = format!("{value:?}");
        } else if self.message.is_empty() {
            self.message = format!("{}: {value:?}", field.name());
        }
    }

    fn record_error(
        &mut self,
        field: &tracing::field::Field,
        value: &(dyn std::error::Error + 'static),
    ) {
        if field.name() == "message" || self.message.is_empty() {
            self.message = value.to_string();
        }
    }
}

// ─── Fingerprint ──────────────────────────────────────────────────────────────

/// Normalize a message so that variable parts (numbers, hex hashes) collapse to
/// a placeholder, allowing similar errors from the same site to share a bucket.
///
/// Examples:
///   "realdebrid torrents page 3: HTTP 401"  →  "realdebrid torrents page N: HTTP NNN"
///   "meta query [tt12345]: decode error"     →  "meta query [ttNNNNN]: decode error"
fn normalize_message(msg: &str) -> String {
    let mut out = String::with_capacity(msg.len().min(80));
    let mut chars = msg.chars().peekable();
    while out.len() < 80 {
        match chars.next() {
            None => break,
            Some(c) if c.is_ascii_digit() => {
                out.push('N');
                // consume the rest of the digit run
                while chars.peek().map(|c| c.is_ascii_digit()).unwrap_or(false) {
                    chars.next();
                }
            }
            Some(c) => out.push(c),
        }
    }
    out
}

fn fingerprint(file: Option<&str>, line: Option<u32>, msg: &str) -> String {
    // Include a normalized message prefix so errors at the same source location
    // but with different messages (different error classes) get distinct fingerprints.
    let normalized = normalize_message(msg);
    let raw = match (file, line) {
        (Some(f), Some(l)) => format!("{f}:{l}:{normalized}"),
        _ => normalized,
    };
    let hash = Sha256::digest(raw.as_bytes());
    hash.iter().take(8).map(|b| format!("{b:02x}")).collect()
}

// ─── Background worker ────────────────────────────────────────────────────────

pub async fn run_worker(
    redis: fred::clients::Client,
    mut rx: mpsc::UnboundedReceiver<ExcEvent>,
    ttl: i64,
    max_entries: i64,
) {
    while let Some(ev) = rx.recv().await {
        store_event(&redis, ev, ttl, max_entries).await;
    }
}

async fn store_event(redis: &fred::clients::Client, ev: ExcEvent, ttl: i64, max_entries: i64) {
    let fp = fingerprint(ev.file, ev.line, &ev.message);
    let key = format!("{KEY_PREFIX}{fp}");
    let source = match (ev.file, ev.line) {
        (Some(f), Some(l)) => format!("{f}:{l}"),
        (Some(f), _) => f.to_string(),
        _ => ev.module.unwrap_or("unknown").to_string(),
    };
    let now = chrono::Utc::now().to_rfc3339();

    let existing: HashMap<String, String> = redis.hgetall(&key).await.unwrap_or_default();

    if existing.is_empty() {
        // Enforce max-entries cap: evict oldest on overflow
        let total: i64 = redis.zcard(INDEX_KEY).await.unwrap_or(0);
        if total >= max_entries {
            let oldest: Vec<String> = redis
                .zrange(INDEX_KEY, 0i64, 0i64, None, false, None, false)
                .await
                .unwrap_or_default();
            if let Some(old_fp) = oldest.first() {
                let _ = redis.del::<i64, _>(format!("{KEY_PREFIX}{old_fp}")).await;
                let _ = redis.zrem::<i64, _, _>(INDEX_KEY, old_fp.clone()).await;
            }
        }

        let traceback = format!("{} at {source}\n{}", ev.level, ev.message);
        let mut fields: HashMap<String, String> = HashMap::new();
        fields.insert("count".into(), "1".into());
        fields.insert("first_seen".into(), now.clone());
        fields.insert("last_seen".into(), now);
        fields.insert("type".into(), ev.level.to_string());
        fields.insert("message".into(), ev.message);
        fields.insert("source".into(), source);
        fields.insert("traceback".into(), traceback);
        let _ = redis.hset::<(), _, _>(&key, fields).await;
    } else {
        let count: i64 = existing
            .get("count")
            .and_then(|c| c.parse().ok())
            .unwrap_or(1)
            + 1;
        // Keep `traceback` in sync with the stored message so the detail view is
        // consistent. The message shown in the list is always the latest occurrence.
        let traceback = format!("{} at {source}\n{}", ev.level, ev.message);
        let mut fields: HashMap<String, String> = HashMap::new();
        fields.insert("count".into(), count.to_string());
        fields.insert("last_seen".into(), now);
        fields.insert("message".into(), ev.message);
        fields.insert("source".into(), source);
        fields.insert("traceback".into(), traceback);
        let _ = redis.hset::<(), _, _>(&key, fields).await;
    }

    let _ = redis.expire::<i64, _>(&key, ttl, None).await;
    let _ = redis
        .zadd::<i64, _, _>(
            INDEX_KEY,
            None,
            None,
            false,
            false,
            (ev.timestamp, fp.as_str()),
        )
        .await;
    let _ = redis.expire::<i64, _>(INDEX_KEY, ttl, None).await;
}

// ─── Admin query helpers (used by admin_extended.rs) ─────────────────────────

pub async fn query_list(
    redis: &fred::clients::Client,
    page: i64,
    per_page: i64,
    exception_type: Option<&str>,
) -> serde_json::Value {
    use fred::prelude::SortedSetsInterface;
    use serde_json::json;

    // Most recent first — rev=true with BYSCORE sort
    let fps: Vec<String> = redis
        .zrange(
            INDEX_KEY,
            "+inf",
            "-inf",
            Some(fred::types::sorted_sets::ZSort::ByScore),
            true,
            None,
            false,
        )
        .await
        .unwrap_or_default();

    let mut items: Vec<serde_json::Value> = Vec::new();
    for fp in &fps {
        let data: HashMap<String, String> = redis
            .hgetall(format!("{KEY_PREFIX}{fp}"))
            .await
            .unwrap_or_default();
        if data.is_empty() {
            let _ = redis.zrem::<i64, _, _>(INDEX_KEY, fp.clone()).await;
            continue;
        }
        if let Some(et) = exception_type {
            if data.get("type").map(|s| s.as_str()) != Some(et) {
                continue;
            }
        }
        items.push(json!({
            "fingerprint": fp,
            "type": data.get("type").unwrap_or(&String::new()),
            "message": data.get("message").unwrap_or(&String::new()),
            "count": data.get("count").and_then(|c| c.parse::<i64>().ok()).unwrap_or(1),
            "first_seen": data.get("first_seen").unwrap_or(&String::new()),
            "last_seen": data.get("last_seen").unwrap_or(&String::new()),
            "source": data.get("source").unwrap_or(&String::new()),
        }));
    }

    let total = items.len() as i64;
    let pages = (total + per_page - 1) / per_page;
    let offset = ((page - 1) * per_page) as usize;
    let page_items: Vec<serde_json::Value> = items
        .into_iter()
        .skip(offset)
        .take(per_page as usize)
        .collect();

    json!({
        "items": page_items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    })
}

pub async fn query_detail(
    redis: &fred::clients::Client,
    fingerprint: &str,
) -> Option<serde_json::Value> {
    let data: HashMap<String, String> = redis
        .hgetall(format!("{KEY_PREFIX}{fingerprint}"))
        .await
        .ok()?;
    if data.is_empty() {
        return None;
    }
    Some(serde_json::json!({
        "fingerprint": fingerprint,
        "type": data.get("type").unwrap_or(&String::new()),
        "message": data.get("message").unwrap_or(&String::new()),
        "count": data.get("count").and_then(|c| c.parse::<i64>().ok()).unwrap_or(1),
        "first_seen": data.get("first_seen").unwrap_or(&String::new()),
        "last_seen": data.get("last_seen").unwrap_or(&String::new()),
        "source": data.get("source").unwrap_or(&String::new()),
        "traceback": data.get("traceback").unwrap_or(&String::new()),
    }))
}

pub async fn clear_all(redis: &fred::clients::Client) -> i64 {
    use fred::prelude::SortedSetsInterface;

    let fps: Vec<String> = redis
        .zrange(INDEX_KEY, 0i64, -1i64, None, false, None, false)
        .await
        .unwrap_or_default();
    let count = fps.len() as i64;
    for fp in &fps {
        let _ = redis.del::<i64, _>(format!("{KEY_PREFIX}{fp}")).await;
    }
    let _ = redis.del::<i64, _>(INDEX_KEY).await;
    count
}

pub async fn clear_one(redis: &fred::clients::Client, fingerprint: &str) -> bool {
    let deleted: i64 = redis
        .del::<i64, _>(format!("{KEY_PREFIX}{fingerprint}"))
        .await
        .unwrap_or(0);
    if deleted > 0 {
        let _ = redis.zrem::<i64, _, _>(INDEX_KEY, fingerprint).await;
    }
    deleted > 0
}
