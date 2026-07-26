//! Scraper site configuration loader (Python `RemoteConfigManager` parity).
//!
//! By default fetches `resources/json/scraper_config.json` from GitHub, caches it
//! in Redis for one hour, and falls back to the bundled local file on failure.
//! Set `USE_CONFIG_SOURCE=local` to read only from disk.

use std::path::Path;

use fred::prelude::KeysInterface;
use fred::types::Expiration;
use serde_json::Value;
use tracing::{info, warn};

use crate::state::AppState;

const CACHE_KEY: &str = "scraper_config";
const CACHE_TTL_SECS: i64 = 3600;

/// Load the full scraper config document (Redis cache → remote/local source).
pub async fn load(state: &AppState) -> Value {
    if let Some(cached) = read_cache(&state.redis).await {
        return cached;
    }

    let cfg = &state.config;
    let loaded = if cfg.use_config_source.eq_ignore_ascii_case("local") {
        load_local(&cfg.scraper_config_path)
    } else if cfg.remote_config_source.starts_with("http://")
        || cfg.remote_config_source.starts_with("https://")
    {
        fetch_remote(&state.http, &cfg.remote_config_source)
            .await
            .or_else(|remote_err| {
                warn!("scraper_config: remote fetch failed ({remote_err}), using local fallback");
                load_local(&cfg.scraper_config_path)
            })
    } else {
        load_local(&cfg.remote_config_source).or_else(|_| load_local(&cfg.scraper_config_path))
    };

    match loaded {
        Ok(config) => {
            write_cache(&state.redis, &config).await;
            config
        }
        Err(err) => {
            warn!("scraper_config: all sources failed: {err}");
            Value::Object(serde_json::Map::new())
        }
    }
}

async fn read_cache(redis: &fred::clients::Client) -> Option<Value> {
    let raw: Option<String> = redis.get(CACHE_KEY).await.ok()?;
    let text = raw.filter(|s| !s.is_empty())?;
    serde_json::from_str(&text).ok()
}

async fn write_cache(redis: &fred::clients::Client, config: &Value) {
    if let Ok(text) = serde_json::to_string(config) {
        let _ = redis
            .set::<(), _, _>(
                CACHE_KEY,
                text,
                Some(Expiration::EX(CACHE_TTL_SECS)),
                None,
                false,
            )
            .await;
    }
}

async fn fetch_remote(http: &reqwest::Client, url: &str) -> Result<Value, String> {
    info!("scraper_config: fetching remote config from {url}");
    let response = http
        .get(url)
        .timeout(std::time::Duration::from_secs(15))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if !response.status().is_success() {
        return Err(format!("HTTP {}", response.status()));
    }
    let text = response.text().await.map_err(|e| e.to_string())?;
    serde_json::from_str(&text).map_err(|e| format!("invalid JSON: {e}"))
}

fn load_local(path: &str) -> Result<Value, String> {
    let resolved = resolve_local_path(path);
    let text = std::fs::read_to_string(&resolved)
        .map_err(|e| format!("failed to read {}: {e}", resolved.display()))?;
    serde_json::from_str(&text).map_err(|e| format!("invalid JSON in {}: {e}", resolved.display()))
}

/// Resolve the local scraper config path, trying Docker and dev layouts.
pub fn resolve_local_path(path: &str) -> std::path::PathBuf {
    let direct = Path::new(path);
    if direct.is_file() {
        return direct.to_path_buf();
    }
    for candidate in [
        path,
        "resources/json/scraper_config.json",
        "../resources/json/scraper_config.json",
    ] {
        let p = Path::new(candidate);
        if p.is_file() {
            return p.to_path_buf();
        }
    }
    direct.to_path_buf()
}

pub fn default_local_config_path() -> String {
    resolve_local_path("resources/json/scraper_config.json")
        .to_string_lossy()
        .into_owned()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolve_local_path_finds_repo_file() {
        let path = resolve_local_path("resources/json/scraper_config.json");
        assert!(
            path.is_file(),
            "expected bundled scraper config at {}",
            path.display()
        );
    }
}
