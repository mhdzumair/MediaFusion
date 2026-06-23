use std::sync::Arc;

use reqwest::Client;
use serde_json::{Value, json};
use tokio::sync::Semaphore;

use crate::{
    parser,
    scrapers::{ScrapedStream, SearchMeta, StreamFile},
    state::KeywordFilterCache,
};

/// POST /dmm/search — broad title lookup.
pub async fn scrape_search(
    client: &Client,
    base_url: &str,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    keyword_filters: &KeywordFilterCache,
) -> Vec<ScrapedStream> {
    let resp = client
        .post(format!("{base_url}/dmm/search"))
        .timeout(std::time::Duration::from_secs(10))
        .json(&json!({"queryText": meta.title}))
        .send()
        .await;

    let raw_items = match resp {
        Ok(r) => parse_json_array(r).await,
        Err(e) => {
            tracing::debug!(
                error_kind = crate::util::http::transport_error_kind(&e),
                "zilean /dmm/search request error: {e}"
            );
            vec![]
        }
    };

    process_items(raw_items, media_type, season, episode, keyword_filters).await
}

/// GET /dmm/filtered — structured movie/series lookup.
pub async fn scrape_filtered(
    client: &Client,
    base_url: &str,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    keyword_filters: &KeywordFilterCache,
) -> Vec<ScrapedStream> {
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

    let resp = client
        .get(format!("{base_url}/dmm/filtered"))
        .timeout(std::time::Duration::from_secs(10))
        .query(&filter_params)
        .send()
        .await;

    let raw_items = match resp {
        Ok(r) => parse_json_array(r).await,
        Err(e) => {
            tracing::debug!(
                error_kind = crate::util::http::transport_error_kind(&e),
                "zilean /dmm/filtered request error: {e}"
            );
            vec![]
        }
    };

    process_items(raw_items, media_type, season, episode, keyword_filters).await
}

pub async fn scrape(
    client: &Client,
    base_url: &str,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    keyword_filters: &KeywordFilterCache,
) -> Vec<ScrapedStream> {
    let (search, filtered) = tokio::join!(
        scrape_search(
            client,
            base_url,
            meta,
            media_type,
            season,
            episode,
            keyword_filters
        ),
        scrape_filtered(
            client,
            base_url,
            meta,
            media_type,
            season,
            episode,
            keyword_filters
        ),
    );
    let mut seen = std::collections::HashSet::new();
    search
        .into_iter()
        .chain(filtered)
        .filter(|s| seen.insert(s.info_hash.clone()))
        .collect()
}

async fn parse_json_array(resp: reqwest::Response) -> Vec<Value> {
    match resp.text().await {
        Ok(body) => match serde_json::from_str::<Vec<Value>>(&body) {
            Ok(items) => items,
            Err(e) => {
                tracing::debug!(
                    "zilean response parse error: {e} — body: {}",
                    body.chars().take(500).collect::<String>()
                );
                vec![]
            }
        },
        Err(e) => {
            tracing::debug!("zilean response body error: {e}");
            vec![]
        }
    }
}

async fn process_items(
    raw_items: Vec<Value>,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    keyword_filters: &KeywordFilterCache,
) -> Vec<ScrapedStream> {
    if raw_items.is_empty() {
        return vec![];
    }

    let sem = Arc::new(Semaphore::new(10));
    let media_type = media_type.to_string();
    let mut handles = Vec::with_capacity(raw_items.len());

    for item in raw_items {
        let sem = sem.clone();
        let media_type = media_type.clone();
        let kf = keyword_filters.clone();
        handles.push(tokio::spawn(async move {
            let _permit = sem.acquire().await.ok()?;
            process_item(&item, &media_type, season, episode, &kf)
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
    keyword_filters: &KeywordFilterCache,
) -> Option<ScrapedStream> {
    let raw_title = item.get("raw_title").and_then(|v| v.as_str())?;

    if keyword_filters.matches_blocked_keyword(raw_title) {
        return None;
    }

    let info_hash = item
        .get("info_hash")
        .and_then(|v| v.as_str())?
        .to_lowercase();
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
        torrent_type: crate::db::TorrentType::Public,
        torrent_file: None,
        announce_list: vec![],
    })
}
