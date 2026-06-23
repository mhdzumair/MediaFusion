//! IMDb / Cinemeta metadata provider.

use std::collections::HashMap;

use serde_json::Value;

use crate::db::{MediaType, NormalizedEpisode, NormalizedMetadata, NormalizedSeason};

use super::{FetchCtx, MIN_TITLE_SIMILARITY, MetadataMatch, year_matches};

pub async fn fetch_by_id(
    http: &reqwest::Client,
    ctx: &FetchCtx<'_>,
    imdb_id: &str,
    is_series: bool,
) -> Option<NormalizedMetadata> {
    if ctx.tmdb_api_key.is_some() {
        if let Some(meta) =
            super::tmdb::find_by_external(http, ctx, "imdb_id", imdb_id, is_series).await
        {
            return Some(meta);
        }
    }

    if ctx.cinemeta_fallback {
        return cinemeta_fetch(http, imdb_id, is_series).await;
    }
    None
}

async fn cinemeta_fetch(
    http: &reqwest::Client,
    imdb_id: &str,
    is_series: bool,
) -> Option<NormalizedMetadata> {
    let kind = if is_series { "series" } else { "movie" };
    let url = format!("https://v3-cinemeta.strem.io/meta/{kind}/{imdb_id}.json");
    let data: Value = http
        .get(&url)
        .timeout(std::time::Duration::from_secs(10))
        .send()
        .await
        .ok()?
        .json()
        .await
        .ok()?;
    let meta = &data["meta"];
    parse_cinemeta_meta(meta, imdb_id, is_series)
}

fn parse_cinemeta_meta(meta: &Value, imdb_id: &str, is_series: bool) -> Option<NormalizedMetadata> {
    let title = meta["name"].as_str()?.to_string();
    let year = meta["year"]
        .as_str()
        .and_then(|y| y.split('-').next()?.parse().ok())
        .or_else(|| meta["year"].as_i64().map(|y| y as i32));

    let genres: Vec<String> = meta["genre"]
        .as_array()
        .map(|arr| {
            arr.iter()
                .filter_map(|g| g.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();

    let mut seasons = vec![];
    if is_series {
        seasons = videos_to_seasons(meta);
    }

    Some(NormalizedMetadata {
        media_type: if is_series {
            MediaType::Series
        } else {
            MediaType::Movie
        },
        title,
        original_title: None,
        year,
        description: meta["description"].as_str().map(str::to_string),
        tagline: None,
        release_date: meta["released"].as_str().map(str::to_string),
        runtime_minutes: meta["runtime"].as_str().and_then(|r| r.parse().ok()),
        original_language: None,
        status: None,
        poster_url: meta["poster"].as_str().map(str::to_string),
        backdrop_url: meta["background"].as_str().map(str::to_string),
        logo_url: None,
        genres,
        external_ids: vec![("imdb".to_string(), imdb_id.to_string())],
        catalogs: vec![],
        seasons,
        ..Default::default()
    })
}

/// Group Cinemeta `meta.videos[]` by season number into normalized seasons/episodes.
pub fn videos_to_seasons(meta: &Value) -> Vec<NormalizedSeason> {
    let videos = match meta["videos"].as_array() {
        Some(v) if !v.is_empty() => v,
        _ => return vec![],
    };

    let mut by_season: HashMap<i32, Vec<NormalizedEpisode>> = HashMap::new();
    for video in videos {
        let season_number = video["season"].as_i64().unwrap_or(1) as i32;
        let episode_number = video["episode"].as_i64().unwrap_or(1) as i32;
        let title = video["title"]
            .as_str()
            .or_else(|| video["name"].as_str())
            .unwrap_or("Episode")
            .to_string();
        let ep = NormalizedEpisode {
            episode_number,
            title,
            overview: video["overview"].as_str().map(str::to_string),
            air_date: video["released"].as_str().map(str::to_string),
            runtime_minutes: None,
            still_url: video["thumbnail"].as_str().map(str::to_string),
            imdb_id: video["id"].as_str().map(str::to_string),
            tmdb_id: None,
            tvdb_id: None,
        };
        by_season.entry(season_number).or_default().push(ep);
    }

    let mut seasons: Vec<NormalizedSeason> = by_season
        .into_iter()
        .map(|(season_number, mut episodes)| {
            episodes.sort_by_key(|e| e.episode_number);
            NormalizedSeason {
                season_number,
                name: Some(format!("Season {season_number}")),
                overview: None,
                air_date: None,
                episodes,
            }
        })
        .collect();
    seasons.sort_by_key(|s| s.season_number);
    seasons
}

pub async fn search(
    http: &reqwest::Client,
    title: &str,
    year: Option<i32>,
    is_series: bool,
    limit: usize,
) -> Vec<MetadataMatch> {
    let kind = if is_series { "series" } else { "movie" };
    let encoded = urlencoding::encode(title);
    let url = format!("https://v3-cinemeta.strem.io/catalog/{kind}/top/search={encoded}.json");

    let Ok(resp) = http
        .get(&url)
        .timeout(std::time::Duration::from_secs(10))
        .send()
        .await
    else {
        return vec![];
    };
    let Ok(data) = resp.json::<Value>().await else {
        return vec![];
    };
    let Some(metas) = data["metas"].as_array() else {
        return vec![];
    };

    let mut matches = Vec::new();
    for m in metas {
        if matches.len() >= limit {
            break;
        }
        let item_year = m["year"]
            .as_str()
            .and_then(|y| y.split('-').next()?.parse::<i32>().ok())
            .or_else(|| m["year"].as_i64().map(|y| y as i32));
        if !year_matches(year, item_year) {
            continue;
        }

        let title_str = m["name"].as_str().unwrap_or("").to_string();
        let sim = crate::parser::similarity_ratio(title, &title_str);
        if sim < MIN_TITLE_SIMILARITY {
            continue;
        }

        let external_id = match m["id"].as_str() {
            Some(id) if !id.is_empty() => id.to_string(),
            _ => continue,
        };
        let poster_url = m["poster"].as_str().map(str::to_string);

        matches.push(MetadataMatch {
            provider: "imdb".to_string(),
            external_id,
            title: title_str,
            year: item_year,
            poster_url,
        });
    }
    matches
}

pub async fn search_single(
    http: &reqwest::Client,
    title: &str,
    year: Option<i32>,
    is_series: bool,
) -> Option<MetadataMatch> {
    search(http, title, year, is_series, 1)
        .await
        .into_iter()
        .next()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn cinemeta_videos_grouped_into_seasons() {
        let meta = json!({
            "videos": [
                {"season": 1, "episode": 1, "title": "Ep 1", "id": "tt1"},
                {"season": 1, "episode": 2, "title": "Ep 2", "id": "tt2"},
                {"season": 2, "episode": 1, "title": "S2E1", "id": "tt3"}
            ]
        });
        let seasons = videos_to_seasons(&meta);
        assert_eq!(seasons.len(), 2);
        assert_eq!(seasons[0].episodes.len(), 2);
        assert_eq!(seasons[1].season_number, 2);
    }
}
