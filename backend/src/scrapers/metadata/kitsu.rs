//! Kitsu API metadata provider for anime.

use serde_json::{json, Value};
use tracing::warn;

use crate::db::{
    MediaType, NormalizedAkaTitle, NormalizedEpisode, NormalizedMetadata, NormalizedRating,
    NormalizedSeason,
};

use super::MetadataMatch;

const KITSU_BASE: &str = "https://kitsu.io/api/edge";

pub async fn search(http: &reqwest::Client, query: &str, limit: usize) -> Vec<MetadataMatch> {
    search_import_kitsu(http, query, limit)
        .await
        .into_iter()
        .filter_map(|entry| {
            let id = entry["id"].as_str()?.strip_prefix("kitsu:")?.to_string();
            Some(MetadataMatch {
                provider: "kitsu".to_string(),
                external_id: id,
                title: entry["title"].as_str().unwrap_or("Unknown").to_string(),
                year: entry["year"].as_i64().map(|y| y as i32),
                poster_url: entry["poster"].as_str().map(str::to_string),
            })
        })
        .collect()
}

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

pub async fn fetch_by_id(http: &reqwest::Client, kitsu_id: &str) -> Option<NormalizedMetadata> {
    let url = format!("{KITSU_BASE}/anime/{kitsu_id}");
    let resp: Value = http
        .get(&url)
        .query(&[("include", "categories,mappings")])
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
    let status = attrs["status"].as_str().map(str::to_string);

    let genres: Vec<String> = resp["included"]
        .as_array()
        .map(|items| {
            items
                .iter()
                .filter(|i| i["type"] == "categories")
                .filter_map(|c| c["attributes"]["title"].as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();

    let mut external_ids = vec![("kitsu".to_string(), kitsu_id.to_string())];
    if let Some(included) = resp["included"].as_array() {
        for item in included {
            if item["type"] == "mappings" {
                let ext = item["attributes"]["externalId"].as_str().unwrap_or("");
                match item["attributes"]["externalSite"].as_str() {
                    Some("myanimelist/anime") if !ext.is_empty() => {
                        external_ids.push(("mal".to_string(), ext.to_string()));
                    }
                    _ => {}
                }
            }
        }
    }

    let mut aka_titles = Vec::new();
    if let Some(titles) = attrs["titles"].as_object() {
        for (lang, value) in titles {
            if let Some(t) = value.as_str().filter(|s| !s.is_empty()) {
                aka_titles.push(NormalizedAkaTitle {
                    title: t.to_string(),
                    language_code: Some(lang.clone()),
                });
            }
        }
    }

    let mut ratings = Vec::new();
    if let Some(raw) = attrs["averageRating"]
        .as_str()
        .and_then(|s| s.parse::<f64>().ok())
    {
        ratings.push(NormalizedRating {
            provider: "kitsu".to_string(),
            rating: raw / 10.0,
            vote_count: attrs["userCount"].as_i64().map(|v| v as i32),
            rating_type: "user".to_string(),
        });
    }

    let episodes = fetch_all_episodes(http, kitsu_id).await;
    let seasons = if episodes.is_empty() {
        vec![NormalizedSeason {
            season_number: 1,
            name: Some("Season 1".to_string()),
            overview: None,
            air_date: None,
            episodes: vec![],
        }]
    } else {
        vec![NormalizedSeason {
            season_number: 1,
            name: Some("Season 1".to_string()),
            overview: None,
            air_date: None,
            episodes,
        }]
    };

    Some(NormalizedMetadata {
        media_type: MediaType::Series,
        title,
        original_title: None,
        year,
        description,
        tagline: None,
        release_date: attrs["startDate"].as_str().map(str::to_string),
        runtime_minutes: attrs["episodeLength"].as_i64().map(|r| r as i32),
        original_language: Some("ja".to_string()),
        status,
        poster_url,
        backdrop_url: attrs["coverImage"]["original"].as_str().map(str::to_string),
        logo_url: None,
        genres,
        external_ids,
        catalogs: vec![],
        seasons,
        aka_titles,
        ratings,
        end_date: attrs["endDate"].as_str().map(str::to_string),
        ..Default::default()
    })
}

async fn fetch_all_episodes(http: &reqwest::Client, kitsu_id: &str) -> Vec<NormalizedEpisode> {
    let mut out = Vec::new();
    let mut offset = 0;
    let limit = 20;

    loop {
        let url = format!("{KITSU_BASE}/anime/{kitsu_id}/episodes");
        let resp: Value = match http
            .get(&url)
            .query(&[
                ("page[offset]", &offset.to_string()),
                ("page[limit]", &limit.to_string()),
            ])
            .header("Accept", "application/vnd.api+json")
            .timeout(std::time::Duration::from_secs(15))
            .send()
            .await
        {
            Ok(r) if r.status().is_success() => match r.json().await {
                Ok(v) => v,
                Err(_) => break,
            },
            _ => break,
        };

        let items = match resp["data"].as_array() {
            Some(a) if !a.is_empty() => a,
            _ => break,
        };

        for (idx, item) in items.iter().enumerate() {
            let attrs = &item["attributes"];
            let episode_number = attrs["number"]
                .as_i64()
                .unwrap_or((offset + idx + 1) as i64) as i32;
            let title = attrs["canonicalTitle"]
                .as_str()
                .or_else(|| attrs["titles"]["en"].as_str())
                .unwrap_or("Episode")
                .to_string();
            out.push(NormalizedEpisode {
                episode_number,
                title,
                overview: attrs["synopsis"].as_str().map(str::to_string),
                air_date: attrs["airdate"].as_str().map(str::to_string),
                runtime_minutes: attrs["length"].as_i64().map(|r| r as i32),
                still_url: attrs["thumbnail"]["original"].as_str().map(str::to_string),
                imdb_id: None,
                tmdb_id: None,
                tvdb_id: None,
            });
        }

        offset += items.len();
        if items.len() < limit {
            break;
        }
        if offset > 2000 {
            break;
        }
    }

    out
}
