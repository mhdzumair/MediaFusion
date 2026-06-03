mod anilist;
mod imdb;
mod kitsu;
mod mdblist;
mod tmdb;
mod trakt;
mod tvdb;

use std::collections::HashSet;

use serde_json::json;
use sqlx::PgPool;

pub use crate::db::{NormalizedEpisode, NormalizedMetadata, NormalizedSeason};
pub use mdblist::{fetch_all_list_imdb_ids, ingest_list};
pub use trakt::resolve_or_store_media;

pub(crate) const MIN_TITLE_SIMILARITY: u32 = 40;
const IMPORT_SEARCH_LIMIT: usize = 10;

/// Shared credentials/context for metadata provider calls.
#[derive(Clone, Copy, Default)]
pub struct FetchCtx<'a> {
    pub tmdb_api_key: Option<&'a str>,
    pub tvdb_api_key: Option<&'a str>,
    pub mdblist_api_key: Option<&'a str>,
    pub trakt_client_id: Option<&'a str>,
    pub trakt_client_secret: Option<&'a str>,
    pub cinemeta_fallback: bool,
}

/// Back-compat alias for existing callers.
pub type ExternalFetchOpts<'a> = FetchCtx<'a>;

impl<'a> FetchCtx<'a> {
    pub fn with_tmdb_tvdb(
        tmdb_api_key: Option<&'a str>,
        tvdb_api_key: Option<&'a str>,
        cinemeta_fallback: bool,
    ) -> Self {
        Self {
            tmdb_api_key,
            tvdb_api_key,
            mdblist_api_key: None,
            trakt_client_id: None,
            trakt_client_secret: None,
            cinemeta_fallback,
        }
    }
}

/// Result of an external metadata search.
#[derive(Debug, Clone)]
pub struct MetadataMatch {
    pub provider: String,
    pub external_id: String,
    pub title: String,
    pub year: Option<i32>,
    pub poster_url: Option<String>,
}

/// Flat metadata shim for callers not yet migrated to [`NormalizedMetadata`].
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

impl From<&NormalizedMetadata> for TmdbDetails {
    fn from(meta: &NormalizedMetadata) -> Self {
        tmdb::to_legacy_details(meta)
    }
}

/// Parse an import `meta_id` (e.g. `tt123`, `tmdb:603`) into provider + external id.
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

/// Fetch rich normalized metadata from a provider by external id.
pub async fn fetch_normalized(
    http: &reqwest::Client,
    ctx: &FetchCtx<'_>,
    provider: &str,
    external_id: &str,
    is_series: bool,
) -> Option<NormalizedMetadata> {
    match provider {
        "tmdb" => tmdb::fetch_by_id(http, ctx, external_id, is_series).await,
        "imdb" => imdb::fetch_by_id(http, ctx, external_id, is_series).await,
        "tvdb" => {
            if let Some(key) = ctx.tvdb_api_key {
                if let Some(meta) = tvdb::fetch_by_id(http, key, external_id, is_series).await {
                    return Some(meta);
                }
            }
            tmdb::find_by_external(http, ctx, "tvdb_id", external_id, is_series).await
        }
        "mal" => anilist::fetch_by_mal_id(http, external_id).await,
        "anilist" => anilist::fetch_by_id(http, external_id).await,
        "kitsu" => kitsu::fetch_by_id(http, external_id).await,
        _ => None,
    }
}

/// Fetch flat metadata (back-compat shim wrapping [`fetch_normalized`]).
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
        FetchCtx::with_tmdb_tvdb(tmdb_api_key, None, true),
    )
    .await
}

pub async fn fetch_by_external_id_with_opts(
    http: &reqwest::Client,
    provider: &str,
    external_id: &str,
    is_series: bool,
    opts: FetchCtx<'_>,
) -> Option<TmdbDetails> {
    fetch_normalized(http, &opts, provider, external_id, is_series)
        .await
        .map(|m| TmdbDetails::from(&m))
}

/// Build a torrent-import match payload from normalized metadata.
pub fn import_match_from_normalized(
    meta: &NormalizedMetadata,
    media_type: &str,
) -> serde_json::Value {
    let imdb_id = meta.external_id("imdb").map(str::to_string);
    let tmdb_id = meta.external_id("tmdb").map(str::to_string);
    let primary_id = imdb_id
        .clone()
        .or_else(|| tmdb_id.as_ref().map(|t| format!("tmdb:{t}")))
        .unwrap_or_default();
    json!({
        "id": primary_id,
        "imdb_id": imdb_id,
        "tmdb_id": tmdb_id,
        "title": meta.title,
        "year": meta.year,
        "poster": meta.poster_url,
        "background": meta.backdrop_url,
        "release_date": meta.release_date,
        "description": meta.description,
        "type": media_type,
    })
}

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

pub async fn imdb_id_from_tmdb(
    http: &reqwest::Client,
    tmdb_id: &str,
    is_series: bool,
    api_key: &str,
) -> Option<String> {
    tmdb::imdb_id_from_tmdb(http, tmdb_id, is_series, api_key).await
}

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
    let ctx = FetchCtx {
        tmdb_api_key,
        tvdb_api_key: None,
        mdblist_api_key: None,
        trakt_client_id: None,
        trakt_client_secret: None,
        cinemeta_fallback: cinemeta_fallback_enabled,
    };
    let imdb_first = metadata_primary_source.eq_ignore_ascii_case("imdb");

    if imdb_first {
        if cinemeta_fallback_enabled {
            if let Some(m) = imdb::search_single(http, title, year, is_series).await {
                return Some(m);
            }
        }
        if let Some(m) = tmdb::search_single(http, &ctx, title, year, is_series).await {
            return Some(m);
        }
    } else {
        if let Some(m) = tmdb::search_single(http, &ctx, title, year, is_series).await {
            return Some(m);
        }
        if cinemeta_fallback_enabled {
            if let Some(m) = imdb::search_single(http, title, year, is_series).await {
                return Some(m);
            }
        }
    }

    if is_series {
        for provider in anime_source_order {
            let results = match provider.as_str() {
                "kitsu" => kitsu::search(http, title, 3).await,
                "anilist" => anilist::search(http, title, 3).await,
                _ => continue,
            };
            if let Some(entry) = results.into_iter().next() {
                return Some(entry);
            }
        }
    }

    None
}

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
    let ctx = FetchCtx {
        tmdb_api_key,
        tvdb_api_key,
        mdblist_api_key: None,
        trakt_client_id: None,
        trakt_client_secret: None,
        cinemeta_fallback: cinemeta_fallback_enabled,
    };
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
        for m in imdb::search(http, title, year, is_series, IMPORT_SEARCH_LIMIT).await {
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
        for entry in
            tvdb::search_import_tvdb(http, tvdb_key, title, media_type, IMPORT_SEARCH_LIMIT).await
        {
            push_match(&mut results, &mut seen, entry);
            if results.len() >= IMPORT_SEARCH_LIMIT {
                return results;
            }
        }
    }

    if let Some(_key) = tmdb_api_key {
        for m in tmdb::search(http, &ctx, title, year, is_series, IMPORT_SEARCH_LIMIT).await {
            let entry = if let Some(meta) =
                fetch_normalized(http, &ctx, "tmdb", &m.external_id, is_series).await
            {
                import_match_from_normalized(&meta, media_type)
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
        for entry in anilist::search_import_anilist(http, title, IMPORT_SEARCH_LIMIT).await {
            push_match(&mut results, &mut seen, entry);
            if results.len() >= IMPORT_SEARCH_LIMIT {
                return results;
            }
        }
        for entry in kitsu::search_import_kitsu(http, title, IMPORT_SEARCH_LIMIT).await {
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
    let Some(_) = crate::db::MediaType::from_wire(meta_type) else {
        return vec![];
    };

    let media_type = if meta_type == "series" {
        "series"
    } else {
        "movie"
    };

    let candidates = crate::db::search_media_candidates(pool, media_type, title).await;
    let mut results = Vec::with_capacity(candidates.len().min(limit));

    for candidate in candidates {
        if results.len() >= limit {
            break;
        }
        if candidate.imdb_id.is_none() && candidate.tmdb_id.is_none() && candidate.tvdb_id.is_none()
        {
            continue;
        }

        let id = candidate.media_id.0;
        let external_ids = load_media_external_ids(pool, id).await;
        let imdb_id = external_ids.get("imdb").cloned().or(candidate.imdb_id);
        let tmdb_id = external_ids.get("tmdb").cloned().or(candidate.tmdb_id);
        let tvdb_id = external_ids.get("tvdb").cloned().or(candidate.tvdb_id);
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
            "title": candidate.title,
            "year": candidate.year,
            "end_year": candidate.end_year,
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

/// Multi-provider metadata refresh — fetches all linked providers, merges, then stores.
pub async fn refresh_media_from_providers(
    pool: &PgPool,
    http: &reqwest::Client,
    media_id: i32,
    media_type: &str,
    opts: FetchCtx<'_>,
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
    let mut fetched = Vec::new();
    let mut refreshed = Vec::new();

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
        if let Some(meta) = fetch_normalized(http, &opts, provider, &external_id, is_series).await {
            refreshed.push(provider.to_string());
            fetched.push(meta);
        }
    }

    if fetched.is_empty() {
        let _ = sqlx::query("UPDATE media SET last_scraped_at = NOW() WHERE id = $1")
            .bind(media_id)
            .execute(pool)
            .await;
        return (
            vec![],
            "Could not fetch fresh metadata from any provider.".to_string(),
        );
    }

    let merged = match crate::db::merge_normalized(fetched) {
        Some(m) => m,
        None => {
            return (
                vec![],
                "Could not fetch fresh metadata from any provider.".to_string(),
            );
        }
    };
    let store_opts = crate::db::StoreMediaOpts::refresh(crate::db::MediaId(media_id));
    if crate::db::store_media(pool, &merged, store_opts)
        .await
        .is_err()
    {
        return (
            vec![],
            "Could not fetch fresh metadata from any provider.".to_string(),
        );
    }

    let message = format!(
        "Successfully refreshed metadata from: {}",
        refreshed.join(", ")
    );
    (refreshed, message)
}

pub async fn search_external_for_provider(
    http: &reqwest::Client,
    pool: &PgPool,
    provider: &str,
    title: &str,
    year: Option<i32>,
    media_type: &str,
    tmdb_api_key: Option<&str>,
    tvdb_api_key: Option<&str>,
    cinemeta_fallback_enabled: bool,
) -> Vec<serde_json::Value> {
    let all = search_import_matches(
        http,
        pool,
        title,
        year,
        media_type,
        tmdb_api_key,
        tvdb_api_key,
        cinemeta_fallback_enabled,
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

pub(crate) fn year_matches(query: Option<i32>, item: Option<i32>) -> bool {
    match (query, item) {
        (Some(q), Some(i)) => (q - i).abs() <= 1,
        _ => true,
    }
}

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

    #[test]
    fn parses_bare_tmdb_numeric_id() {
        assert_eq!(
            parse_import_meta_id("603"),
            Some(("tmdb", "603".to_string()))
        );
    }
}
