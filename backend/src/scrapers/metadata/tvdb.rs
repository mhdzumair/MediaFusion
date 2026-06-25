//! TVDB API v4 metadata provider.

use std::collections::HashMap;
use std::sync::OnceLock;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::Value;
use tokio::sync::Mutex;
use tracing::warn;

use crate::db::{MediaType, NormalizedEpisode, NormalizedMetadata, NormalizedSeason};

use super::MetadataMatch;

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
    if let Some(ref t) = guard.token
        && now_secs() < guard.expires_at {
            return Some(t.clone());
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

pub async fn search(
    http: &reqwest::Client,
    api_key: &str,
    title: &str,
    media_type: &str,
    limit: usize,
) -> Vec<MetadataMatch> {
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

        let poster = item["image_url"]
            .as_str()
            .filter(|s| !s.is_empty())
            .map(str::to_string);

        out.push(MetadataMatch {
            provider: "tvdb".to_string(),
            external_id: tvdb_id,
            title: name,
            year,
            poster_url: poster,
        });
    }

    out
}

/// Search TVDB for import UI matches (JSON payload for analyze UI).
pub async fn search_import_tvdb(
    http: &reqwest::Client,
    api_key: &str,
    title: &str,
    media_type: &str,
    limit: usize,
) -> Vec<serde_json::Value> {
    search(http, api_key, title, media_type, limit)
        .await
        .into_iter()
        .map(|m| {
            let result_type = if media_type == "movie" {
                "movie"
            } else {
                "series"
            };
            serde_json::json!({
                "id": format!("tvdb:{}", m.external_id),
                "tvdb_id": m.external_id,
                "title": m.title,
                "year": m.year,
                "poster": m.poster_url,
                "type": result_type,
            })
        })
        .collect()
}

pub async fn fetch_by_id(
    http: &reqwest::Client,
    api_key: &str,
    tvdb_id: &str,
    is_series: bool,
) -> Option<NormalizedMetadata> {
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

    let genres: Vec<String> = record["genres"]
        .as_array()
        .map(|arr| {
            arr.iter()
                .filter_map(|g| g["name"].as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();

    let mut seasons = vec![];
    if is_series {
        seasons = fetch_series_episodes(http, api_key, tvdb_id).await;
    }

    let mut external_ids = vec![("tvdb".to_string(), tvdb_id.to_string())];
    if let Some(remotes) = record["remoteIds"].as_array() {
        for remote in remotes {
            let source = remote["sourceName"]
                .as_str()
                .unwrap_or("")
                .to_ascii_lowercase();
            let id = remote["id"].as_str().unwrap_or("");
            if id.is_empty() {
                continue;
            }
            match source.as_str() {
                "imdb" | "themoviedb" | "tmdb" => {
                    let provider = if source == "imdb" { "imdb" } else { "tmdb" };
                    external_ids.push((provider.to_string(), id.to_string()));
                }
                _ => {}
            }
        }
    }

    let mut poster_url = record["image"]
        .as_str()
        .filter(|s| !s.is_empty())
        .map(str::to_string);
    let mut backdrop_url = None;
    let mut logo_url = None;
    if let Some(artworks) = record["artworks"].as_array() {
        for art in artworks {
            let art_type = art["type"].as_i64().unwrap_or(0);
            let url = art["image"]
                .as_str()
                .filter(|s| !s.is_empty())
                .map(str::to_string);
            let Some(url) = url else { continue };
            match art_type {
                2 if poster_url.is_none() => poster_url = Some(url),
                3 | 15 if backdrop_url.is_none() => backdrop_url = Some(url),
                23 | 25 if logo_url.is_none() => logo_url = Some(url),
                _ => {}
            }
        }
    }

    let cast: Vec<crate::db::NormalizedCastMember> = record["characters"]
        .as_array()
        .map(|arr| {
            arr.iter()
                .take(20)
                .enumerate()
                .filter_map(|(i, c)| {
                    let person = &c["peopleType"];
                    Some(crate::db::NormalizedCastMember {
                        name: person["name"].as_str()?.to_string(),
                        character: c["name"].as_str().map(str::to_string),
                        order: i as i32,
                        tmdb_id: None,
                        imdb_id: None,
                        profile_url: person["image"].as_str().map(str::to_string),
                    })
                })
                .collect()
        })
        .unwrap_or_default();

    let trailers: Vec<crate::db::NormalizedTrailer> = record["trailers"]
        .as_array()
        .map(|arr| {
            arr.iter()
                .take(5)
                .enumerate()
                .filter_map(|(i, t)| {
                    let url = t["url"].as_str()?;
                    let key = url.rsplit('/').next()?.split('?').next()?;
                    Some(crate::db::NormalizedTrailer {
                        video_key: key.to_string(),
                        site: "YouTube".to_string(),
                        name: t["name"].as_str().map(str::to_string),
                        trailer_type: "trailer".to_string(),
                        is_official: true,
                        is_primary: i == 0,
                        size: None,
                    })
                })
                .collect()
        })
        .unwrap_or_default();

    let network = if is_series {
        record["originalNetwork"]["name"]
            .as_str()
            .or_else(|| record["latestNetwork"]["name"].as_str())
            .map(str::to_string)
    } else {
        None
    };

    Some(NormalizedMetadata {
        media_type: if is_series {
            MediaType::Series
        } else {
            MediaType::Movie
        },
        title,
        original_title: None,
        year,
        description: record["overview"].as_str().map(str::to_string),
        tagline: None,
        release_date: record["firstAired"]
            .as_str()
            .or_else(|| record["released"].as_str())
            .map(str::to_string),
        runtime_minutes: record["runtime"].as_i64().map(|r| r as i32),
        original_language: record["originalLanguage"].as_str().map(str::to_string),
        status: record["status"]["name"].as_str().map(str::to_string),
        poster_url,
        backdrop_url,
        logo_url,
        genres,
        external_ids,
        catalogs: vec![],
        seasons,
        cast,
        trailers,
        network,
        ..Default::default()
    })
}

async fn fetch_series_episodes(
    http: &reqwest::Client,
    api_key: &str,
    tvdb_id: &str,
) -> Vec<NormalizedSeason> {
    let mut all_episodes: Vec<Value> = vec![];
    let mut page = 0;

    loop {
        let path = format!("series/{tvdb_id}/episodes/default/eng");
        let page_str = page.to_string();
        let data = if page == 0 {
            tvdb_get(http, api_key, &path, &[]).await
        } else {
            tvdb_get(http, api_key, &path, &[("page", &page_str)]).await
        };

        let Some(data) = data else {
            if page == 0 {
                let alt_path = format!("series/{tvdb_id}/episodes/official/eng");
                if let Some(alt) = tvdb_get(http, api_key, &alt_path, &[]).await
                    && let Some(eps) = alt["data"]["episodes"].as_array() {
                        all_episodes.extend(eps.clone());
                    }
            }
            break;
        };

        let episodes = data["data"]["episodes"].as_array();
        let total = data["data"]["total"].as_i64().unwrap_or(0) as usize;
        if let Some(eps) = episodes {
            if eps.is_empty() {
                break;
            }
            all_episodes.extend(eps.clone());
            if all_episodes.len() >= total {
                break;
            }
        } else {
            break;
        }
        page += 1;
        if page > 50 {
            break;
        }
    }

    group_episodes_by_season(&all_episodes)
}

fn group_episodes_by_season(episodes: &[Value]) -> Vec<NormalizedSeason> {
    let mut by_season: HashMap<i32, Vec<NormalizedEpisode>> = HashMap::new();

    for ep in episodes {
        let season_number = ep["seasonNumber"].as_i64().unwrap_or(1) as i32;
        let episode_number = ep["number"].as_i64().unwrap_or(1) as i32;
        let title = ep["name"].as_str().unwrap_or("Episode").to_string();
        let still_url = ep["image"]
            .as_str()
            .filter(|s| !s.is_empty())
            .map(str::to_string);

        by_season
            .entry(season_number)
            .or_default()
            .push(NormalizedEpisode {
                episode_number,
                title,
                overview: ep["overview"].as_str().map(str::to_string),
                air_date: ep["aired"].as_str().map(str::to_string),
                runtime_minutes: ep["runtime"].as_i64().map(|r| r as i32),
                still_url,
                imdb_id: ep["imdbId"].as_str().map(str::to_string),
                tmdb_id: ep["tmdbId"].as_i64().map(|id| id as i32),
                tvdb_id: ep["id"].as_i64().map(|id| id as i32),
            });
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
