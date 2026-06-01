//! AniList GraphQL client for anime import search (Python `mal_data.py` parity).

use serde_json::{json, Value};
use tracing::warn;

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

const MAL_LOOKUP_QUERY: &str = r#"
query ($malId: Int!) {
  Media(idMal: $malId, type: ANIME) {
    id
    idMal
    title { romaji english native }
    startDate { year }
    coverImage { large }
    description(asHtml: false)
  }
}
"#;

/// Search AniList for anime import UI matches.
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

/// Fetch anime details by MAL id via AniList bridge.
pub async fn fetch_anime_by_mal_id(
    http: &reqwest::Client,
    mal_id: &str,
) -> Option<crate::scrapers::metadata::TmdbDetails> {
    let mal_int: i32 = mal_id.parse().ok()?;
    let body = json!({
        "query": MAL_LOOKUP_QUERY,
        "variables": { "malId": mal_int },
    });

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

const ANILIST_BY_ID_QUERY: &str = r#"
query ($id: Int!) {
  Media(id: $id, type: ANIME) {
    id
    idMal
    title { romaji english native }
    startDate { year }
    coverImage { large }
    description(asHtml: false)
  }
}
"#;

/// Fetch anime metadata by AniList numeric id.
pub async fn fetch_anilist_by_id(
    http: &reqwest::Client,
    anilist_id: &str,
) -> Option<crate::scrapers::metadata::TmdbDetails> {
    let id: i32 = anilist_id.parse().ok()?;
    let body = json!({
        "query": ANILIST_BY_ID_QUERY,
        "variables": { "id": id },
    });
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

fn details_from_anilist_media(item: &Value) -> Option<crate::scrapers::metadata::TmdbDetails> {
    if item.is_null() {
        return None;
    }
    let title = item["title"]["english"]
        .as_str()
        .or_else(|| item["title"]["romaji"].as_str())
        .unwrap_or("Unknown")
        .to_string();
    let year = item["startDate"]["year"].as_i64().map(|y| y as i32);
    let poster_url = item["coverImage"]["large"].as_str().map(str::to_string);
    let description = item["description"].as_str().map(str::to_string);
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
