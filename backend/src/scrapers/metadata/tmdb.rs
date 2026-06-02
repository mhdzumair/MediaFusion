//! TMDB metadata provider — movies, series, seasons, and episodes.

use std::collections::HashSet;

use futures::stream::{self, StreamExt};
use serde_json::Value;

use crate::db::{
    MediaType, NormalizedAkaTitle, NormalizedCastMember, NormalizedCrewMember, NormalizedEpisode,
    NormalizedMetadata, NormalizedRating, NormalizedSeason, NormalizedTrailer, NudityStatus,
};

use super::{year_matches, FetchCtx, MetadataMatch, MIN_TITLE_SIMILARITY};

const TMDB_IMG: &str = "https://image.tmdb.org/t/p";

const IMPORTANT_CREW_JOBS: &[&str] = &[
    "Director",
    "Writer",
    "Screenplay",
    "Producer",
    "Executive Producer",
    "Composer",
    "Director of Photography",
    "Editor",
];

const ADULT_CERT_KEYWORDS: &[&str] = &[
    "nc-17",
    "x",
    "xxx",
    "ao",
    "r18",
    "18+",
    "nr-18",
    "x18",
    "adults only",
];

pub async fn fetch_by_id(
    http: &reqwest::Client,
    ctx: &FetchCtx<'_>,
    external_id: &str,
    is_series: bool,
) -> Option<NormalizedMetadata> {
    let api_key = ctx.tmdb_api_key?;
    let kind = if is_series { "tv" } else { "movie" };
    let url = format!(
        "https://api.themoviedb.org/3/{kind}/{external_id}?api_key={api_key}\
         &append_to_response=external_ids,credits,videos,keywords,alternative_titles,\
         release_dates,content_ratings"
    );
    let data: Value = http
        .get(&url)
        .timeout(std::time::Duration::from_secs(15))
        .send()
        .await
        .ok()?
        .json()
        .await
        .ok()?;

    let mut meta = parse_tmdb_response(&data, is_series)?;

    if is_series {
        meta.seasons = fetch_all_seasons(http, api_key, external_id, &data).await;
    }

    Some(meta)
}

async fn fetch_all_seasons(
    http: &reqwest::Client,
    api_key: &str,
    tmdb_id: &str,
    show_data: &Value,
) -> Vec<NormalizedSeason> {
    let max_season = show_data["number_of_seasons"].as_i64().unwrap_or(0) as i32;
    if max_season <= 0 {
        return vec![];
    }

    let season_numbers: Vec<i32> = (0..=max_season).collect();
    let fetched: Vec<Option<NormalizedSeason>> = stream::iter(season_numbers)
        .map(|n| {
            let http = http.clone();
            let api_key = api_key.to_string();
            let tmdb_id = tmdb_id.to_string();
            async move { fetch_season(&http, &api_key, &tmdb_id, n).await }
        })
        .buffer_unordered(4)
        .collect()
        .await;

    fetched.into_iter().flatten().collect()
}

async fn fetch_season(
    http: &reqwest::Client,
    api_key: &str,
    tmdb_id: &str,
    season_number: i32,
) -> Option<NormalizedSeason> {
    let url = format!(
        "https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season_number}?api_key={api_key}"
    );
    let data: Value = http
        .get(&url)
        .timeout(std::time::Duration::from_secs(15))
        .send()
        .await
        .ok()?
        .json()
        .await
        .ok()?;

    let episodes: Vec<NormalizedEpisode> = data["episodes"]
        .as_array()
        .map(|arr| arr.iter().filter_map(parse_tmdb_episode).collect())
        .unwrap_or_default();

    Some(NormalizedSeason {
        season_number,
        name: data["name"].as_str().map(str::to_string),
        overview: data["overview"]
            .as_str()
            .filter(|s| !s.is_empty())
            .map(str::to_string),
        air_date: data["air_date"]
            .as_str()
            .filter(|s| !s.is_empty())
            .map(str::to_string),
        episodes,
    })
}

fn parse_tmdb_episode(item: &Value) -> Option<NormalizedEpisode> {
    let episode_number = item["episode_number"].as_i64()? as i32;
    let title = item["name"]
        .as_str()
        .filter(|s| !s.is_empty())
        .unwrap_or("Episode")
        .to_string();
    let still_url = item["still_path"]
        .as_str()
        .map(|p| format!("{TMDB_IMG}/w500{p}"));

    Some(NormalizedEpisode {
        episode_number,
        title,
        overview: item["overview"]
            .as_str()
            .filter(|s| !s.is_empty())
            .map(str::to_string),
        air_date: item["air_date"]
            .as_str()
            .filter(|s| !s.is_empty())
            .map(str::to_string),
        runtime_minutes: item["runtime"].as_i64().map(|r| r as i32),
        still_url,
        imdb_id: item["imdb_id"]
            .as_str()
            .filter(|s| !s.is_empty())
            .map(str::to_string),
        tmdb_id: item["id"].as_i64().map(|id| id as i32),
        tvdb_id: None,
    })
}

pub fn parse_tmdb_response(data: &Value, is_series: bool) -> Option<NormalizedMetadata> {
    let title = data["title"]
        .as_str()
        .or_else(|| data["name"].as_str())?
        .to_string();
    let original_title = data["original_title"]
        .as_str()
        .or_else(|| data["original_name"].as_str())
        .map(str::to_string);

    let date = data["release_date"]
        .as_str()
        .or_else(|| data["first_air_date"].as_str())
        .unwrap_or("");
    let year: Option<i32> = if date.len() >= 4 {
        date[..4].parse().ok()
    } else {
        None
    };
    let release_date = if date.is_empty() {
        None
    } else {
        Some(date.to_string())
    };

    let runtime_minutes = if is_series {
        data["episode_run_time"]
            .as_array()
            .and_then(|a| a.first())
            .and_then(|v| v.as_i64())
            .map(|r| r as i32)
    } else {
        data["runtime"].as_i64().map(|r| r as i32)
    };

    let genres: Vec<String> = data["genres"]
        .as_array()
        .map(|arr| {
            arr.iter()
                .filter_map(|g| g["name"].as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();

    let poster_url = data["poster_path"]
        .as_str()
        .map(|p| format!("{TMDB_IMG}/w500{p}"));
    let backdrop_url = data["backdrop_path"]
        .as_str()
        .map(|p| format!("{TMDB_IMG}/original{p}"));

    let mut external_ids = vec![("tmdb".to_string(), data["id"].as_i64()?.to_string())];

    if let Some(ext) = data["external_ids"].as_object() {
        if let Some(imdb) = ext.get("imdb_id").and_then(|v| v.as_str()) {
            if !imdb.is_empty() {
                external_ids.push(("imdb".to_string(), imdb.to_string()));
            }
        }
        if let Some(tvdb) = ext.get("tvdb_id").and_then(|v| v.as_i64()) {
            if tvdb > 0 {
                external_ids.push(("tvdb".to_string(), tvdb.to_string()));
            }
        }
    } else if let Some(imdb) = data["imdb_id"].as_str() {
        if !imdb.is_empty() {
            external_ids.push(("imdb".to_string(), imdb.to_string()));
        }
    }

    let cast: Vec<NormalizedCastMember> = data["credits"]["cast"]
        .as_array()
        .map(|arr| {
            arr.iter()
                .take(20)
                .enumerate()
                .filter_map(|(i, c)| {
                    Some(NormalizedCastMember {
                        name: c["name"].as_str()?.to_string(),
                        character: c["character"].as_str().map(str::to_string),
                        order: c["order"].as_i64().unwrap_or(i as i64) as i32,
                        tmdb_id: c["id"].as_i64().map(|id| id as i32),
                        imdb_id: None,
                        profile_url: c["profile_path"]
                            .as_str()
                            .map(|p| format!("{TMDB_IMG}/w185{p}")),
                    })
                })
                .collect()
        })
        .unwrap_or_default();

    let crew: Vec<NormalizedCrewMember> = data["credits"]["crew"]
        .as_array()
        .map(|arr| {
            arr.iter()
                .filter(|c| {
                    c["job"]
                        .as_str()
                        .is_some_and(|job| IMPORTANT_CREW_JOBS.contains(&job))
                })
                .map(|c| NormalizedCrewMember {
                    name: c["name"].as_str().unwrap_or("Unknown").to_string(),
                    department: c["department"].as_str().map(str::to_string),
                    job: c["job"].as_str().map(str::to_string),
                    tmdb_id: c["id"].as_i64().map(|id| id as i32),
                    imdb_id: None,
                    profile_url: c["profile_path"]
                        .as_str()
                        .map(|p| format!("{TMDB_IMG}/w185{p}")),
                })
                .collect()
        })
        .unwrap_or_default();

    let trailers: Vec<NormalizedTrailer> = data["videos"]["results"]
        .as_array()
        .map(|arr| {
            arr.iter()
                .filter(|v| {
                    v["site"].as_str() == Some("YouTube")
                        && v["type"].as_str().is_some_and(|t| {
                            matches!(t, "Trailer" | "Teaser" | "Clip" | "Featurette")
                        })
                })
                .take(10)
                .enumerate()
                .filter_map(|(i, v)| {
                    Some(NormalizedTrailer {
                        video_key: v["key"].as_str()?.to_string(),
                        site: "YouTube".to_string(),
                        name: v["name"].as_str().map(str::to_string),
                        trailer_type: v["type"].as_str().unwrap_or("Trailer").to_ascii_lowercase(),
                        is_official: v["official"].as_bool().unwrap_or(true),
                        is_primary: i == 0,
                        size: v["size"].as_i64().map(|s| s as i32),
                    })
                })
                .collect()
        })
        .unwrap_or_default();

    let keywords: Vec<String> = if is_series {
        data["keywords"]["results"]
            .as_array()
            .map(|arr| {
                arr.iter()
                    .filter_map(|k| k["name"].as_str().map(str::to_string))
                    .collect()
            })
            .unwrap_or_default()
    } else {
        data["keywords"]["keywords"]
            .as_array()
            .map(|arr| {
                arr.iter()
                    .filter_map(|k| k["name"].as_str().map(str::to_string))
                    .collect()
            })
            .unwrap_or_default()
    };

    let aka_titles: Vec<NormalizedAkaTitle> = if is_series {
        data["alternative_titles"]["results"]
            .as_array()
            .map(|arr| parse_aka_titles(arr))
            .unwrap_or_default()
    } else {
        data["alternative_titles"]["titles"]
            .as_array()
            .map(|arr| parse_aka_titles(arr))
            .unwrap_or_default()
    };

    let certificates = parse_certificates(data, is_series);
    let adult = data["adult"].as_bool().unwrap_or(false);
    let nudity_status = derive_nudity_status(adult, &certificates);

    let mut ratings = Vec::new();
    if let Some(rating) = data["vote_average"].as_f64().filter(|r| *r > 0.0) {
        ratings.push(NormalizedRating {
            provider: "tmdb".to_string(),
            rating,
            vote_count: data["vote_count"].as_i64().map(|v| v as i32),
            rating_type: "user".to_string(),
        });
    }

    let popularity = data["popularity"].as_f64();
    let budget = if is_series {
        None
    } else {
        data["budget"].as_i64().filter(|b| *b > 0)
    };
    let revenue = if is_series {
        None
    } else {
        data["revenue"].as_i64().filter(|b| *b > 0)
    };

    let website = data["homepage"]
        .as_str()
        .filter(|s| !s.is_empty())
        .map(str::to_string);

    let end_date = if is_series {
        data["last_air_date"]
            .as_str()
            .filter(|s| !s.is_empty())
            .map(str::to_string)
    } else {
        None
    };

    let country = data["production_countries"]
        .as_array()
        .and_then(|arr| arr.first())
        .and_then(|c| c["iso_3166_1"].as_str())
        .map(str::to_string);

    let network = if is_series {
        data["networks"]
            .as_array()
            .and_then(|arr| arr.first())
            .and_then(|n| n["name"].as_str())
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
        original_title,
        year,
        description: data["overview"]
            .as_str()
            .filter(|s| !s.is_empty())
            .map(str::to_string),
        tagline: data["tagline"]
            .as_str()
            .filter(|s| !s.is_empty())
            .map(str::to_string),
        release_date,
        runtime_minutes,
        original_language: data["original_language"].as_str().map(str::to_string),
        status: data["status"].as_str().map(str::to_string),
        poster_url,
        backdrop_url,
        logo_url: None,
        genres,
        external_ids,
        catalogs: vec![],
        seasons: vec![],
        cast,
        crew,
        trailers,
        website,
        end_date,
        country,
        network,
        aka_titles,
        keywords,
        ratings,
        certificates,
        popularity,
        adult,
        nudity_status,
        budget,
        revenue,
    })
}

fn parse_aka_titles(arr: &[Value]) -> Vec<NormalizedAkaTitle> {
    arr.iter()
        .filter_map(|item| {
            let title = item["title"].as_str()?.trim();
            if title.is_empty() {
                return None;
            }
            Some(NormalizedAkaTitle {
                title: title.to_string(),
                language_code: item["iso_3166_1"]
                    .as_str()
                    .or_else(|| item["iso_639_1"].as_str())
                    .map(str::to_string),
            })
        })
        .collect()
}

fn parse_certificates(data: &Value, is_series: bool) -> Vec<String> {
    let mut certs = HashSet::new();
    if is_series {
        if let Some(results) = data["content_ratings"]["results"].as_array() {
            for entry in results {
                if let Some(rating) = entry["rating"].as_str().filter(|s| !s.is_empty()) {
                    certs.insert(rating.to_string());
                }
            }
        }
    } else if let Some(results) = data["release_dates"]["results"].as_array() {
        for country_entry in results {
            if let Some(dates) = country_entry["release_dates"].as_array() {
                for rd in dates {
                    if let Some(cert) = rd["certification"].as_str().filter(|s| !s.is_empty()) {
                        certs.insert(cert.to_string());
                    }
                }
            }
        }
    }
    certs.into_iter().collect()
}

fn derive_nudity_status(adult: bool, certificates: &[String]) -> NudityStatus {
    if adult
        || certificates.iter().any(|c| {
            ADULT_CERT_KEYWORDS
                .iter()
                .any(|kw| c.eq_ignore_ascii_case(kw))
        })
    {
        NudityStatus::Severe
    } else {
        NudityStatus::Unknown
    }
}

pub async fn search(
    http: &reqwest::Client,
    ctx: &FetchCtx<'_>,
    title: &str,
    year: Option<i32>,
    is_series: bool,
    limit: usize,
) -> Vec<MetadataMatch> {
    let Some(api_key) = ctx.tmdb_api_key else {
        return vec![];
    };
    let kind = if is_series { "tv" } else { "movie" };
    let encoded = urlencoding::encode(title);
    let url =
        format!("https://api.themoviedb.org/3/search/{kind}?api_key={api_key}&query={encoded}");

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
    let Some(results) = data["results"].as_array() else {
        return vec![];
    };

    let mut matches = Vec::new();
    for item in results {
        if matches.len() >= limit {
            break;
        }
        let date = item["release_date"]
            .as_str()
            .or_else(|| item["first_air_date"].as_str())
            .unwrap_or("");
        let item_year: Option<i32> = if date.len() >= 4 {
            date[..4].parse().ok()
        } else {
            None
        };
        if !year_matches(year, item_year) {
            continue;
        }

        let title_str = item["title"]
            .as_str()
            .or_else(|| item["name"].as_str())
            .unwrap_or("")
            .to_string();
        let sim = crate::parser::similarity_ratio(title, &title_str);
        if sim < MIN_TITLE_SIMILARITY {
            continue;
        }

        let external_id = match item["id"].as_i64() {
            Some(id) => id.to_string(),
            None => continue,
        };
        let poster_url = item["poster_path"]
            .as_str()
            .map(|p| format!("{TMDB_IMG}/w500{p}"));

        matches.push(MetadataMatch {
            provider: "tmdb".to_string(),
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
    ctx: &FetchCtx<'_>,
    title: &str,
    year: Option<i32>,
    is_series: bool,
) -> Option<MetadataMatch> {
    search(http, ctx, title, year, is_series, 1)
        .await
        .into_iter()
        .next()
}

pub async fn imdb_id_from_tmdb(
    http: &reqwest::Client,
    tmdb_id: &str,
    is_series: bool,
    api_key: &str,
) -> Option<String> {
    let kind = if is_series { "tv" } else { "movie" };
    let url =
        format!("https://api.themoviedb.org/3/{kind}/{tmdb_id}/external_ids?api_key={api_key}");
    let data: Value = http
        .get(&url)
        .timeout(std::time::Duration::from_secs(10))
        .send()
        .await
        .ok()?
        .json()
        .await
        .ok()?;

    data["imdb_id"]
        .as_str()
        .filter(|s| !s.is_empty())
        .map(str::to_string)
}

/// Resolve TMDB details via IMDb or TVDB external id lookup.
pub async fn find_by_external(
    http: &reqwest::Client,
    ctx: &FetchCtx<'_>,
    source: &str,
    external_id: &str,
    is_series: bool,
) -> Option<NormalizedMetadata> {
    let api_key = ctx.tmdb_api_key?;
    let url = format!(
        "https://api.themoviedb.org/3/find/{external_id}?api_key={api_key}&external_source={source}"
    );
    let data: Value = http
        .get(&url)
        .timeout(std::time::Duration::from_secs(10))
        .send()
        .await
        .ok()?
        .json()
        .await
        .ok()?;

    let results = if is_series {
        data["tv_results"].as_array()
    } else {
        data["movie_results"].as_array()
    }?;
    let tmdb_id = results.first()?["id"].as_i64()?.to_string();
    fetch_by_id(http, ctx, &tmdb_id, is_series).await
}

/// Back-compat shim for callers still using flat details.
pub fn to_legacy_details(meta: &NormalizedMetadata) -> super::TmdbDetails {
    super::TmdbDetails {
        title: meta.title.clone(),
        year: meta.year,
        description: meta.description.clone(),
        poster_url: meta.poster_url.clone(),
        backdrop_url: meta.backdrop_url.clone(),
        release_date: meta.release_date.clone(),
        imdb_id: meta.external_id("imdb").map(str::to_string),
        tmdb_id: meta.external_id("tmdb").map(str::to_string),
        is_series: meta.media_type == MediaType::Series,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn parse_tmdb_series_includes_genres_and_external_ids() {
        let data = json!({
            "id": 66732,
            "name": "Stranger Things",
            "first_air_date": "2016-07-15",
            "overview": "Test",
            "tagline": "Every ending has a beginning.",
            "status": "Returning Series",
            "original_language": "en",
            "episode_run_time": [50],
            "genres": [{"name": "Drama"}, {"name": "Sci-Fi"}],
            "poster_path": "/abc.jpg",
            "backdrop_path": "/bg.jpg",
            "external_ids": {"imdb_id": "tt4574334", "tvdb_id": 305288}
        });
        let meta = parse_tmdb_response(&data, true).unwrap();
        assert_eq!(meta.genres.len(), 2);
        assert!(meta.external_id("imdb").is_some());
        assert!(meta.external_id("tvdb").is_some());
    }

    #[test]
    fn parse_tmdb_episode_still_url() {
        let ep = parse_tmdb_episode(&json!({
            "episode_number": 1,
            "name": "Pilot",
            "still_path": "/still.jpg"
        }))
        .unwrap();
        assert!(ep.still_url.as_ref().unwrap().contains("still.jpg"));
    }
}
