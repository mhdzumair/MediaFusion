use reqwest::Client;
use serde::Deserialize;

use crate::{
    parser,
    scrapers::{prowlarr::build_series_files, ScrapedStream, SearchMeta},
};

// ─── Jackett JSON response shapes ─────────────────────────────────────────────

#[derive(Debug, Deserialize)]
struct JackettResponse {
    #[serde(rename = "Results", default)]
    results: Vec<JackettResult>,
}

#[derive(Debug, Deserialize)]
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
}

// ─── Public entry point ───────────────────────────────────────────────────────

pub async fn scrape(
    client: &Client,
    base_url: &str,
    api_key: &str,
    meta: &SearchMeta,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> Vec<ScrapedStream> {
    let imdb_id = meta.imdb_id.as_deref().unwrap_or("");
    let is_series = media_type == "series";

    // Jackett supports a single all-indexers search endpoint.
    let query = if !imdb_id.is_empty() {
        format!("{{IMDbId:{imdb_id}}}")
    } else {
        build_title_query(&meta.title, meta.year, media_type, season, episode)
    };

    let categories: &[&str] = if is_series {
        &[
            "5000", "5010", "5020", "5030", "5040", "5045", "5050", "5060", "5070",
        ]
    } else {
        &[
            "2000", "2010", "2020", "2030", "2040", "2045", "2050", "2060", "2070",
        ]
    };

    // Build params with repeated Category[] keys
    let mut params: Vec<(&str, String)> = vec![("apikey", api_key.to_string()), ("Query", query)];
    for cat in categories {
        params.push(("Category[]", cat.to_string()));
    }

    let resp = match client
        .get(format!("{base_url}/api/v2.0/indexers/all/results"))
        .query(&params)
        .timeout(std::time::Duration::from_secs(30))
        .send()
        .await
    {
        Ok(r) => r,
        Err(e) => {
            tracing::debug!("jackett request failed: {e}");
            return vec![];
        }
    };

    let body: JackettResponse = match resp.json().await {
        Ok(v) => v,
        Err(e) => {
            tracing::debug!("jackett response parse failed: {e}");
            return vec![];
        }
    };

    body.results
        .into_iter()
        .filter_map(|r| parse_result(r, media_type, season, episode))
        .collect()
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

fn build_title_query(
    title: &str,
    year: Option<i32>,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> String {
    if media_type == "series" {
        match (season, episode) {
            (Some(s), Some(e)) => format!("{title} S{s:02}E{e:02}"),
            (Some(s), None) => format!("{title} S{s:02}"),
            _ => title.to_string(),
        }
    } else if let Some(y) = year {
        format!("{title} {y}")
    } else {
        title.to_string()
    }
}

fn parse_result(
    item: JackettResult,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> Option<ScrapedStream> {
    let title = item.title.as_deref()?.trim().to_string();
    if title.is_empty() {
        return None;
    }

    let info_hash = item
        .info_hash
        .as_deref()
        .map(|h| h.to_lowercase())
        .filter(|h| h.len() == 40 && h.chars().all(|c| c.is_ascii_hexdigit()))
        .or_else(|| {
            item.magnet_uri
                .as_deref()
                .and_then(parser::extract_info_hash)
        })?;

    let source = item.tracker.unwrap_or_else(|| "Jackett".to_string());

    let parsed = parser::parse_title(&title);
    let files = if media_type == "series" {
        build_series_files(&parsed, season, episode)
    } else {
        vec![]
    };

    Some(ScrapedStream {
        info_hash,
        name: title,
        source,
        seeders: item.seeders,
        size: item.size,
        parsed,
        files,
        is_cached: false,
    })
}
