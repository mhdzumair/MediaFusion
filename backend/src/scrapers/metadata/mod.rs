mod anilist;
mod imdb;
mod keys;
mod kitsu;
mod match_search;
mod mdblist;
mod tmdb;
mod trakt;
mod tvdb;

use serde_json::json;
use sqlx::PgPool;

pub use crate::db::{NormalizedEpisode, NormalizedMetadata, NormalizedSeason};
pub use keys::{MetadataServerKeys, ResolvedMetadataKeys, resolve_metadata_keys};
pub use match_search::{MediaMatchSearchOptions, search_media_matches};
pub use mdblist::{fetch_all_list_imdb_ids, ingest_list};
pub use trakt::resolve_or_store_media;

pub(crate) const IMPORT_SEARCH_LIMIT: usize = 10;
pub(crate) const MIN_TITLE_SIMILARITY: u32 = 40;

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
    search_media_matches(
        http,
        pool,
        MediaMatchSearchOptions {
            title: Some(title),
            year,
            external_id: None,
            media_type: meta_type,
            limit: IMPORT_SEARCH_LIMIT,
            user_id: None,
            include_user_content: false,
            include_official: true,
            include_catalog: true,
            include_external: true,
            tmdb_api_key,
            tvdb_api_key,
            cinemeta_fallback_enabled,
        },
    )
    .await
}

#[derive(sqlx::FromRow)]
struct ImportDbMatchDetails {
    media_id: i32,
    description: Option<String>,
    release_date: Option<chrono::NaiveDate>,
    runtime_minutes: Option<i32>,
    poster: Option<String>,
    background: Option<String>,
    logo: Option<String>,
    imdb_rating: Option<f64>,
}

async fn load_import_db_match_details(
    pool: &PgPool,
    media_ids: &[i32],
) -> std::collections::HashMap<i32, ImportDbMatchDetails> {
    if media_ids.is_empty() {
        return std::collections::HashMap::new();
    }

    let rows: Vec<ImportDbMatchDetails> = sqlx::query_as(
        r#"
        SELECT
            m.id AS media_id,
            m.description,
            m.release_date,
            m.runtime_minutes,
            mi_poster.url AS poster,
            mi_bg.url AS background,
            mi_logo.url AS logo,
            mr.rating AS imdb_rating
        FROM media m
        LEFT JOIN LATERAL (
            SELECT url FROM media_image
            WHERE media_id = m.id AND image_type = 'poster' AND is_primary = true
            LIMIT 1
        ) mi_poster ON true
        LEFT JOIN LATERAL (
            SELECT url FROM media_image
            WHERE media_id = m.id AND image_type = 'background' AND is_primary = true
            LIMIT 1
        ) mi_bg ON true
        LEFT JOIN LATERAL (
            SELECT url FROM media_image
            WHERE media_id = m.id AND image_type = 'logo' AND is_primary = true
            LIMIT 1
        ) mi_logo ON true
        LEFT JOIN LATERAL (
            SELECT r.rating FROM media_rating r
            JOIN rating_provider rp ON rp.id = r.rating_provider_id
            WHERE r.media_id = m.id AND lower(rp.name) IN ('imdb', 'tmdb')
            ORDER BY CASE lower(rp.name) WHEN 'imdb' THEN 0 ELSE 1 END
            LIMIT 1
        ) mr ON true
        WHERE m.id = ANY($1)
        "#,
    )
    .bind(media_ids)
    .fetch_all(pool)
    .await
    .unwrap_or_default();

    rows.into_iter().map(|row| (row.media_id, row)).collect()
}

fn runtime_label(minutes: Option<i32>) -> Option<String> {
    minutes.map(|mins| {
        if mins >= 60 {
            format!("{}h {}m", mins / 60, mins % 60)
        } else {
            format!("{mins}m")
        }
    })
}

pub(super) async fn search_import_db_matches(
    pool: &PgPool,
    title: &str,
    meta_type: &str,
    year: Option<i32>,
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

    let candidates = crate::db::search_media_candidates(pool, media_type, title, year).await;
    let mut results = Vec::with_capacity(candidates.len().min(limit));
    let mut eligible = Vec::new();

    for candidate in candidates {
        if eligible.len() >= limit {
            break;
        }
        if year.is_some()
            && !year_matches(year, candidate.year)
            && !year_matches(year, candidate.end_year)
        {
            continue;
        }
        if candidate.imdb_id.is_none()
            && candidate.tmdb_id.is_none()
            && candidate.tvdb_id.is_none()
            && candidate.mal_id.is_none()
            && candidate.kitsu_id.is_none()
        {
            continue;
        }
        eligible.push(candidate);
    }

    let media_ids: Vec<i32> = eligible.iter().map(|c| c.media_id.0).collect();
    let details_by_id = load_import_db_match_details(pool, &media_ids).await;

    for candidate in eligible {
        let id = candidate.media_id.0;
        let imdb_id = candidate.imdb_id;
        let tmdb_id = candidate.tmdb_id;
        let tvdb_id = candidate.tvdb_id;
        let mal_id = candidate.mal_id;
        let kitsu_id = candidate.kitsu_id;
        let primary_id = imdb_id
            .clone()
            .or_else(|| tmdb_id.as_ref().map(|t| format!("tmdb:{t}")))
            .or_else(|| tvdb_id.as_ref().map(|t| format!("tvdb:{t}")))
            .or_else(|| mal_id.as_ref().map(|t| format!("mal:{t}")))
            .unwrap_or_else(|| format!("media:{id}"));
        let details = details_by_id.get(&id);
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
            "description": details.and_then(|d| d.description.clone()),
            "poster": details.and_then(|d| d.poster.clone()),
            "background": details.and_then(|d| d.background.clone()),
            "logo": details.and_then(|d| d.logo.clone()),
            "release_date": details
                .and_then(|d| d.release_date)
                .map(|d| d.format("%Y-%m-%d").to_string()),
            "imdb_rating": details.and_then(|d| d.imdb_rating),
            "runtime": details.and_then(|d| runtime_label(d.runtime_minutes)),
        }));
    }
    results
}

pub(super) async fn build_db_match_from_media_id(
    pool: &PgPool,
    media_id: i32,
    media_type: &str,
    user_flags: Option<(bool, bool)>,
) -> Option<serde_json::Value> {
    let row: (String, Option<i32>, String) =
        sqlx::query_as("SELECT title, year, type::text FROM media WHERE id = $1")
            .bind(media_id)
            .fetch_optional(pool)
            .await
            .ok()
            .flatten()?;

    let (title, year, db_type) = row;
    let wire_type = if media_type == "sports" {
        media_type
    } else if db_type.eq_ignore_ascii_case("series") {
        "series"
    } else {
        "movie"
    };

    let ext_rows: Vec<(String, String)> =
        sqlx::query_as("SELECT provider, external_id FROM media_external_id WHERE media_id = $1")
            .bind(media_id)
            .fetch_all(pool)
            .await
            .unwrap_or_default();

    let mut imdb_id = None;
    let mut tmdb_id = None;
    let mut tvdb_id = None;
    let mut mal_id = None;
    let mut kitsu_id = None;
    for (provider, external_id) in ext_rows {
        match provider.as_str() {
            "imdb" => imdb_id = Some(external_id),
            "tmdb" => tmdb_id = Some(external_id),
            "tvdb" => tvdb_id = Some(external_id),
            "mal" => mal_id = Some(external_id),
            "kitsu" => kitsu_id = Some(external_id),
            _ => {}
        }
    }

    let primary_id = imdb_id
        .clone()
        .or_else(|| tmdb_id.as_ref().map(|t| format!("tmdb:{t}")))
        .or_else(|| tvdb_id.as_ref().map(|t| format!("tvdb:{t}")))
        .or_else(|| mal_id.as_ref().map(|t| format!("mal:{t}")))
        .unwrap_or_else(|| format!("media:{media_id}"));

    let details_by_id = load_import_db_match_details(pool, &[media_id]).await;
    let details = details_by_id.get(&media_id);

    let mut entry = json!({
        "id": primary_id,
        "imdb_id": imdb_id,
        "tmdb_id": tmdb_id,
        "tvdb_id": tvdb_id,
        "mal_id": mal_id,
        "kitsu_id": kitsu_id,
        "media_id": media_id,
        "title": title,
        "year": year,
        "type": wire_type,
        "description": details.and_then(|d| d.description.clone()),
        "poster": details.and_then(|d| d.poster.clone()),
        "background": details.and_then(|d| d.background.clone()),
        "logo": details.and_then(|d| d.logo.clone()),
        "release_date": details
            .and_then(|d| d.release_date)
            .map(|d| d.format("%Y-%m-%d").to_string()),
        "imdb_rating": details.and_then(|d| d.imdb_rating),
        "runtime": details.and_then(|d| runtime_label(d.runtime_minutes)),
    });

    if let Some((is_user_created, is_own)) = user_flags {
        if let Some(obj) = entry.as_object_mut() {
            obj.insert("is_user_created".to_string(), json!(is_user_created));
            obj.insert("is_own".to_string(), json!(is_own));
        }
    }

    Some(entry)
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

pub(crate) fn year_matches(query: Option<i32>, item: Option<i32>) -> bool {
    match (query, item) {
        (Some(q), Some(i)) => (q - i).abs() <= 1,
        _ => true,
    }
}

#[cfg(test)]
mod parse_tests {
    use super::{parse_import_meta_id, year_matches};

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

    #[test]
    fn year_matches_allows_plus_minus_one() {
        assert!(year_matches(Some(2025), Some(2025)));
        assert!(year_matches(Some(2025), Some(2024)));
        assert!(year_matches(Some(2025), Some(2026)));
        assert!(!year_matches(Some(2025), Some(2022)));
        assert!(year_matches(None, Some(2022)));
        assert!(year_matches(Some(2025), None));
    }
}
