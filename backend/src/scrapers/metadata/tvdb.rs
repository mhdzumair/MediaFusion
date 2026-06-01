//! TVDB API v4 client for import metadata search (Python `tvdb_data.search_tvdb` parity).
//!
//! Requires `TVDB_API_KEY`. Discover uses per-user keys from profiles; import search uses the
//! server-level key like Python `settings.tvdb_api_key`.

use std::sync::OnceLock;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::Value;
use tokio::sync::Mutex;
use tracing::warn;

const TVDB_API_URL: &str = "https://api4.thetvdb.com/v4";
const TOKEN_TTL_SECS: u64 = 27 * 24 * 3600;

struct TokenCache {
    token: Option<String>,
    expires_at: u64,
}

static TOKEN_CACHE: OnceLock<Mutex<TokenCache>> = OnceLock::new();

fn cache() -> &'static Mutex<TokenCache> {
    TOKEN_CACHE.get_or_init(|| {
        Mutex::new(TokenCache {
            token: None,
            expires_at: 0,
        })
    })
}

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

async fn auth_token(http: &reqwest::Client, api_key: &str) -> Option<String> {
    let mut guard = cache().lock().await;
    if let Some(ref t) = guard.token {
        if now_secs() < guard.expires_at {
            return Some(t.clone());
        }
    }

    let resp = http
        .post(format!("{TVDB_API_URL}/login"))
        .json(&serde_json::json!({ "apikey": api_key }))
        .timeout(Duration::from_secs(30))
        .send()
        .await
        .ok()?;

    if !resp.status().is_success() {
        warn!("TVDB login failed: HTTP {}", resp.status());
        return None;
    }

    let data: Value = resp.json().await.ok()?;
    let token = data["data"]["token"].as_str()?.to_string();
    guard.token = Some(token.clone());
    guard.expires_at = now_secs() + TOKEN_TTL_SECS;
    Some(token)
}

async fn tvdb_get(
    http: &reqwest::Client,
    api_key: &str,
    path: &str,
    query: &[(&str, &str)],
) -> Option<Value> {
    let token = auth_token(http, api_key).await?;
    let mut url = format!("{TVDB_API_URL}/{path}");
    if !query.is_empty() {
        let qs: String = query
            .iter()
            .map(|(k, v)| format!("{}={}", k, urlencoding::encode(v)))
            .collect::<Vec<_>>()
            .join("&");
        url.push('?');
        url.push_str(&qs);
    }

    let resp = http
        .get(&url)
        .header("Authorization", format!("Bearer {token}"))
        .header("Accept", "application/json")
        .timeout(Duration::from_secs(30))
        .send()
        .await
        .ok()?;

    if resp.status().as_u16() == 404 {
        return None;
    }
    if !resp.status().is_success() {
        warn!("TVDB GET {path} failed: HTTP {}", resp.status());
        return None;
    }

    resp.json().await.ok()
}

/// Search TVDB for import UI matches (Python `search_tvdb` / lightweight `search_multiple_tvdb`).
pub async fn search_import_tvdb(
    http: &reqwest::Client,
    api_key: &str,
    title: &str,
    media_type: &str,
    limit: usize,
) -> Vec<serde_json::Value> {
    let tvdb_type = if media_type == "movie" {
        "movie"
    } else {
        "series"
    };
    let data = match tvdb_get(
        http,
        api_key,
        "search",
        &[("query", title), ("type", tvdb_type)],
    )
    .await
    {
        Some(d) => d,
        None => return Vec::new(),
    };

    let items = match data["data"].as_array() {
        Some(a) => a,
        None => return Vec::new(),
    };

    let mut out = Vec::new();
    for item in items.iter().take(limit) {
        if item["network"].as_str() == Some("YouTube") {
            continue;
        }
        let image = item["image_url"].as_str().unwrap_or("");
        if item["network"].as_str().is_none()
            && (image.is_empty() || image.contains("/images/missing/"))
        {
            continue;
        }

        let tvdb_id = item["tvdb_id"]
            .as_i64()
            .map(|n| n.to_string())
            .or_else(|| item["id"].as_i64().map(|n| n.to_string()))
            .or_else(|| item["tvdb_id"].as_str().map(str::to_string))
            .or_else(|| item["id"].as_str().map(str::to_string));
        let Some(tvdb_id) = tvdb_id else {
            continue;
        };

        let name = item["name"]
            .as_str()
            .or_else(|| item["title"].as_str())
            .unwrap_or("")
            .to_string();
        if name.is_empty() {
            continue;
        }

        let year = item["year"]
            .as_str()
            .and_then(|y| y.parse::<i32>().ok())
            .or_else(|| item["year"].as_i64().map(|y| y as i32));

        let result_type = if item["type"].as_str().unwrap_or("").to_lowercase() == "movie" {
            "movie"
        } else {
            "series"
        };

        let poster = item["image_url"]
            .as_str()
            .filter(|s| !s.is_empty())
            .map(str::to_string);

        out.push(serde_json::json!({
            "id": format!("tvdb:{tvdb_id}"),
            "tvdb_id": tvdb_id,
            "title": name,
            "year": year,
            "poster": poster,
            "type": result_type,
        }));
    }

    out
}

/// Fetch extended TVDB metadata by numeric TVDB id (Python `get_tvdb_series_data` / movie parity).
pub async fn fetch_tvdb_details(
    http: &reqwest::Client,
    api_key: &str,
    tvdb_id: &str,
    is_series: bool,
) -> Option<crate::scrapers::metadata::TmdbDetails> {
    let path = if is_series {
        format!("series/{tvdb_id}/extended")
    } else {
        format!("movies/{tvdb_id}/extended")
    };
    let data = tvdb_get(http, api_key, &path, &[]).await?;
    let record = &data["data"];
    let title = record["name"].as_str()?.to_string();
    let year = record["year"]
        .as_str()
        .and_then(|y| y.parse().ok())
        .or_else(|| record["year"].as_i64().map(|y| y as i32));
    let description = record["overview"].as_str().map(str::to_string);
    let poster_url = record["image"]
        .as_str()
        .filter(|s| !s.is_empty())
        .map(str::to_string);
    let release_date = record["firstAired"]
        .as_str()
        .or_else(|| record["released"].as_str())
        .map(str::to_string);
    Some(crate::scrapers::metadata::TmdbDetails {
        title,
        year,
        description,
        poster_url,
        backdrop_url: None,
        release_date,
        imdb_id: None,
        tmdb_id: None,
        is_series,
    })
}
