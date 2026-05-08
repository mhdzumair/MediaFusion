use std::sync::Arc;

use reqwest::Client;
use serde_json::{json, Value};
use tokio::sync::Semaphore;

use crate::{
    parser,
    scrapers::{ScrapedStream, SearchMeta, StreamFile},
};

pub async fn scrape(
    client: &Client,
    base_url: &str,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> Vec<ScrapedStream> {
    // Fire POST /dmm/search and GET /dmm/filtered concurrently.
    let search_fut = client
        .post(format!("{base_url}/dmm/search"))
        .timeout(std::time::Duration::from_secs(10))
        .json(&json!({"queryText": meta.title}))
        .send();

    let mut filter_params: Vec<(&str, String)> = vec![("Query", meta.title.clone())];
    if media_type == "movie" {
        if let Some(y) = meta.year {
            filter_params.push(("Year", y.to_string()));
        }
    } else {
        if let Some(s) = season {
            filter_params.push(("Season", s.to_string()));
        }
        if let Some(e) = episode {
            filter_params.push(("Episode", e.to_string()));
        }
    }

    let filtered_fut = client
        .get(format!("{base_url}/dmm/filtered"))
        .timeout(std::time::Duration::from_secs(10))
        .query(&filter_params)
        .send();

    let (search_res, filtered_res) = tokio::join!(search_fut, filtered_fut);

    let mut raw_items: Vec<Value> = Vec::new();

    match search_res {
        Ok(resp) => match resp.json::<Vec<Value>>().await {
            Ok(items) => raw_items.extend(items),
            Err(e) => tracing::debug!("zilean /dmm/search parse error: {e}"),
        },
        Err(e) => tracing::debug!("zilean /dmm/search request error: {e}"),
    }
    match filtered_res {
        Ok(resp) => match resp.json::<Vec<Value>>().await {
            Ok(items) => raw_items.extend(items),
            Err(e) => tracing::debug!("zilean /dmm/filtered parse error: {e}"),
        },
        Err(e) => tracing::debug!("zilean /dmm/filtered request error: {e}"),
    }

    if raw_items.is_empty() {
        return vec![];
    }

    let sem = Arc::new(Semaphore::new(10));
    let media_type = media_type.to_string();
    let mut handles = Vec::with_capacity(raw_items.len());

    for item in raw_items {
        let sem = sem.clone();
        let media_type = media_type.clone();
        handles.push(tokio::spawn(async move {
            let _permit = sem.acquire().await.ok()?;
            process_item(&item, &media_type, season, episode)
        }));
    }

    let mut results = Vec::new();
    for h in handles {
        if let Ok(Some(s)) = h.await {
            results.push(s);
        }
    }
    results
}

fn process_item(
    item: &Value,
    media_type: &str,
    _season: Option<i32>,
    _episode: Option<i32>,
) -> Option<ScrapedStream> {
    let raw_title = item.get("raw_title").and_then(|v| v.as_str())?;

    if parser::contains_adult_keywords(raw_title) {
        return None;
    }

    let info_hash = item.get("info_hash").and_then(|v| v.as_str())?.to_lowercase();
    if info_hash.len() != 40 || !info_hash.chars().all(|c| c.is_ascii_hexdigit()) {
        return None;
    }

    let size = item
        .get("size")
        .and_then(|v| v.as_i64())
        .or_else(|| item.get("size").and_then(|v| v.as_f64()).map(|f| f as i64))
        .map(|s| s.max(0));

    let parsed = parser::parse_title(raw_title);

    let files = if media_type == "series" {
        if parsed.seasons.is_empty() {
            return None;
        }
        let mut f: Vec<StreamFile> = Vec::new();
        if !parsed.episodes.is_empty() {
            for ep in &parsed.episodes {
                f.push(StreamFile {
                    file_index: 0,
                    filename: String::new(),
                    season_number: parsed.seasons[0],
                    episode_number: *ep,
                });
            }
        } else {
            for s in &parsed.seasons {
                f.push(StreamFile {
                    file_index: 0,
                    filename: String::new(),
                    season_number: *s,
                    episode_number: 1,
                });
            }
        }
        f
    } else {
        // Movie: reject if it has season/episode markers
        if !parsed.seasons.is_empty() || !parsed.episodes.is_empty() {
            return None;
        }
        vec![]
    };

    Some(ScrapedStream {
        info_hash,
        name: raw_title.to_string(),
        source: "Zilean DMM".to_string(),
        seeders: None,
        size,
        parsed,
        files,
        is_cached: false,
    })
}
