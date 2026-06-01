//! AniList GraphQL metadata provider for anime (MAL bridge).

use serde_json::{json, Value};
use tracing::warn;

use crate::db::{
    MediaType, NormalizedAkaTitle, NormalizedMetadata, NormalizedRating, NormalizedSeason,
    NormalizedTrailer,
};

use super::MetadataMatch;

const ANILIST_URL: &str = "https://graphql.anilist.co";

const SEARCH_QUERY: &str = r#"
query ($search: String!, $perPage: Int) {
  Page(perPage: $perPage) {
    media(search: $search, type: ANIME, isAdult: false, sort: SEARCH_MATCH) {
      id
      idMal
      title { romaji english native }
      startDate { year }
      coverImage { large }
    }
  }
}
"#;

const DETAIL_QUERY: &str = r#"
query ($id: Int, $malId: Int) {
  Media(id: $id, idMal: $malId, type: ANIME) {
    id
    idMal
    title { romaji english native }
    synonyms
    startDate { year month day }
    endDate { year month day }
    coverImage { large extraLarge }
    bannerImage
    description(asHtml: false)
    genres
    status
    episodes
    duration
    format
    averageScore
    favourites
    studios(isMain: true) { nodes { name } }
    trailer { id site }
  }
}
"#;

pub async fn search(http: &reqwest::Client, query: &str, limit: usize) -> Vec<MetadataMatch> {
    search_import_anilist(http, query, limit)
        .await
        .into_iter()
        .filter_map(|entry| {
            let id = entry["id"].as_str()?;
            let (provider, ext_id) = if id.starts_with("mal:") {
                ("mal", id.strip_prefix("mal:")?.to_string())
            } else if id.starts_with("anilist:") {
                ("anilist", id.strip_prefix("anilist:")?.to_string())
            } else {
                return None;
            };
            Some(MetadataMatch {
                provider: provider.to_string(),
                external_id: ext_id,
                title: entry["title"].as_str().unwrap_or("Unknown").to_string(),
                year: entry["year"].as_i64().map(|y| y as i32),
                poster_url: entry["poster"].as_str().map(str::to_string),
            })
        })
        .collect()
}

pub async fn search_import_anilist(
    http: &reqwest::Client,
    query: &str,
    limit: usize,
) -> Vec<Value> {
    let variables = json!({ "search": query, "perPage": limit.min(20) });
    let body = json!({ "query": SEARCH_QUERY, "variables": variables });

    let resp = match http
        .post(ANILIST_URL)
        .json(&body)
        .timeout(std::time::Duration::from_secs(15))
        .send()
        .await
    {
        Ok(r) if r.status().is_success() => r.json().await.unwrap_or(Value::Null),
        Ok(r) => {
            warn!("AniList search HTTP {}", r.status());
            return vec![];
        }
        Err(e) => {
            warn!("AniList search error: {e}");
            return vec![];
        }
    };

    let media = resp["data"]["Page"]["media"].as_array();
    media
        .map(|items| items.iter().filter_map(anilist_media_to_match).collect())
        .unwrap_or_default()
}

fn anilist_media_to_match(item: &Value) -> Option<Value> {
    let anilist_id = item["id"].as_i64()?;
    let mal_id = item["idMal"].as_i64();
    let title = item["title"]["english"]
        .as_str()
        .or_else(|| item["title"]["romaji"].as_str())
        .or_else(|| item["title"]["native"].as_str())
        .unwrap_or("Unknown");
    let year = item["startDate"]["year"].as_i64().map(|y| y as i32);
    let poster = item["coverImage"]["large"].as_str().map(str::to_string);

    let primary_id = mal_id
        .map(|id| format!("mal:{id}"))
        .unwrap_or_else(|| format!("anilist:{anilist_id}"));

    Some(json!({
        "id": primary_id,
        "mal_id": mal_id.map(|id| id.to_string()),
        "anilist_id": anilist_id.to_string(),
        "title": title,
        "year": year,
        "poster": poster,
        "type": "series",
    }))
}

pub async fn fetch_by_mal_id(http: &reqwest::Client, mal_id: &str) -> Option<NormalizedMetadata> {
    let mal_int: i32 = mal_id.parse().ok()?;
    fetch_detail(http, json!({ "malId": mal_int })).await
}

pub async fn fetch_by_id(http: &reqwest::Client, anilist_id: &str) -> Option<NormalizedMetadata> {
    let id: i32 = anilist_id.parse().ok()?;
    fetch_detail(http, json!({ "id": id })).await
}

async fn fetch_detail(http: &reqwest::Client, variables: Value) -> Option<NormalizedMetadata> {
    let body = json!({ "query": DETAIL_QUERY, "variables": variables });
    let resp: Value = http
        .post(ANILIST_URL)
        .json(&body)
        .timeout(std::time::Duration::from_secs(15))
        .send()
        .await
        .ok()?
        .json()
        .await
        .ok()?;

    details_from_anilist_media(&resp["data"]["Media"])
}

fn details_from_anilist_media(item: &Value) -> Option<NormalizedMetadata> {
    if item.is_null() {
        return None;
    }
    let title = item["title"]["english"]
        .as_str()
        .or_else(|| item["title"]["romaji"].as_str())
        .unwrap_or("Unknown")
        .to_string();
    let year = item["startDate"]["year"].as_i64().map(|y| y as i32);

    let release_date = {
        let y = item["startDate"]["year"].as_i64();
        y.map(|year| {
            let m = item["startDate"]["month"].as_i64().unwrap_or(1);
            let d = item["startDate"]["day"].as_i64().unwrap_or(1);
            format!("{year:04}-{m:02}-{d:02}")
        })
    };

    let genres: Vec<String> = item["genres"]
        .as_array()
        .map(|arr| {
            arr.iter()
                .filter_map(|g| g.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();

    let poster_url = item["coverImage"]["large"].as_str().map(str::to_string);
    let backdrop_url = item["bannerImage"]
        .as_str()
        .or_else(|| item["coverImage"]["extraLarge"].as_str())
        .map(str::to_string);
    let description = item["description"].as_str().map(str::to_string);
    let status = item["status"].as_str().map(str::to_string);
    let runtime_minutes = item["duration"].as_i64().map(|d| d as i32);

    let end_date = {
        let y = item["endDate"]["year"].as_i64();
        y.map(|year| {
            let m = item["endDate"]["month"].as_i64().unwrap_or(1);
            let d = item["endDate"]["day"].as_i64().unwrap_or(1);
            format!("{year:04}-{m:02}-{d:02}")
        })
    };

    let network = item["studios"]["nodes"]
        .as_array()
        .and_then(|nodes| nodes.first())
        .and_then(|n| n["name"].as_str())
        .map(str::to_string);

    let mut aka_titles: Vec<NormalizedAkaTitle> = item["synonyms"]
        .as_array()
        .map(|arr| {
            arr.iter()
                .filter_map(|s| {
                    let t = s.as_str()?.trim();
                    if t.is_empty() {
                        None
                    } else {
                        Some(NormalizedAkaTitle {
                            title: t.to_string(),
                            language_code: None,
                        })
                    }
                })
                .collect()
        })
        .unwrap_or_default();
    for (lang, field) in [("romaji", "romaji"), ("native", "native")] {
        if let Some(t) = item["title"][field].as_str().filter(|s| !s.is_empty()) {
            aka_titles.push(NormalizedAkaTitle {
                title: t.to_string(),
                language_code: Some(lang.to_string()),
            });
        }
    }

    let mut ratings = Vec::new();
    if let Some(score) = item["averageScore"].as_i64().filter(|s| *s > 0) {
        ratings.push(NormalizedRating {
            provider: "mal".to_string(),
            rating: score as f64 / 10.0,
            vote_count: item["favourites"].as_i64().map(|v| v as i32),
            rating_type: "user".to_string(),
        });
    }

    let trailers = if item["trailer"]["site"].as_str() == Some("youtube") {
        vec![NormalizedTrailer {
            video_key: item["trailer"]["id"].as_str().unwrap_or_default().to_string(),
            site: "YouTube".to_string(),
            name: Some("Trailer".to_string()),
            trailer_type: "trailer".to_string(),
            is_official: true,
            is_primary: true,
            size: None,
        }]
    } else {
        vec![]
    };

    let mut external_ids = vec![(
        "anilist".to_string(),
        item["id"].as_i64()?.to_string(),
    )];
    if let Some(mal) = item["idMal"].as_i64() {
        external_ids.push(("mal".to_string(), mal.to_string()));
    }

    // AniList has episode count but no per-episode list — season 1 stub.
    let episode_count = item["episodes"].as_i64().unwrap_or(0) as i32;
    let seasons = vec![NormalizedSeason {
        season_number: 1,
        name: Some(format!("Season 1 ({episode_count} episodes)")),
        overview: None,
        air_date: None,
        episodes: vec![],
    }];

    Some(NormalizedMetadata {
        media_type: MediaType::Series,
        title,
        original_title: item["title"]["native"].as_str().map(str::to_string),
        year,
        description,
        tagline: None,
        release_date,
        runtime_minutes,
        original_language: Some("ja".to_string()),
        status,
        poster_url,
        backdrop_url,
        logo_url: None,
        genres,
        external_ids,
        catalogs: vec![],
        seasons,
        end_date,
        network,
        aka_titles,
        ratings,
        trailers,
        ..Default::default()
    })
}
