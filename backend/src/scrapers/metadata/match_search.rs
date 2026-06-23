use std::collections::HashSet;

use serde_json::{Value, json};
use sqlx::PgPool;

use crate::db::UserId;

use super::{
    FetchCtx, IMPORT_SEARCH_LIMIT, fetch_by_external_id_with_opts, fetch_normalized,
    import_match_from_details, import_match_from_normalized, parse_import_meta_id, year_matches,
};
use super::{anilist, imdb, kitsu, tmdb, tvdb};

/// Options for unified media match search (DB first, then external providers).
#[derive(Debug, Clone)]
pub struct MediaMatchSearchOptions<'a> {
    pub title: Option<&'a str>,
    pub year: Option<i32>,
    pub external_id: Option<&'a str>,
    pub media_type: &'a str,
    pub limit: usize,
    pub user_id: Option<UserId>,
    pub include_user_content: bool,
    pub include_official: bool,
    pub include_catalog: bool,
    pub include_external: bool,
    pub tmdb_api_key: Option<&'a str>,
    pub tvdb_api_key: Option<&'a str>,
    pub cinemeta_fallback_enabled: bool,
}

impl Default for MediaMatchSearchOptions<'_> {
    fn default() -> Self {
        Self {
            title: None,
            year: None,
            external_id: None,
            media_type: "movie",
            limit: IMPORT_SEARCH_LIMIT,
            user_id: None,
            include_user_content: false,
            include_official: true,
            include_catalog: true,
            include_external: true,
            tmdb_api_key: None,
            tvdb_api_key: None,
            cinemeta_fallback_enabled: true,
        }
    }
}

pub async fn search_media_matches(
    http: &reqwest::Client,
    pool: &PgPool,
    opts: MediaMatchSearchOptions<'_>,
) -> Vec<Value> {
    let started = std::time::Instant::now();
    let limit = opts.limit.clamp(1, 50);

    let results =
        if let Some(external_id) = opts.external_id.map(str::trim).filter(|s| !s.is_empty()) {
            lookup_matches_by_external_id(http, pool, external_id, opts.media_type, &opts).await
        } else if let Some(title) = opts.title.map(str::trim).filter(|s| !s.is_empty()) {
            search_matches_by_title(http, pool, title, opts.year, &opts, limit).await
        } else {
            vec![]
        };

    tracing::debug!(
        title = ?opts.title,
        year = ?opts.year,
        external_id = ?opts.external_id,
        media_type = %opts.media_type,
        result_count = results.len(),
        elapsed_ms = started.elapsed().as_millis(),
        "media match search complete"
    );

    results
}

async fn lookup_matches_by_external_id(
    http: &reqwest::Client,
    pool: &PgPool,
    external_id: &str,
    media_type: &str,
    opts: &MediaMatchSearchOptions<'_>,
) -> Vec<Value> {
    let Some((provider, provider_external_id)) = parse_import_meta_id(external_id) else {
        return vec![];
    };

    let lookup_id = if provider == "imdb" {
        provider_external_id.clone()
    } else {
        format!("{provider}:{provider_external_id}")
    };

    let meta_type = normalize_meta_type(media_type);
    if let Some(media_id) =
        crate::db::get_media_id_by_external_id(pool, &lookup_id, Some(meta_type))
            .await
            .ok()
            .flatten()
    {
        if let Some(mut entry) =
            super::build_db_match_from_media_id(pool, media_id.0, meta_type, None).await
        {
            tag_match(&mut entry, "database");
            return vec![entry];
        }
    }

    if !opts.include_external {
        return vec![];
    }

    let is_series = meta_type == "series";
    let ctx = fetch_ctx(opts);
    let Some(details) =
        fetch_by_external_id_with_opts(http, provider, &provider_external_id, is_series, ctx).await
    else {
        return vec![];
    };

    let mut entry = import_match_from_details(&details, meta_type);
    tag_match(&mut entry, "external");
    vec![entry]
}

async fn search_matches_by_title(
    http: &reqwest::Client,
    pool: &PgPool,
    title: &str,
    year: Option<i32>,
    opts: &MediaMatchSearchOptions<'_>,
    limit: usize,
) -> Vec<Value> {
    if opts.media_type == "all" {
        let mut combined = Vec::new();
        let mut seen = HashSet::new();
        for mt in ["movie", "series"] {
            let per_type_limit = limit.saturating_sub(combined.len()).max(1);
            let mut sub_opts = opts.clone();
            sub_opts.media_type = mt;
            sub_opts.limit = per_type_limit;
            for entry in
                search_matches_by_title_single(http, pool, title, year, &sub_opts, per_type_limit)
                    .await
            {
                if push_unique(&mut combined, &mut seen, entry) && combined.len() >= limit {
                    return combined;
                }
            }
            if combined.len() >= limit {
                break;
            }
        }
        return combined;
    }

    search_matches_by_title_single(http, pool, title, year, opts, limit).await
}

async fn search_matches_by_title_single(
    http: &reqwest::Client,
    pool: &PgPool,
    title: &str,
    year: Option<i32>,
    opts: &MediaMatchSearchOptions<'_>,
    limit: usize,
) -> Vec<Value> {
    let meta_type = normalize_meta_type(opts.media_type);

    if meta_type == "sports" {
        let mut results = Vec::new();
        for mut entry in super::search_import_db_matches(pool, title, meta_type, year, limit).await
        {
            tag_match(&mut entry, "database");
            results.push(entry);
        }
        return results;
    }

    let mut seen = HashSet::new();
    let mut results = Vec::new();

    if opts.include_user_content {
        if let Some(user_id) = opts.user_id {
            for mut entry in search_user_accessible_db_matches(
                pool,
                user_id,
                title,
                meta_type,
                limit,
                opts.include_official,
            )
            .await
            {
                tag_match(&mut entry, "database");
                push_unique(&mut results, &mut seen, entry);
                if results.len() >= limit {
                    return results;
                }
            }
        }
    }

    if opts.include_catalog {
        for mut entry in super::search_import_db_matches(pool, title, meta_type, year, limit).await
        {
            tag_match(&mut entry, "database");
            push_unique(&mut results, &mut seen, entry);
            if results.len() >= limit {
                return results;
            }
        }
    }

    if !opts.include_external || results.len() >= limit {
        return results;
    }

    append_external_title_matches(
        http,
        pool,
        title,
        year,
        meta_type,
        limit,
        opts,
        &mut results,
        &mut seen,
    )
    .await;
    results
}

async fn append_external_title_matches(
    http: &reqwest::Client,
    _pool: &PgPool,
    title: &str,
    year: Option<i32>,
    meta_type: &str,
    limit: usize,
    opts: &MediaMatchSearchOptions<'_>,
    results: &mut Vec<Value>,
    seen: &mut HashSet<String>,
) {
    let is_series = meta_type == "series";
    let media_type = if is_series { "series" } else { "movie" };
    let ctx = fetch_ctx(opts);

    let push_match = |results: &mut Vec<Value>, seen: &mut HashSet<String>, mut entry: Value| {
        if year.is_some() {
            let item_year = entry["year"].as_i64().map(|y| y as i32);
            if !year_matches(year, item_year) {
                return false;
            }
        }
        tag_match(&mut entry, "external");
        push_unique(results, seen, entry)
    };

    if opts.cinemeta_fallback_enabled {
        for m in imdb::search(http, title, year, is_series, limit).await {
            let imdb_id = m.external_id;
            if push_match(
                results,
                seen,
                json!({
                    "id": imdb_id,
                    "imdb_id": imdb_id,
                    "title": m.title,
                    "year": m.year,
                    "poster": m.poster_url,
                    "type": media_type,
                }),
            ) && results.len() >= limit
            {
                return;
            }
        }
    }

    if let Some(tvdb_key) = opts.tvdb_api_key {
        for entry in tvdb::search_import_tvdb(http, tvdb_key, title, media_type, limit).await {
            if push_match(results, seen, entry) && results.len() >= limit {
                return;
            }
        }
    }

    if opts.tmdb_api_key.is_some() {
        for m in tmdb::search(http, &ctx, title, year, is_series, limit).await {
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
            if push_match(results, seen, entry) && results.len() >= limit {
                return;
            }
        }
    }

    if meta_type == "series" {
        for entry in anilist::search_import_anilist(http, title, limit).await {
            if push_match(results, seen, entry) && results.len() >= limit {
                return;
            }
        }
        for entry in kitsu::search_import_kitsu(http, title, limit).await {
            if push_match(results, seen, entry) && results.len() >= limit {
                return;
            }
        }
    }
}

async fn search_user_accessible_db_matches(
    pool: &PgPool,
    user_id: UserId,
    title: &str,
    media_type: &str,
    limit: usize,
    include_official: bool,
) -> Vec<Value> {
    let pattern = format!("%{title}%");
    let mut sql = String::from("SELECT id FROM media WHERE title ILIKE $1");

    match media_type {
        "movie" => sql.push_str(" AND type = 'MOVIE'::mediatype"),
        "series" => sql.push_str(" AND type = 'SERIES'::mediatype"),
        "tv" => sql.push_str(" AND type = 'TV'::mediatype"),
        _ => {}
    }

    if include_official {
        sql.push_str(
            " AND (is_user_created = false OR created_by_user_id = $2 OR is_public = true)",
        );
    } else {
        sql.push_str(" AND created_by_user_id = $2");
    }

    sql.push_str(&format!(" ORDER BY total_streams DESC LIMIT {limit}"));

    let ids: Vec<i32> = sqlx::query_scalar(sqlx::AssertSqlSafe(sql.as_str()))
        .bind(&pattern)
        .bind(user_id)
        .fetch_all(pool)
        .await
        .unwrap_or_default();

    let mut results = Vec::new();
    for media_id in ids {
        let flags = user_flags_for_media(pool, media_id, user_id).await;
        if let Some(entry) =
            super::build_db_match_from_media_id(pool, media_id, media_type, flags).await
        {
            results.push(entry);
        }
    }
    results
}

async fn user_flags_for_media(
    pool: &PgPool,
    media_id: i32,
    user_id: UserId,
) -> Option<(bool, bool)> {
    let row: (bool, Option<i32>) =
        sqlx::query_as("SELECT is_user_created, created_by_user_id FROM media WHERE id = $1")
            .bind(media_id)
            .fetch_optional(pool)
            .await
            .ok()
            .flatten()?;

    Some((row.0, row.1 == Some(i32::from(user_id))))
}

fn fetch_ctx<'a>(opts: &'a MediaMatchSearchOptions<'a>) -> FetchCtx<'a> {
    FetchCtx {
        tmdb_api_key: opts.tmdb_api_key,
        tvdb_api_key: opts.tvdb_api_key,
        mdblist_api_key: None,
        trakt_client_id: None,
        trakt_client_secret: None,
        cinemeta_fallback: opts.cinemeta_fallback_enabled,
    }
}

fn normalize_meta_type(media_type: &str) -> &str {
    match media_type {
        "show" => "series",
        other => other,
    }
}

fn dedup_key(entry: &Value) -> Option<String> {
    entry["imdb_id"]
        .as_str()
        .map(|id| format!("imdb:{id}"))
        .or_else(|| entry["tvdb_id"].as_str().map(|id| format!("tvdb:{id}")))
        .or_else(|| entry["tmdb_id"].as_str().map(|id| format!("tmdb:{id}")))
        .or_else(|| entry["media_id"].as_i64().map(|id| format!("media:{id}")))
        .or_else(|| entry["id"].as_str().map(|id| format!("primary:{id}")))
}

fn push_unique(results: &mut Vec<Value>, seen: &mut HashSet<String>, entry: Value) -> bool {
    if let Some(key) = dedup_key(&entry) {
        if seen.insert(key) {
            results.push(entry);
            return true;
        }
    }
    false
}

fn tag_match(entry: &mut Value, source: &str) {
    if let Some(obj) = entry.as_object_mut() {
        obj.insert("source".to_string(), json!(source));
    }
}
