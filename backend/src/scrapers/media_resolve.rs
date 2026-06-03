/// Shared media find-or-create logic used by RSS and spider scrapers.
///
/// Resolution order:
///   1. Exact title + type + year match in `media`
///   2. pg_trgm fuzzy match (similarity > 0.4, then similarity_ratio >= 70)
///   3. External metadata: TMDB (if API key) → Cinemeta/IMDb
///   4. Minimal stub creation so the stream is never lost
use std::collections::HashMap;

use futures::future::join_all;
use serde_json::Value;
use sqlx::PgPool;
use tracing::{debug, info};

use crate::db::MediaType;

fn wire_media_type(s: &str) -> Option<MediaType> {
    MediaType::from_wire(&s.to_ascii_lowercase())
}

pub struct MediaEntry {
    pub id: i32,
    pub title: String,
    pub year: Option<i32>,
}

pub async fn find_or_create_media(
    pool: &PgPool,
    http: &reqwest::Client,
    title: &str,
    year: Option<i32>,
    is_series: bool,
    catalog_ids: &[&str],
    tmdb_api_key: Option<&str>,
    cinemeta_fallback_enabled: bool,
) -> Option<MediaEntry> {
    find_or_create_media_with_anime(
        pool,
        http,
        title,
        year,
        is_series,
        catalog_ids,
        tmdb_api_key,
        cinemeta_fallback_enabled,
        &[],
        "tmdb",
    )
    .await
}

pub async fn find_or_create_media_with_anime(
    pool: &PgPool,
    http: &reqwest::Client,
    title: &str,
    year: Option<i32>,
    is_series: bool,
    catalog_ids: &[&str],
    tmdb_api_key: Option<&str>,
    cinemeta_fallback_enabled: bool,
    anime_source_order: &[String],
    metadata_primary_source: &str,
) -> Option<MediaEntry> {
    let media_type = if is_series {
        MediaType::Series
    } else {
        MediaType::Movie
    };

    // 1. Exact title match (case-insensitive) with ±1 year tolerance.
    let row: Option<(i32, String, Option<i32>)> = if let Some(y) = year {
        sqlx::query_as(
            "SELECT id, title, year FROM media \
             WHERE LOWER(title) = LOWER($1) AND type = $2 \
             AND (year = $3 OR year = $4 OR year IS NULL) LIMIT 1",
        )
        .bind(title)
        .bind(media_type)
        .bind(y)
        .bind(y - 1)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten()
    } else {
        sqlx::query_as(
            "SELECT id, title, year FROM media \
             WHERE LOWER(title) = LOWER($1) AND type = $2 LIMIT 1",
        )
        .bind(title)
        .bind(media_type)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten()
    };

    if let Some((id, t, y)) = row {
        debug!("media_resolve: found existing media {id} for '{title}'");
        link_to_catalogs(pool, id, catalog_ids).await;
        return Some(MediaEntry {
            id,
            title: t,
            year: y,
        });
    }

    // 2. Fuzzy pg_trgm match.
    // Use the `%` similarity operator (not the function) so the query planner
    // can use the GIN trigram index — the function form causes a seq-scan.
    let fuzzy: Option<(i32, String, Option<i32>)> = sqlx::query_as(
        "SELECT id, title, year FROM media \
         WHERE type = $1 AND title % $2 \
         ORDER BY similarity(title, $2) DESC LIMIT 1",
    )
    .bind(media_type)
    .bind(title)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten();

    if let Some((id, t, y)) = fuzzy {
        let sim = crate::parser::similarity_ratio(title, &t);
        if sim >= 70 {
            debug!("media_resolve: fuzzy match {id} (sim={sim}) for '{title}' → '{t}'");
            link_to_catalogs(pool, id, catalog_ids).await;
            return Some(MediaEntry {
                id,
                title: t,
                year: y,
            });
        }
    }

    // 3. External metadata lookup: TMDB → Cinemeta → anime providers.
    if let Some(meta) = crate::scrapers::metadata::search_by_title_with_anime_primary(
        http,
        title,
        year,
        is_series,
        tmdb_api_key,
        cinemeta_fallback_enabled,
        anime_source_order,
        metadata_primary_source,
    )
    .await
    {
        debug!(
            "media_resolve: external match '{}' ({:?}) via {} for '{title}'",
            meta.title, meta.year, meta.provider
        );

        // Check if this external_id is already in the DB (different local title).
        let existing: Option<(i32,)> = sqlx::query_as(
            "SELECT m.id FROM media m JOIN media_external_id mei ON mei.media_id = m.id \
             WHERE mei.provider = $1 AND mei.external_id = $2 AND m.type = $3 LIMIT 1",
        )
        .bind(&meta.provider)
        .bind(&meta.external_id)
        .bind(media_type)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten();

        if let Some((media_id,)) = existing {
            debug!("media_resolve: found existing media {media_id} via external_id");
            link_to_catalogs(pool, media_id, catalog_ids).await;
            return Some(MediaEntry {
                id: media_id,
                title: meta.title,
                year: meta.year,
            });
        }

        let match_meta = meta;
        let mut normalized = crate::scrapers::metadata::fetch_normalized(
            http,
            &import_fetch_ctx(tmdb_api_key, None),
            &match_meta.provider,
            &match_meta.external_id,
            is_series,
        )
        .await
        .unwrap_or_else(|| crate::db::NormalizedMetadata {
            media_type,
            title: match_meta.title.clone(),
            year: match_meta.year,
            poster_url: match_meta.poster_url.clone(),
            external_ids: vec![(match_meta.provider.clone(), match_meta.external_id.clone())],
            ..Default::default()
        });

        if match_meta.provider == "tmdb" {
            if let Some(key) = tmdb_api_key {
                if normalized.external_id("imdb").is_none() {
                    if let Some(iid) = crate::scrapers::metadata::imdb_id_from_tmdb(
                        http,
                        &match_meta.external_id,
                        is_series,
                        key,
                    )
                    .await
                    {
                        normalized.external_ids.push(("imdb".to_string(), iid));
                    }
                }
            }
        }

        let media_id =
            crate::db::store_media(pool, &normalized, crate::db::StoreMediaOpts::default())
                .await
                .ok()?;

        link_to_catalogs(pool, media_id.0, catalog_ids).await;
        info!(
            "media_resolve: created media {} from {} match for '{title}' → '{}'",
            media_id.0, match_meta.provider, match_meta.title
        );
        return Some(MediaEntry {
            id: media_id.0,
            title: match_meta.title,
            year: match_meta.year,
        });
    }

    // 4. No external match — create a minimal stub so the stream is not lost.
    let stub = crate::db::NormalizedMetadata {
        media_type,
        title: title.to_string(),
        year,
        ..Default::default()
    };
    let media_id = crate::db::store_media(pool, &stub, crate::db::StoreMediaOpts::default())
        .await
        .ok()?;
    link_to_catalogs(pool, media_id.0, catalog_ids).await;

    info!(
        "media_resolve: created stub media {} for '{title}'",
        media_id.0
    );
    Some(MediaEntry {
        id: media_id.0,
        title: title.to_string(),
        year,
    })
}

pub async fn insert_media_row(
    pool: &PgPool,
    media_type: MediaType,
    title: &str,
    year: Option<i32>,
) -> Option<i32> {
    let meta = crate::db::NormalizedMetadata {
        media_type,
        title: title.to_string(),
        year,
        ..Default::default()
    };
    crate::db::store_media(pool, &meta, crate::db::StoreMediaOpts::default())
        .await
        .ok()
        .map(|id| id.0)
}

pub async fn store_external_id(pool: &PgPool, media_id: i32, provider: &str, external_id: &str) {
    crate::db::store_external_id(pool, media_id, provider, external_id).await;
}

pub async fn link_to_catalogs(pool: &PgPool, media_id: i32, catalog_ids: &[&str]) {
    crate::db::link_to_catalogs(pool, media_id, catalog_ids).await;
}

pub async fn link_genre(pool: &PgPool, media_id: i32, genre_name: &str) {
    crate::db::link_genre(pool, media_id, genre_name).await;
}

pub struct ImportMediaOverrides<'a> {
    pub title: Option<&'a str>,
    pub poster: Option<&'a str>,
    pub background: Option<&'a str>,
    pub release_date: Option<&'a str>,
    pub year: Option<i32>,
}

/// Cache key for prefetched torrent import metadata (Python `prefetched_media_payloads`).
#[derive(Clone, Hash, PartialEq, Eq)]
pub struct ImportMetaCacheKey {
    pub meta_id: String,
    pub meta_type: String,
}

/// Prefetched provider payload or a title-only fallback when fetch fails.
#[derive(Clone)]
pub enum ImportMediaPrefetchEntry {
    Fetched(Box<crate::db::NormalizedMetadata>),
    FallbackTitle(String),
}

pub type ImportMetadataCache = HashMap<ImportMetaCacheKey, ImportMediaPrefetchEntry>;

/// Normalize UI meta ids (bare numeric TMDB ids → `tmdb:123`).
pub fn normalize_contributor_meta_id(meta_id: &str) -> String {
    let meta_id = meta_id.trim();
    if meta_id.is_empty() {
        return String::new();
    }
    if meta_id.contains(':') || meta_id.starts_with("tt") {
        meta_id.to_string()
    } else {
        format!("tmdb:{meta_id}")
    }
}

/// Map sports file metadata to movie/series for external provider fetch (Python `_resolve_fetch_media_type`).
pub fn resolve_file_fetch_meta_type(
    raw_meta_type: &str,
    sports_category: Option<&str>,
) -> &'static str {
    if raw_meta_type == "sports" {
        if matches!(
            sports_category.unwrap_or("other_sports"),
            "wwe" | "mma" | "boxing"
        ) {
            "series"
        } else {
            "movie"
        }
    } else if raw_meta_type == "series" {
        "series"
    } else {
        "movie"
    }
}

fn import_fetch_ctx<'a>(
    tmdb_api_key: Option<&'a str>,
    tvdb_api_key: Option<&'a str>,
) -> crate::scrapers::metadata::FetchCtx<'a> {
    crate::scrapers::metadata::FetchCtx::with_tmdb_tvdb(tmdb_api_key, tvdb_api_key, true)
}

/// Fetch metadata from external providers without holding a DB connection (Python `fetch_external_metadata_payload`).
pub async fn fetch_external_metadata_for_import(
    http: &reqwest::Client,
    meta_id: &str,
    meta_type: &str,
    fallback_title: Option<&str>,
    tmdb_api_key: Option<&str>,
    tvdb_api_key: Option<&str>,
) -> ImportMediaPrefetchEntry {
    if meta_type == "sports" {
        return ImportMediaPrefetchEntry::FallbackTitle(
            fallback_title.unwrap_or("Unknown").to_string(),
        );
    }

    let meta_id = normalize_contributor_meta_id(meta_id);
    if meta_id.is_empty() {
        return ImportMediaPrefetchEntry::FallbackTitle(
            fallback_title.unwrap_or("Unknown").to_string(),
        );
    }

    let is_series = meta_type == "series";
    if let Some((provider, ext_id)) = crate::scrapers::metadata::parse_import_meta_id(&meta_id) {
        if let Some(meta) = crate::scrapers::metadata::fetch_normalized(
            http,
            &import_fetch_ctx(tmdb_api_key, tvdb_api_key),
            provider,
            &ext_id,
            is_series,
        )
        .await
        {
            return ImportMediaPrefetchEntry::Fetched(Box::new(meta));
        }
    }

    ImportMediaPrefetchEntry::FallbackTitle(fallback_title.unwrap_or("Unknown").to_string())
}

/// Collect unique `(meta_id, fetch_meta_type, fallback_title)` tuples for prefetch.
pub fn collect_import_prefetch_requests(
    primary_meta_id: &str,
    primary_meta_type: &str,
    primary_title: &str,
    default_sports_category: Option<&str>,
    file_rows: &[Value],
) -> Vec<(String, String, Option<String>)> {
    let mut out: Vec<(String, String, Option<String>)> = Vec::new();
    let mut seen: Vec<(String, String)> = Vec::new();

    let mut push = |meta_id: &str, fetch_type: &str, fallback: Option<String>| {
        if meta_id.is_empty() || fetch_type == "sports" {
            return;
        }
        let meta_id = normalize_contributor_meta_id(meta_id);
        if meta_id.is_empty() {
            return;
        }
        let key = (meta_id.clone(), fetch_type.to_string());
        if seen.iter().any(|k| k == &key) {
            return;
        }
        seen.push(key);
        out.push((meta_id, fetch_type.to_string(), fallback));
    };

    if primary_meta_type != "sports" && !primary_meta_id.is_empty() {
        push(
            primary_meta_id,
            primary_meta_type,
            Some(primary_title.to_string()),
        );
    }

    for file in file_rows {
        let Some(file_meta_id) = file.get("meta_id").and_then(|v| v.as_str()) else {
            continue;
        };
        if file_meta_id.is_empty() {
            continue;
        }
        let raw_type = file
            .get("meta_type")
            .and_then(|v| v.as_str())
            .unwrap_or(primary_meta_type);
        let sports_cat = file
            .get("sports_category")
            .and_then(|v| v.as_str())
            .or(default_sports_category);
        let fetch_type = if raw_type == "sports" || primary_meta_type == "sports" {
            resolve_file_fetch_meta_type("sports", sports_cat)
        } else {
            resolve_file_fetch_meta_type(raw_type, None)
        };
        let fallback = file
            .get("meta_title")
            .or_else(|| file.get("title"))
            .and_then(|v| v.as_str())
            .map(str::to_string);
        push(file_meta_id, fetch_type, fallback);
    }

    out
}

/// Prefetch all external metadata for a torrent import before DB writes.
pub async fn prefetch_import_metadata(
    http: &reqwest::Client,
    tmdb_api_key: Option<&str>,
    tvdb_api_key: Option<&str>,
    primary_meta_id: &str,
    primary_meta_type: &str,
    primary_title: &str,
    default_sports_category: Option<&str>,
    file_rows: &[Value],
) -> ImportMetadataCache {
    let requests = collect_import_prefetch_requests(
        primary_meta_id,
        primary_meta_type,
        primary_title,
        default_sports_category,
        file_rows,
    );

    let fetches = join_all(requests.into_iter().map(|(meta_id, meta_type, fallback)| {
        let http = http.clone();
        async move {
            let key = ImportMetaCacheKey {
                meta_id: meta_id.clone(),
                meta_type: meta_type.clone(),
            };
            let entry = fetch_external_metadata_for_import(
                &http,
                &meta_id,
                &meta_type,
                fallback.as_deref(),
                tmdb_api_key,
                tvdb_api_key,
            )
            .await;
            (key, entry)
        }
    }))
    .await;

    fetches.into_iter().collect()
}

fn prefetched_meta(
    cache: Option<&ImportMetadataCache>,
    meta_id: &str,
    meta_type: &str,
) -> Option<crate::db::NormalizedMetadata> {
    let key = ImportMetaCacheKey {
        meta_id: normalize_contributor_meta_id(meta_id),
        meta_type: meta_type.to_string(),
    };
    match cache?.get(&key) {
        Some(ImportMediaPrefetchEntry::Fetched(d)) => Some(d.as_ref().clone()),
        _ => None,
    }
}

fn prefetched_fallback_title(
    cache: Option<&ImportMetadataCache>,
    meta_id: &str,
    meta_type: &str,
) -> Option<String> {
    let key = ImportMetaCacheKey {
        meta_id: normalize_contributor_meta_id(meta_id),
        meta_type: meta_type.to_string(),
    };
    match cache?.get(&key) {
        Some(ImportMediaPrefetchEntry::FallbackTitle(t)) => Some(t.clone()),
        _ => None,
    }
}

/// Resolve or create `media` for a user torrent import from `meta_id` (tt*, tmdb:*, etc.).
pub async fn ensure_media_for_import(
    pool: &PgPool,
    http: &reqwest::Client,
    meta_id: &str,
    meta_type: &str,
    tmdb_api_key: Option<&str>,
    tvdb_api_key: Option<&str>,
    overrides: ImportMediaOverrides<'_>,
    prefetch: Option<&ImportMetadataCache>,
) -> Option<i32> {
    let meta_id = meta_id.trim();
    if meta_id.is_empty() {
        return None;
    }

    if let Some(raw) = meta_id
        .strip_prefix("mf:")
        .or_else(|| meta_id.strip_prefix("mf"))
    {
        if let Ok(id) = raw.parse::<i32>() {
            return Some(id);
        }
    }

    if let Ok(Some(id)) =
        crate::db::get_media_id_by_external_id(pool, meta_id, Some(meta_type)).await
    {
        return Some(id.0);
    }

    let fetch_meta_type = if meta_type == "sports" {
        "movie"
    } else {
        meta_type
    };
    let is_series = fetch_meta_type == "series";
    let db_type = if is_series {
        MediaType::Series
    } else {
        MediaType::Movie
    };

    let mut normalized = prefetched_meta(prefetch, meta_id, fetch_meta_type);
    if normalized.is_none() && prefetch.is_none() {
        if let Some((provider, ext_id)) = crate::scrapers::metadata::parse_import_meta_id(meta_id) {
            normalized = crate::scrapers::metadata::fetch_normalized(
                http,
                &import_fetch_ctx(tmdb_api_key, tvdb_api_key),
                provider,
                &ext_id,
                is_series,
            )
            .await;
        }
    }

    let fallback_title = overrides
        .title
        .filter(|t| !t.is_empty())
        .map(str::to_string)
        .or_else(|| normalized.as_ref().map(|d| d.title.clone()))
        .or_else(|| prefetched_fallback_title(prefetch, meta_id, fetch_meta_type))
        .or_else(|| Some(meta_id.to_string()))?;

    let mut meta = normalized.unwrap_or_else(|| crate::db::NormalizedMetadata {
        media_type: db_type,
        title: fallback_title,
        year: overrides.year,
        ..Default::default()
    });

    meta.apply_overrides(
        overrides.title,
        overrides.year,
        overrides.poster,
        overrides.background,
        overrides.release_date,
    );

    if meta.external_ids.is_empty() {
        if let Some((provider, ext_id)) = crate::scrapers::metadata::parse_import_meta_id(meta_id) {
            meta.external_ids.push((provider.to_string(), ext_id));
        }
    }

    let media_id = crate::db::store_media(pool, &meta, crate::db::StoreMediaOpts::default())
        .await
        .ok()?;

    info!(
        "ensure_media_for_import: stored media {} for meta_id={meta_id}",
        media_id.0
    );
    Some(media_id.0)
}

pub async fn link_stream_to_media(
    pool: &PgPool,
    stream_id: crate::db::StreamId,
    media_id: crate::db::MediaId,
) -> Result<(), sqlx::Error> {
    crate::db::link_stream_to_media(pool, stream_id, media_id).await
}

/// Minimum title similarity for DMM hashlist metadata linking (Python parity).
pub const DMM_METADATA_MIN_SIMILARITY: u32 = 87;

/// Dynamic similarity floor for short/generic titles (mirrors user_library).
pub fn dmm_dynamic_min_similarity(title: &str) -> u32 {
    let compact_len = title
        .to_lowercase()
        .chars()
        .filter(|c| c.is_alphanumeric())
        .count();
    if compact_len <= 4 {
        90
    } else if compact_len <= 8 {
        80
    } else {
        DMM_METADATA_MIN_SIMILARITY
    }
}

/// Extract a calendar year from a DMM-style broadcast date tag, e.g. `[250504]` → 2025.
pub fn extract_bracket_air_year(torrent_title: &str) -> Option<i32> {
    static AIR_DATE_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let re = AIR_DATE_RE.get_or_init(|| regex::Regex::new(r"\[(\d{6})\]").unwrap());
    let caps = re.captures(torrent_title)?;
    let tag = caps.get(1)?.as_str();
    let yy: i32 = tag.get(0..2)?.parse().ok()?;
    let mm: i32 = tag.get(2..4)?.parse().ok()?;
    let dd: i32 = tag.get(4..6)?.parse().ok()?;
    if !(1..=12).contains(&mm) || !(1..=31).contains(&dd) {
        return None;
    }
    Some(if yy >= 50 { 1900 + yy } else { 2000 + yy })
}

/// Validate a metadata candidate before linking a DMM hashlist stream.
pub fn is_valid_dmm_metadata_match(
    parsed_title: &str,
    parsed_year: Option<i32>,
    media_type: &str,
    candidate_title: &str,
    candidate_year: Option<i32>,
    candidate_end_year: Option<i32>,
    episode_number: Option<i32>,
) -> bool {
    let min_similarity = dmm_dynamic_min_similarity(parsed_title);
    let similarity = crate::parser::max_similarity_ratio(parsed_title, candidate_title, &[]);
    if similarity < min_similarity {
        return false;
    }

    if let (Some(episode), Some(start_year), Some(air_year)) =
        (episode_number, candidate_year, parsed_year)
    {
        if episode > 0 {
            let max_reasonable = (air_year - start_year + 1).saturating_mul(60);
            if max_reasonable > 0 && episode > max_reasonable {
                return false;
            }
        }
    }

    let Some(parsed_year) = parsed_year else {
        return true;
    };

    let Some(candidate_year) = candidate_year else {
        return true;
    };

    if media_type == "movie" {
        return candidate_year == parsed_year;
    }

    if let Some(end_year) = candidate_end_year {
        return candidate_year <= parsed_year && parsed_year <= end_year;
    }

    parsed_year >= candidate_year
}

fn dmm_import_meta_id(entry: &serde_json::Value) -> Option<String> {
    if let Some(imdb) = entry.get("imdb_id").and_then(|v| v.as_str()) {
        if !imdb.is_empty() {
            return Some(imdb.to_string());
        }
    }
    entry
        .get("id")
        .and_then(|v| v.as_str())
        .filter(|id| !id.is_empty() && !id.starts_with("media:") && !id.starts_with("mf:"))
        .map(str::to_string)
}

fn dmm_candidate_has_external_id(candidate: &crate::db::media::MediaCandidate) -> bool {
    candidate.imdb_id.is_some() || candidate.tmdb_id.is_some() || candidate.tvdb_id.is_some()
}

fn dmm_effective_year(parsed_year: Option<i32>, torrent_title: Option<&str>) -> Option<i32> {
    parsed_year.or_else(|| torrent_title.and_then(extract_bracket_air_year))
}

fn dmm_score_candidate(
    parsed_title: &str,
    effective_year: Option<i32>,
    candidate: &crate::db::media::MediaCandidate,
    akas: &[String],
) -> i32 {
    let sim = crate::parser::max_similarity_ratio(parsed_title, &candidate.title, akas) as i32;
    let mut score = sim;
    if let Some(y) = effective_year {
        if let Some(cy) = candidate.year {
            if cy == y {
                score += 8;
            } else if (cy - y).abs() <= 1 {
                score += 2;
            }
        }
        if let Some(end) = candidate.end_year {
            if cy_in_range(y, candidate.year, Some(end)) {
                score += 4;
            }
        } else if candidate
            .year
            .is_some_and(|cy| effective_year.is_some_and(|y| y >= cy))
        {
            score += 2;
        }
    }
    if candidate.imdb_id.is_some() {
        score += 2;
    }
    if candidate.tmdb_id.is_some() {
        score += 1;
    }
    score
}

fn cy_in_range(year: i32, start: Option<i32>, end: Option<i32>) -> bool {
    let Some(start) = start else {
        return false;
    };
    if let Some(end) = end {
        start <= year && year <= end
    } else {
        year >= start
    }
}

async fn pick_best_dmm_db_candidate(
    pool: &PgPool,
    title: &str,
    effective_year: Option<i32>,
    media_type: &str,
    episode_number: Option<i32>,
) -> Option<crate::scrapers::SearchMeta> {
    let mut best: Option<(i32, crate::scrapers::SearchMeta)> = None;

    for candidate in crate::db::search_media_candidates(pool, media_type, title).await {
        if !dmm_candidate_has_external_id(&candidate) {
            continue;
        }
        let akas = crate::db::load_aka_titles(pool, candidate.media_id).await;
        if !is_valid_dmm_metadata_match(
            title,
            effective_year,
            media_type,
            &candidate.title,
            candidate.year,
            candidate.end_year,
            episode_number,
        ) && !akas.iter().any(|aka| {
            is_valid_dmm_metadata_match(
                title,
                effective_year,
                media_type,
                aka,
                candidate.year,
                candidate.end_year,
                episode_number,
            )
        }) {
            continue;
        }

        let score = dmm_score_candidate(title, effective_year, &candidate, &akas);
        let meta = crate::scrapers::SearchMeta {
            media_id: candidate.media_id,
            imdb_id: candidate.imdb_id,
            title: candidate.title,
            year: candidate.year,
        };
        if best
            .as_ref()
            .is_none_or(|(best_score, _)| score > *best_score)
        {
            best = Some((score, meta));
        }
    }

    best.map(|(_, meta)| meta)
}

/// Resolve DMM hashlist metadata without creating title-only stubs.
///
/// Mirrors Python `_resolve_meta_id`: DB search → external providers → `None` when
/// no confident match (similarity ≥ 87, year sanity). Requires real provider IDs
/// (imdb/tmdb/tvdb) — internal `media.id` is never written to `media_external_id`.
pub async fn search_meta_for_dmm_hashlist(
    pool: &PgPool,
    http: &reqwest::Client,
    title: &str,
    year: Option<i32>,
    is_series: bool,
    tmdb_api_key: Option<&str>,
    tvdb_api_key: Option<&str>,
    cinemeta_fallback_enabled: bool,
    torrent_title: Option<&str>,
    episode_number: Option<i32>,
) -> Option<crate::scrapers::SearchMeta> {
    let media_type = if is_series { "series" } else { "movie" };
    let effective_year = dmm_effective_year(year, torrent_title);

    if let Some(meta) =
        pick_best_dmm_db_candidate(pool, title, effective_year, media_type, episode_number).await
    {
        return Some(meta);
    }

    let matches = crate::scrapers::metadata::search_import_matches(
        http,
        pool,
        title,
        effective_year,
        media_type,
        tmdb_api_key,
        tvdb_api_key,
        cinemeta_fallback_enabled,
    )
    .await;

    let mut best: Option<(i32, crate::scrapers::SearchMeta)> = None;

    for entry in matches {
        let candidate_title = match entry.get("title").and_then(|v| v.as_str()) {
            Some(t) if !t.is_empty() => t,
            _ => continue,
        };
        let candidate_year = entry.get("year").and_then(|v| v.as_i64()).map(|y| y as i32);
        let candidate_end_year = entry
            .get("end_year")
            .and_then(|v| v.as_i64())
            .map(|y| y as i32);
        if !is_valid_dmm_metadata_match(
            title,
            effective_year,
            media_type,
            candidate_title,
            candidate_year,
            candidate_end_year,
            episode_number,
        ) {
            continue;
        }

        let Some(meta_id) = dmm_import_meta_id(&entry) else {
            continue;
        };

        let media_id = ensure_media_for_import(
            pool,
            http,
            &meta_id,
            media_type,
            tmdb_api_key,
            tvdb_api_key,
            ImportMediaOverrides {
                title: Some(candidate_title),
                poster: entry.get("poster").and_then(|v| v.as_str()),
                background: None,
                release_date: None,
                year: candidate_year.or(effective_year),
            },
            None,
        )
        .await?;

        let sim = crate::parser::max_similarity_ratio(title, candidate_title, &[]) as i32;
        let mut score = sim;
        if let Some(y) = effective_year {
            if candidate_year == Some(y) {
                score += 8;
            }
        }
        if entry.get("imdb_id").and_then(|v| v.as_str()).is_some() {
            score += 2;
        }

        let meta = crate::scrapers::SearchMeta {
            media_id: crate::db::MediaId(media_id),
            imdb_id: entry
                .get("imdb_id")
                .and_then(|v| v.as_str())
                .map(str::to_string),
            title: candidate_title.to_string(),
            year: candidate_year.or(effective_year),
        };

        if best
            .as_ref()
            .is_none_or(|(best_score, _)| score > *best_score)
        {
            best = Some((score, meta));
        }
    }

    if let Some((_, meta)) = best {
        return Some(meta);
    }

    debug!("media_resolve: no confident DMM match for '{title}' ({media_type})");
    None
}

/// Resolve media from a parsed title before persisting scraped streams.
pub async fn search_meta_for_title(
    pool: &PgPool,
    http: &reqwest::Client,
    title: &str,
    year: Option<i32>,
    is_series: bool,
    tmdb_api_key: Option<&str>,
    cinemeta_fallback_enabled: bool,
) -> Option<crate::scrapers::SearchMeta> {
    search_meta_for_title_with_anime(
        pool,
        http,
        title,
        year,
        is_series,
        tmdb_api_key,
        cinemeta_fallback_enabled,
        &[],
        "tmdb",
    )
    .await
}

pub async fn search_meta_for_title_with_anime(
    pool: &PgPool,
    http: &reqwest::Client,
    title: &str,
    year: Option<i32>,
    is_series: bool,
    tmdb_api_key: Option<&str>,
    cinemeta_fallback_enabled: bool,
    anime_source_order: &[String],
    metadata_primary_source: &str,
) -> Option<crate::scrapers::SearchMeta> {
    let media = find_or_create_media_with_anime(
        pool,
        http,
        title,
        year,
        is_series,
        &[],
        tmdb_api_key,
        cinemeta_fallback_enabled,
        anime_source_order,
        metadata_primary_source,
    )
    .await?;
    Some(crate::scrapers::SearchMeta {
        media_id: crate::db::MediaId(media.id),
        imdb_id: None,
        title: media.title,
        year: media.year,
    })
}

/// Resolve metadata for a Telegram feed candidate, preferring caption IMDb ID when present.
pub async fn search_meta_for_telegram_feed(
    pool: &PgPool,
    http: &reqwest::Client,
    title: &str,
    year: Option<i32>,
    is_series: bool,
    caption_imdb_id: Option<&str>,
    tmdb_api_key: Option<&str>,
    tvdb_api_key: Option<&str>,
    cinemeta_fallback_enabled: bool,
    anime_source_order: &[String],
    metadata_primary_source: &str,
) -> Option<crate::scrapers::SearchMeta> {
    let media_type = if is_series { "series" } else { "movie" };

    if let Some(imdb) = caption_imdb_id.filter(|s| !s.is_empty()) {
        let overrides = ImportMediaOverrides {
            title: Some(title),
            poster: None,
            background: None,
            release_date: None,
            year,
        };
        if let Some(media_id) = ensure_media_for_import(
            pool,
            http,
            imdb,
            media_type,
            tmdb_api_key,
            tvdb_api_key,
            overrides,
            None,
        )
        .await
        {
            let row: Option<(String, Option<i32>, Option<String>)> = sqlx::query_as(
                "SELECT m.title, m.year, mei.external_id \
                 FROM media m \
                 LEFT JOIN media_external_id mei \
                   ON mei.media_id = m.id AND mei.provider = 'imdb' \
                 WHERE m.id = $1",
            )
            .bind(media_id)
            .fetch_optional(pool)
            .await
            .ok()
            .flatten();

            if let Some((resolved_title, resolved_year, imdb_ext)) = row {
                return Some(crate::scrapers::SearchMeta {
                    media_id: crate::db::MediaId(media_id),
                    imdb_id: imdb_ext.or_else(|| Some(imdb.to_string())),
                    title: resolved_title,
                    year: resolved_year,
                });
            }
        }
    }

    search_meta_for_title_with_anime(
        pool,
        http,
        title,
        year,
        is_series,
        tmdb_api_key,
        cinemeta_fallback_enabled,
        anime_source_order,
        metadata_primary_source,
    )
    .await
}

/// Resolve media for a feed/spider scraped torrent before persistence (skip when unresolved).
pub async fn search_meta_for_scraped(
    pool: &PgPool,
    http: &reqwest::Client,
    stream: &crate::scrapers::ScrapedStream,
    is_series: bool,
    tmdb_api_key: Option<&str>,
    cinemeta_fallback_enabled: bool,
    anime_source_order: &[String],
    metadata_primary_source: &str,
) -> Option<crate::scrapers::SearchMeta> {
    let title = stream
        .parsed
        .title
        .as_deref()
        .filter(|t| !t.is_empty())
        .unwrap_or(stream.name.as_str());
    search_meta_for_title_with_anime(
        pool,
        http,
        title,
        stream.parsed.year,
        is_series,
        tmdb_api_key,
        cinemeta_fallback_enabled,
        anime_source_order,
        metadata_primary_source,
    )
    .await
}

/// Find or create a minimal media stub for a sports event, skipping any external
/// metadata lookup. Sports event titles (match replays, highlight clips) are not
/// on TMDB/IMDb, so a remote lookup would be wasted latency.
///
/// Sets `is_add_title_to_poster = true` on newly-created stubs so the poster
/// endpoint auto-selects a genre-matched poster from the bundled sports artifacts.
pub async fn find_or_create_sports_stub(
    pool: &PgPool,
    title: &str,
    year: Option<i32>,
    poster_url: Option<&str>,
    media_type: &str,
) -> Option<i32> {
    // 1. Exact title match (case-insensitive).
    let mt = wire_media_type(media_type)?;
    let row: Option<(i32,)> = if let Some(y) = year {
        sqlx::query_as(
            "SELECT id FROM media WHERE LOWER(title) = LOWER($1) \
             AND type = $2 AND (year = $3 OR year IS NULL) LIMIT 1",
        )
        .bind(title)
        .bind(mt)
        .bind(y)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten()
    } else {
        sqlx::query_as(
            "SELECT id FROM media WHERE LOWER(title) = LOWER($1) \
             AND type = $2 LIMIT 1",
        )
        .bind(title)
        .bind(mt)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten()
    };

    if let Some((id,)) = row {
        return Some(id);
    }

    // 2. Fuzzy pg_trgm match (same threshold as find_or_create_media).
    let fuzzy: Option<(i32, String)> = sqlx::query_as(
        "SELECT id, title FROM media WHERE type = $1 AND title % $2 \
         ORDER BY similarity(title, $2) DESC LIMIT 1",
    )
    .bind(mt)
    .bind(title)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten();

    if let Some((id, existing_title)) = fuzzy {
        if crate::parser::similarity_ratio(title, &existing_title) >= 70 {
            return Some(id);
        }
    }

    // 3. Create stub with is_add_title_to_poster = true via the metadata funnel.
    let meta = crate::db::NormalizedMetadata {
        media_type: mt,
        title: title.to_string(),
        year,
        poster_url: poster_url.map(str::to_string),
        ..Default::default()
    };

    if let Some(existing) = crate::db::find_existing_media(pool, mt, title, year).await {
        if let Some(url) = poster_url {
            crate::db::upsert_primary_image(pool, existing.0, "poster", url).await;
        }
        return Some(existing.0);
    }

    let media_id = crate::db::store_media(pool, &meta, crate::db::StoreMediaOpts::sports_stub())
        .await
        .ok()?;

    debug!(
        "media_resolve: created sports stub {} ({media_type}) for '{title}'",
        media_id.0
    );
    Some(media_id.0)
}

#[cfg(test)]
mod prefetch_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn normalize_bare_tmdb_id() {
        assert_eq!(normalize_contributor_meta_id("603"), "tmdb:603");
        assert_eq!(normalize_contributor_meta_id("tt0111161"), "tt0111161");
    }

    #[test]
    fn collects_primary_and_per_file_prefetch_keys() {
        let files = vec![
            json!({"meta_id": "tt1234567", "meta_type": "movie"}),
            json!({"meta_id": "603", "meta_title": "Episode 2"}),
        ];
        let reqs = collect_import_prefetch_requests("tt999", "series", "Show Name", None, &files);
        assert!(reqs
            .iter()
            .any(|(id, ty, _)| id == "tt999" && ty == "series"));
        assert!(reqs
            .iter()
            .any(|(id, ty, _)| id == "tt1234567" && ty == "movie"));
        assert!(reqs
            .iter()
            .any(|(id, ty, _)| id == "tmdb:603" && ty == "series"));
        assert_eq!(reqs.len(), 3);
    }

    #[test]
    fn skips_prefetch_for_sports_primary() {
        let reqs =
            collect_import_prefetch_requests("tt1", "sports", "Match", Some("football"), &[]);
        assert!(reqs.is_empty());
    }
}
