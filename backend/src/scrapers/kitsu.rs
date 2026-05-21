//! Kitsu API client for anime import search (Python `kitsu_data.py` parity).

use serde_json::{json, Value};
use tracing::warn;

const KITSU_BASE: &str = "https://kitsu.io/api/edge";

/// Search Kitsu anime for import UI matches.
pub async fn search_import_kitsu(http: &reqwest::Client, query: &str, limit: usize) -> Vec<Value> {
    let resp = match http
        .get(format!("{KITSU_BASE}/anime"))
        .query(&[
            ("filter[text]", query),
            ("page[limit]", &limit.min(20).to_string()),
        ])
        .header("Accept", "application/vnd.api+json")
        .timeout(std::time::Duration::from_secs(15))
        .send()
        .await
    {
        Ok(r) if r.status().is_success() => r.json().await.unwrap_or(Value::Null),
        Ok(r) => {
            warn!("Kitsu search HTTP {}", r.status());
            return vec![];
        }
        Err(e) => {
            warn!("Kitsu search error: {e}");
            return vec![];
        }
    };

    resp["data"]
        .as_array()
        .map(|items| items.iter().filter_map(kitsu_anime_to_match).collect())
        .unwrap_or_default()
}

fn kitsu_anime_to_match(item: &Value) -> Option<Value> {
    let kitsu_id = item["id"].as_str()?;
    let attrs = &item["attributes"];
    let title = attrs["canonicalTitle"]
        .as_str()
        .or_else(|| attrs["titles"]["en"].as_str())
        .or_else(|| attrs["titles"]["en_jp"].as_str())
        .unwrap_or("Unknown");
    let year: Option<i32> = attrs["startDate"]
        .as_str()
        .and_then(|d| d.get(..4))
        .and_then(|y| y.parse().ok());
    let poster = attrs["posterImage"]["medium"].as_str().map(str::to_string);

    Some(json!({
        "id": format!("kitsu:{kitsu_id}"),
        "kitsu_id": kitsu_id,
        "title": title,
        "year": year,
        "poster": poster,
        "type": "series",
    }))
}

/// Fetch anime metadata by Kitsu id.
pub async fn fetch_kitsu_by_id(
    http: &reqwest::Client,
    kitsu_id: &str,
) -> Option<crate::scrapers::metadata::TmdbDetails> {
    let url = format!("{KITSU_BASE}/anime/{kitsu_id}");
    let resp: serde_json::Value = http
        .get(&url)
        .header("Accept", "application/vnd.api+json")
        .timeout(std::time::Duration::from_secs(15))
        .send()
        .await
        .ok()?
        .json()
        .await
        .ok()?;

    let attrs = &resp["data"]["attributes"];
    let title = attrs["canonicalTitle"]
        .as_str()
        .or_else(|| attrs["titles"]["en"].as_str())
        .unwrap_or("Unknown")
        .to_string();
    let year: Option<i32> = attrs["startDate"]
        .as_str()
        .and_then(|d| d.get(..4))
        .and_then(|y| y.parse().ok());
    let poster_url = attrs["posterImage"]["medium"].as_str().map(str::to_string);
    let description = attrs["synopsis"].as_str().map(str::to_string);

    Some(crate::scrapers::metadata::TmdbDetails {
        title,
        year,
        description,
        poster_url,
        backdrop_url: None,
        release_date: None,
        imdb_id: None,
        tmdb_id: None,
        is_series: true,
    })
}
