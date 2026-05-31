use std::collections::HashSet;

use serde_json::json;
use sqlx::PgPool;
use tracing::debug;

const MIN_TITLE_SIMILARITY: u32 = 40;
const IMPORT_SEARCH_LIMIT: usize = 10;

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
    cinemeta_fallback_enabled: bool,
) -> Option<MetadataMatch> {
    search_by_title_with_anime(
        http,
        title,
        year,
        is_series,
        tmdb_api_key,
        cinemeta_fallback_enabled,
        &[],
    )
    .await
}

pub async fn search_by_title_with_anime(
    http: &reqwest::Client,
    title: &str,
    year: Option<i32>,
    is_series: bool,
    tmdb_api_key: Option<&str>,
    cinemeta_fallback_enabled: bool,
    anime_source_order: &[String],
) -> Option<MetadataMatch> {
    search_by_title_with_anime_primary(
        http,
        title,
        year,
        is_series,
        tmdb_api_key,
        cinemeta_fallback_enabled,
        anime_source_order,
        "tmdb",
    )
    .await
}

/// Title search with configurable primary provider (`METADATA_PRIMARY_SOURCE`).
pub async fn search_by_title_with_anime_primary(
    http: &reqwest::Client,
    title: &str,
    year: Option<i32>,
    is_series: bool,
    tmdb_api_key: Option<&str>,
    cinemeta_fallback_enabled: bool,
    anime_source_order: &[String],
    metadata_primary_source: &str,
) -> Option<MetadataMatch> {
    let imdb_first = metadata_primary_source.eq_ignore_ascii_case("imdb");

    if imdb_first {
        if cinemeta_fallback_enabled {
            if let Some(m) = cinemeta_search(http, title, year, is_series).await {
                return Some(m);
            }
        }
        if let Some(key) = tmdb_api_key {
            if let Some(m) = tmdb_search(http, key, title, year, is_series).await {
                return Some(m);
            }
        }
    } else {
        if let Some(key) = tmdb_api_key {
            if let Some(m) = tmdb_search(http, key, title, year, is_series).await {
                return Some(m);
            }
        }
        if cinemeta_fallback_enabled {
            if let Some(m) = cinemeta_search(http, title, year, is_series).await {
                return Some(m);
            }
        }
    }

    if is_series {
        for provider in anime_source_order {
            let results = match provider.as_str() {
                "kitsu" => crate::scrapers::kitsu::search_import_kitsu(http, title, 3).await,
                "anilist" => crate::scrapers::anilist::search_import_anilist(http, title, 3).await,
                _ => continue,
            };
            if let Some(entry) = results.into_iter().next() {
                let id = entry["id"].as_str().unwrap_or("").to_string();
                if let Some((prov, ext_id)) = parse_import_meta_id(&id) {
                    return Some(MetadataMatch {
                        provider: prov.to_string(),
                        external_id: ext_id,
                        title: entry["title"].as_str().unwrap_or(title).to_string(),
                        year: entry["year"].as_i64().map(|y| y as i32),
                        poster_url: entry["poster"].as_str().map(str::to_string),
                    });
                }
            }
        }
    }

    None
}

/// Search local DB and external providers (Cinemeta, TMDB) for import UI matches.
///
/// Mirrors Python `meta_fetcher.search_multiple_results` used by magnet/torrent analyze.
pub async fn search_import_matches(
    http: &reqwest::Client,
    pool: &PgPool,
    title: &str,
    year: Option<i32>,
    meta_type: &str,
    tmdb_api_key: Option<&str>,
    tvdb_api_key: Option<&str>,
    cinemeta_fallback_enabled: bool,
) -> Vec<serde_json::Value> {
    if meta_type == "sports" {
        return search_import_db_matches(pool, title, meta_type, IMPORT_SEARCH_LIMIT).await;
    }

    let is_series = meta_type == "series";
    let media_type = if is_series { "series" } else { "movie" };
    let mut seen: HashSet<String> = HashSet::new();
    let mut results: Vec<serde_json::Value> = Vec::new();

    let push_match = |results: &mut Vec<serde_json::Value>,
                      seen: &mut HashSet<String>,
                      entry: serde_json::Value| {
        let dedup_key = entry["imdb_id"]
            .as_str()
            .map(|id| format!("imdb:{id}"))
            .or_else(|| entry["tvdb_id"].as_str().map(|id| format!("tvdb:{id}")))
            .or_else(|| entry["tmdb_id"].as_str().map(|id| format!("tmdb:{id}")))
            .or_else(|| entry["id"].as_str().map(|id| format!("primary:{id}")));
        if let Some(key) = dedup_key {
            if seen.insert(key) {
                results.push(entry);
            }
        }
    };

    for entry in search_import_db_matches(pool, title, meta_type, IMPORT_SEARCH_LIMIT).await {
        push_match(&mut results, &mut seen, entry);
        if results.len() >= IMPORT_SEARCH_LIMIT {
            return results;
        }
    }

    if cinemeta_fallback_enabled {
        for m in cinemeta_search_multiple(http, title, year, is_series, IMPORT_SEARCH_LIMIT).await {
            let imdb_id = m.external_id;
            push_match(
                &mut results,
                &mut seen,
                json!({
                    "id": imdb_id,
                    "imdb_id": imdb_id,
                    "title": m.title,
                    "year": m.year,
                    "poster": m.poster_url,
                    "type": media_type,
                }),
            );
            if results.len() >= IMPORT_SEARCH_LIMIT {
                return results;
            }
        }
    }

    if let Some(tvdb_key) = tvdb_api_key {
        for entry in crate::scrapers::tvdb::search_import_tvdb(
            http,
            tvdb_key,
            title,
            media_type,
            IMPORT_SEARCH_LIMIT,
        )
        .await
        {
            push_match(&mut results, &mut seen, entry);
            if results.len() >= IMPORT_SEARCH_LIMIT {
                return results;
            }
        }
    }

    if let Some(key) = tmdb_api_key {
        for m in tmdb_search_multiple(http, key, title, year, is_series, IMPORT_SEARCH_LIMIT).await
        {
            let entry = if let Some(details) = fetch_by_external_id_with_opts(
                http,
                "tmdb",
                &m.external_id,
                is_series,
                ExternalFetchOpts {
                    tmdb_api_key: Some(key),
                    tvdb_api_key: None,
                    cinemeta_fallback: true,
                },
            )
            .await
            {
                import_match_from_details(&details, media_type)
            } else {
                json!({
                    "id": format!("tmdb:{}", m.external_id),
                    "tmdb_id": m.external_id,
                    "title": m.title,
                    "year": m.year,
                    "poster": m.poster_url,
                    "type": media_type,
                })
            };
            push_match(&mut results, &mut seen, entry);
            if results.len() >= IMPORT_SEARCH_LIMIT {
                return results;
            }
        }
    }

    if meta_type == "series" {
        for entry in
            crate::scrapers::anilist::search_import_anilist(http, title, IMPORT_SEARCH_LIMIT).await
        {
            push_match(&mut results, &mut seen, entry);
            if results.len() >= IMPORT_SEARCH_LIMIT {
                return results;
            }
        }
        for entry in
            crate::scrapers::kitsu::search_import_kitsu(http, title, IMPORT_SEARCH_LIMIT).await
        {
            push_match(&mut results, &mut seen, entry);
            if results.len() >= IMPORT_SEARCH_LIMIT {
                return results;
            }
        }
    }

    results
}

async fn search_import_db_matches(
    pool: &PgPool,
    title: &str,
    meta_type: &str,
    limit: usize,
) -> Vec<serde_json::Value> {
    let pattern = format!("%{title}%");
    let Some(media_type) = crate::db::MediaType::from_wire(meta_type) else {
        return vec![];
    };
    let rows: Vec<(i32, String, Option<i32>)> = sqlx::query_as(
        r#"SELECT m.id, m.title, m.year
           FROM media m
           WHERE LOWER(m.title) LIKE LOWER($1)
             AND m.type = $2
           LIMIT $3"#,
    )
    .bind(&pattern)
    .bind(media_type)
    .bind(limit as i64)
    .fetch_all(pool)
    .await
    .unwrap_or_default();

    let media_type = if meta_type == "series" {
        "series"
    } else {
        "movie"
    };

    let mut results = Vec::with_capacity(rows.len());
    for (id, title, year) in rows {
        let external_ids = load_media_external_ids(pool, id).await;
        let imdb_id = external_ids.get("imdb").cloned();
        let tmdb_id = external_ids.get("tmdb").cloned();
        let tvdb_id = external_ids.get("tvdb").cloned();
        let mal_id = external_ids.get("mal").cloned();
        let kitsu_id = external_ids.get("kitsu").cloned();
        let primary_id = imdb_id
            .clone()
            .or_else(|| tmdb_id.as_ref().map(|t| format!("tmdb:{t}")))
            .or_else(|| tvdb_id.as_ref().map(|t| format!("tvdb:{t}")))
            .or_else(|| mal_id.as_ref().map(|t| format!("mal:{t}")))
            .unwrap_or_else(|| format!("media:{id}"));
        results.push(json!({
            "id": primary_id,
            "imdb_id": imdb_id,
            "tmdb_id": tmdb_id,
            "tvdb_id": tvdb_id,
            "mal_id": mal_id,
            "kitsu_id": kitsu_id,
            "media_id": id,
            "title": title,
            "year": year,
            "type": media_type,
        }));
    }
    results
}

async fn load_media_external_ids(
    pool: &PgPool,
    media_id: i32,
) -> std::collections::HashMap<String, String> {
    let rows: Vec<(String, String)> =
        sqlx::query_as("SELECT provider, external_id FROM media_external_id WHERE media_id = $1")
            .bind(media_id)
            .fetch_all(pool)
            .await
            .unwrap_or_default();

    rows.into_iter().collect()
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

async fn tmdb_search_multiple(
    http: &reqwest::Client,
    api_key: &str,
    title: &str,
    year: Option<i32>,
    is_series: bool,
    limit: usize,
) -> Vec<MetadataMatch> {
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
    let Ok(data) = resp.json::<serde_json::Value>().await else {
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
            .map(|p| format!("https://image.tmdb.org/t/p/w500{p}"));

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

async fn cinemeta_search_multiple(
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
    let Ok(data) = resp.json::<serde_json::Value>().await else {
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
        let poster_url = m["poster"].as_str().map(|s| s.to_string());

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
    pub backdrop_url: Option<String>,
    pub release_date: Option<String>,
    pub imdb_id: Option<String>,
    pub tmdb_id: Option<String>,
    pub is_series: bool,
}

/// Parse an import `meta_id` (e.g. `tt123`, `tmdb:603`) into provider + external id.
#[cfg(test)]
mod parse_tests {
    use super::parse_import_meta_id;

    #[test]
    fn parses_anime_provider_prefixes() {
        assert_eq!(
            parse_import_meta_id("mal:12345"),
            Some(("mal", "12345".to_string()))
        );
        assert_eq!(
            parse_import_meta_id("kitsu:42"),
            Some(("kitsu", "42".to_string()))
        );
        assert_eq!(
            parse_import_meta_id("anilist:99"),
            Some(("anilist", "99".to_string()))
        );
    }
}

pub fn parse_import_meta_id(meta_id: &str) -> Option<(&'static str, String)> {
    let meta_id = meta_id.trim();
    if meta_id.is_empty() {
        return None;
    }
    if meta_id.starts_with("tt") {
        return Some(("imdb", meta_id.to_string()));
    }
    if let Some(id) = meta_id.strip_prefix("tmdb:") {
        return Some(("tmdb", id.to_string()));
    }
    if let Some(id) = meta_id.strip_prefix("tvdb:") {
        return Some(("tvdb", id.to_string()));
    }
    if let Some(id) = meta_id.strip_prefix("mal:") {
        return Some(("mal", id.to_string()));
    }
    if let Some(id) = meta_id.strip_prefix("kitsu:") {
        return Some(("kitsu", id.to_string()));
    }
    if let Some(id) = meta_id.strip_prefix("anilist:") {
        return Some(("anilist", id.to_string()));
    }
    if meta_id.chars().all(|c| c.is_ascii_digit()) {
        return Some(("tmdb", meta_id.to_string()));
    }
    None
}

/// Build a torrent-import match payload from TMDB (or similar) details.
pub fn import_match_from_details(details: &TmdbDetails, media_type: &str) -> serde_json::Value {
    let primary_id = details
        .imdb_id
        .clone()
        .unwrap_or_else(|| format!("tmdb:{}", details.tmdb_id.as_deref().unwrap_or_default()));
    json!({
        "id": primary_id,
        "imdb_id": details.imdb_id,
        "tmdb_id": details.tmdb_id,
        "title": details.title,
        "year": details.year,
        "poster": details.poster_url,
        "background": details.backdrop_url,
        "release_date": details.release_date,
        "description": details.description,
        "type": media_type,
    })
}

/// Options for [`fetch_by_external_id`].
#[derive(Clone, Copy, Default)]
pub struct ExternalFetchOpts<'a> {
    pub tmdb_api_key: Option<&'a str>,
    pub tvdb_api_key: Option<&'a str>,
    pub cinemeta_fallback: bool,
}

/// Fetch full metadata from external providers (TMDB, TVDB, Cinemeta, MAL/Kitsu/AniList).
pub async fn fetch_by_external_id(
    http: &reqwest::Client,
    provider: &str,
    external_id: &str,
    is_series: bool,
    tmdb_api_key: Option<&str>,
) -> Option<TmdbDetails> {
    fetch_by_external_id_with_opts(
        http,
        provider,
        external_id,
        is_series,
        ExternalFetchOpts {
            tmdb_api_key,
            tvdb_api_key: None,
            cinemeta_fallback: true,
        },
    )
    .await
}

pub async fn fetch_by_external_id_with_opts(
    http: &reqwest::Client,
    provider: &str,
    external_id: &str,
    is_series: bool,
    opts: ExternalFetchOpts<'_>,
) -> Option<TmdbDetails> {
    match provider {
        "tmdb" => {
            let api_key = opts.tmdb_api_key?;
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
            if let Some(api_key) = opts.tmdb_api_key {
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
                return Some(result);
            }
            if opts.cinemeta_fallback {
                return cinemeta_fetch_details(http, external_id, is_series).await;
            }
            None
        }
        "tvdb" => {
            if let Some(tvdb_key) = opts.tvdb_api_key {
                if let Some(d) = crate::scrapers::tvdb::fetch_tvdb_details(
                    http,
                    tvdb_key,
                    external_id,
                    is_series,
                )
                .await
                {
                    return Some(d);
                }
            }
            let api_key = opts.tmdb_api_key?;
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
            parse_tmdb_details(&detail, is_series)
        }
        "mal" => crate::scrapers::anilist::fetch_anime_by_mal_id(http, external_id).await,
        "anilist" => crate::scrapers::anilist::fetch_anilist_by_id(http, external_id).await,
        "kitsu" => crate::scrapers::kitsu::fetch_kitsu_by_id(http, external_id).await,
        _ => None,
    }
}

async fn cinemeta_fetch_details(
    http: &reqwest::Client,
    imdb_id: &str,
    is_series: bool,
) -> Option<TmdbDetails> {
    let kind = if is_series { "series" } else { "movie" };
    let url = format!("https://v3-cinemeta.strem.io/meta/{kind}/{imdb_id}.json");
    let data: serde_json::Value = http
        .get(&url)
        .timeout(std::time::Duration::from_secs(10))
        .send()
        .await
        .ok()?
        .json()
        .await
        .ok()?;
    let meta = &data["meta"];
    let title = meta["name"].as_str()?.to_string();
    let year = meta["year"]
        .as_str()
        .and_then(|y| y.split('-').next()?.parse().ok())
        .or_else(|| meta["year"].as_i64().map(|y| y as i32));
    let description = meta["description"].as_str().map(str::to_string);
    let poster_url = meta["poster"].as_str().map(str::to_string);
    let release_date = meta["released"].as_str().map(str::to_string);
    Some(TmdbDetails {
        title,
        year,
        description,
        poster_url,
        backdrop_url: None,
        release_date,
        imdb_id: Some(imdb_id.to_string()),
        tmdb_id: None,
        is_series,
    })
}

/// Multi-provider metadata refresh (Python `refresh_metadata` / `get_metadata_from_all_providers`).
pub async fn refresh_media_from_providers(
    pool: &PgPool,
    http: &reqwest::Client,
    media_id: i32,
    media_type: &str,
    opts: ExternalFetchOpts<'_>,
    provider_filter: Option<&[String]>,
) -> (Vec<String>, String) {
    let is_series = media_type == "series";
    let ext_rows: Vec<(String, String)> =
        sqlx::query_as("SELECT provider, external_id FROM media_external_id WHERE media_id = $1")
            .bind(media_id)
            .fetch_all(pool)
            .await
            .unwrap_or_default();

    if ext_rows.is_empty() {
        return (vec![], "No external IDs linked to this media.".to_string());
    }

    let priority = ["imdb", "mal", "kitsu", "anilist", "tmdb", "tvdb"];
    let mut refreshed = Vec::new();
    let mut applied = false;

    for provider in priority {
        if let Some(filter) = provider_filter {
            if !filter.iter().any(|p| p == provider) {
                continue;
            }
        }
        let external_id = match ext_rows.iter().find(|(p, _)| p == provider) {
            Some((_, id)) => id.clone(),
            None => continue,
        };
        if let Some(details) =
            fetch_by_external_id_with_opts(http, provider, &external_id, is_series, opts).await
        {
            let _ = sqlx::query(
                "UPDATE media SET title = $2, year = COALESCE($3, year), description = COALESCE($4, description), last_scraped_at = NOW(), updated_at = NOW() WHERE id = $1",
            )
            .bind(media_id)
            .bind(&details.title)
            .bind(details.year)
            .bind(&details.description)
            .execute(pool)
            .await;
            refreshed.push(provider.to_string());
            applied = true;
            break;
        }
    }

    if !applied {
        let _ = sqlx::query("UPDATE media SET last_scraped_at = NOW() WHERE id = $1")
            .bind(media_id)
            .execute(pool)
            .await;
    }

    let message = if refreshed.is_empty() {
        "Could not fetch fresh metadata from any provider.".to_string()
    } else {
        format!(
            "Successfully refreshed metadata from: {}",
            refreshed.join(", ")
        )
    };

    (refreshed, message)
}

/// External search for moderator/import UIs using the same provider chain as analyze.
pub async fn search_external_for_provider(
    http: &reqwest::Client,
    pool: &PgPool,
    provider: &str,
    title: &str,
    year: Option<i32>,
    media_type: &str,
    tmdb_api_key: Option<&str>,
    tvdb_api_key: Option<&str>,
    cinemeta_fallback: bool,
) -> Vec<serde_json::Value> {
    let all = search_import_matches(
        http,
        pool,
        title,
        year,
        media_type,
        tmdb_api_key,
        tvdb_api_key,
        cinemeta_fallback,
    )
    .await;

    if provider == "all" || provider.is_empty() {
        return all;
    }

    all.into_iter()
        .filter(|entry| match provider {
            "tmdb" => entry.get("tmdb_id").is_some(),
            "tvdb" => entry.get("tvdb_id").is_some(),
            "imdb" => entry.get("imdb_id").is_some(),
            "mal" => entry.get("mal_id").is_some(),
            "kitsu" => entry.get("kitsu_id").is_some(),
            "anilist" => entry["id"]
                .as_str()
                .is_some_and(|id| id.starts_with("anilist:")),
            _ => true,
        })
        .collect()
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
    let backdrop_url = data["backdrop_path"]
        .as_str()
        .map(|p| format!("https://image.tmdb.org/t/p/original{p}"));
    let release_date = if date.is_empty() {
        None
    } else {
        Some(date.to_string())
    };
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
        backdrop_url,
        release_date,
        imdb_id,
        tmdb_id,
        is_series,
    })
}
