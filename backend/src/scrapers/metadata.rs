use tracing::debug;

const MIN_TITLE_SIMILARITY: u32 = 40;

/// Result of an external metadata search.
#[derive(Debug, Clone)]
pub struct MetadataMatch {
    pub provider: String,    // "tmdb" or "imdb"
    pub external_id: String, // TMDB numeric ID or IMDb "tt..." ID
    pub title: String,
    pub year: Option<i32>,
    pub poster_url: Option<String>,
}

/// Search for media metadata by title via TMDB (primary) then Cinemeta/IMDb (fallback).
///
/// Returns the best-matching result after applying a year filter (±1 year tolerance).
pub async fn search_by_title(
    http: &reqwest::Client,
    title: &str,
    year: Option<i32>,
    is_series: bool,
    tmdb_api_key: Option<&str>,
) -> Option<MetadataMatch> {
    // Try TMDB first if an API key is available.
    if let Some(key) = tmdb_api_key {
        if let Some(m) = tmdb_search(http, key, title, year, is_series).await {
            return Some(m);
        }
    }

    // Fall back to Cinemeta (IMDb-backed, no key needed).
    cinemeta_search(http, title, year, is_series).await
}

/// Fetch the IMDb external ID for a given TMDB movie/series ID.
pub async fn imdb_id_from_tmdb(
    http: &reqwest::Client,
    tmdb_id: &str,
    is_series: bool,
    api_key: &str,
) -> Option<String> {
    let kind = if is_series { "tv" } else { "movie" };
    let url =
        format!("https://api.themoviedb.org/3/{kind}/{tmdb_id}/external_ids?api_key={api_key}");
    let data: serde_json::Value = http
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
        .map(|s| s.to_string())
}

// ─── Internal helpers ─────────────────────────────────────────────────────────

async fn tmdb_search(
    http: &reqwest::Client,
    api_key: &str,
    title: &str,
    year: Option<i32>,
    is_series: bool,
) -> Option<MetadataMatch> {
    let kind = if is_series { "tv" } else { "movie" };
    let encoded = urlencoding::encode(title);
    let url =
        format!("https://api.themoviedb.org/3/search/{kind}?api_key={api_key}&query={encoded}");

    let data: serde_json::Value = http
        .get(&url)
        .timeout(std::time::Duration::from_secs(10))
        .send()
        .await
        .ok()?
        .json()
        .await
        .ok()?;

    let results = data["results"].as_array()?;

    let best = results.iter().find(|item| {
        let date = item["release_date"]
            .as_str()
            .or_else(|| item["first_air_date"].as_str())
            .unwrap_or("");
        let item_year: Option<i32> = if date.len() >= 4 {
            date[..4].parse().ok()
        } else {
            None
        };
        year_matches(year, item_year)
    })?;

    let title_str = best["title"]
        .as_str()
        .or_else(|| best["name"].as_str())
        .unwrap_or("")
        .to_string();

    let date = best["release_date"]
        .as_str()
        .or_else(|| best["first_air_date"].as_str())
        .unwrap_or("");
    let item_year: Option<i32> = if date.len() >= 4 {
        date[..4].parse().ok()
    } else {
        None
    };

    let external_id = best["id"].as_i64()?.to_string();
    let poster_url = best["poster_path"]
        .as_str()
        .map(|p| format!("https://image.tmdb.org/t/p/w500{p}"));

    let sim = crate::parser::similarity_ratio(title, &title_str);
    if sim < MIN_TITLE_SIMILARITY {
        debug!("metadata: TMDB match '{title_str}' rejected (sim={sim}) for query '{title}'");
        return None;
    }

    debug!("metadata: TMDB match '{title_str}' ({item_year:?}) for query '{title}'");

    Some(MetadataMatch {
        provider: "tmdb".to_string(),
        external_id,
        title: title_str,
        year: item_year,
        poster_url,
    })
}

async fn cinemeta_search(
    http: &reqwest::Client,
    title: &str,
    year: Option<i32>,
    is_series: bool,
) -> Option<MetadataMatch> {
    let kind = if is_series { "series" } else { "movie" };
    let encoded = urlencoding::encode(title);
    let url = format!("https://v3-cinemeta.strem.io/catalog/{kind}/top/search={encoded}.json");

    let data: serde_json::Value = http
        .get(&url)
        .timeout(std::time::Duration::from_secs(10))
        .send()
        .await
        .ok()?
        .json()
        .await
        .ok()?;

    let metas = data["metas"].as_array()?;

    let best = metas.iter().find(|m| {
        let item_year = m["year"]
            .as_str()
            .and_then(|y| y.split('-').next()?.parse::<i32>().ok())
            .or_else(|| m["year"].as_i64().map(|y| y as i32));
        year_matches(year, item_year)
    })?;

    let title_str = best["name"].as_str().unwrap_or("").to_string();
    let external_id = best["id"].as_str()?.to_string();
    let item_year = best["year"]
        .as_str()
        .and_then(|y| y.split('-').next()?.parse::<i32>().ok())
        .or_else(|| best["year"].as_i64().map(|y| y as i32));
    let poster_url = best["poster"].as_str().map(|s| s.to_string());

    let sim = crate::parser::similarity_ratio(title, &title_str);
    if sim < MIN_TITLE_SIMILARITY {
        debug!("metadata: Cinemeta match '{title_str}' rejected (sim={sim}) for query '{title}'");
        return None;
    }

    debug!("metadata: Cinemeta match '{title_str}' ({item_year:?}) for query '{title}'");

    Some(MetadataMatch {
        provider: "imdb".to_string(),
        external_id,
        title: title_str,
        year: item_year,
        poster_url,
    })
}

/// Returns true if the item year matches the query year within ±1 tolerance,
/// or if either year is unknown.
fn year_matches(query: Option<i32>, item: Option<i32>) -> bool {
    match (query, item) {
        (Some(q), Some(i)) => (q - i).abs() <= 1,
        _ => true,
    }
}

// ─── Direct external-ID lookup ────────────────────────────────────────────────

/// Rich metadata returned when fetching by a known external ID.
#[derive(Debug, Clone)]
pub struct TmdbDetails {
    pub title: String,
    pub year: Option<i32>,
    pub description: Option<String>,
    pub poster_url: Option<String>,
    pub imdb_id: Option<String>,
    pub tmdb_id: Option<String>,
    pub is_series: bool,
}

/// Fetch full metadata from TMDB by a known external ID.
///
/// - `provider = "tmdb"` → direct `/3/movie/{id}` or `/3/tv/{id}` call
/// - `provider = "imdb"` → TMDB `/3/find/{tt-id}?external_source=imdb_id`, then full details
/// - `provider = "tvdb"` → TMDB `/3/find/{id}?external_source=tvdb_id`, then full details
///
/// Returns `None` when the API key is absent, the ID is not found, or any request fails.
pub async fn fetch_by_external_id(
    http: &reqwest::Client,
    provider: &str,
    external_id: &str,
    is_series: bool,
    tmdb_api_key: Option<&str>,
) -> Option<TmdbDetails> {
    let api_key = tmdb_api_key?;
    match provider {
        "tmdb" => {
            let kind = if is_series { "tv" } else { "movie" };
            let url =
                format!("https://api.themoviedb.org/3/{kind}/{external_id}?api_key={api_key}");
            let data: serde_json::Value = http
                .get(&url)
                .timeout(std::time::Duration::from_secs(10))
                .send()
                .await
                .ok()?
                .json()
                .await
                .ok()?;
            parse_tmdb_details(&data, is_series)
        }
        "imdb" => {
            let url = format!(
                "https://api.themoviedb.org/3/find/{external_id}?api_key={api_key}&external_source=imdb_id"
            );
            let data: serde_json::Value = http
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
            let kind = if is_series { "tv" } else { "movie" };
            let detail_url =
                format!("https://api.themoviedb.org/3/{kind}/{tmdb_id}?api_key={api_key}");
            let detail: serde_json::Value = http
                .get(&detail_url)
                .timeout(std::time::Duration::from_secs(10))
                .send()
                .await
                .ok()?
                .json()
                .await
                .ok()?;
            let mut result = parse_tmdb_details(&detail, is_series)?;
            // Preserve the original IMDb ID the caller passed in.
            if result.imdb_id.is_none() {
                result.imdb_id = Some(external_id.to_string());
            }
            Some(result)
        }
        "tvdb" => {
            let url = format!(
                "https://api.themoviedb.org/3/find/{external_id}?api_key={api_key}&external_source=tvdb_id"
            );
            let data: serde_json::Value = http
                .get(&url)
                .timeout(std::time::Duration::from_secs(10))
                .send()
                .await
                .ok()?
                .json()
                .await
                .ok()?;
            let results = data["tv_results"].as_array()?;
            let tmdb_id = results.first()?["id"].as_i64()?.to_string();
            let detail_url = format!("https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={api_key}");
            let detail: serde_json::Value = http
                .get(&detail_url)
                .timeout(std::time::Duration::from_secs(10))
                .send()
                .await
                .ok()?
                .json()
                .await
                .ok()?;
            parse_tmdb_details(&detail, true)
        }
        _ => None,
    }
}

fn parse_tmdb_details(data: &serde_json::Value, is_series: bool) -> Option<TmdbDetails> {
    let title = data["title"]
        .as_str()
        .or_else(|| data["name"].as_str())?
        .to_string();
    let date = data["release_date"]
        .as_str()
        .or_else(|| data["first_air_date"].as_str())
        .unwrap_or("");
    let year: Option<i32> = if date.len() >= 4 {
        date[..4].parse().ok()
    } else {
        None
    };
    let description = data["overview"]
        .as_str()
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string());
    let poster_url = data["poster_path"]
        .as_str()
        .map(|p| format!("https://image.tmdb.org/t/p/w500{p}"));
    let imdb_id = data["imdb_id"]
        .as_str()
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string());
    let tmdb_id = data["id"].as_i64().map(|id| id.to_string());
    Some(TmdbDetails {
        title,
        year,
        description,
        poster_url,
        imdb_id,
        tmdb_id,
        is_series,
    })
}
