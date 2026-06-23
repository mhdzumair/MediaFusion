use chrono::{DateTime, Utc};
use sqlx::PgPool;

use super::types::MediaType;

/// A torrent row returned for Torznab search results.
pub struct TorznabRow {
    pub info_hash: String,
    pub name: String,
    pub total_size: Option<i64>,
    pub seeders: Option<i32>,
    pub leechers: Option<i32>,
    pub uploaded_at: Option<DateTime<Utc>>,
    pub resolution: Option<String>,
    pub media_type: String,
    pub imdb_id: Option<String>,
    pub tmdb_id: Option<String>,
    pub source: Option<String>,
    pub trackers: Vec<String>,
}

#[derive(sqlx::FromRow)]
struct Row {
    info_hash: String,
    name: String,
    total_size: Option<i64>,
    seeders: Option<i32>,
    leechers: Option<i32>,
    uploaded_at: Option<DateTime<Utc>>,
    resolution: Option<String>,
    media_type: MediaType,
    imdb_id: Option<String>,
    tmdb_id: Option<String>,
    source: Option<String>,
    trackers: Vec<String>,
}

impl From<Row> for TorznabRow {
    fn from(r: Row) -> Self {
        TorznabRow {
            info_hash: r.info_hash,
            name: r.name,
            total_size: r.total_size,
            seeders: r.seeders,
            leechers: r.leechers,
            uploaded_at: r.uploaded_at,
            resolution: r.resolution,
            media_type: r.media_type.as_wire().to_string(),
            imdb_id: r.imdb_id,
            tmdb_id: r.tmdb_id,
            source: r.source,
            trackers: r.trackers,
        }
    }
}

const SELECT_COLS: &str = r#"
    ts.info_hash,
    st.name,
    ts.total_size,
    ts.seeders,
    ts.leechers,
    COALESCE(ts.uploaded_at, st.created_at) AS uploaded_at,
    st.resolution,
    m.type AS media_type,
    MAX(CASE WHEN mei.provider = 'imdb' THEN mei.external_id END) AS imdb_id,
    MAX(CASE WHEN mei.provider = 'tmdb' THEN mei.external_id END) AS tmdb_id,
    st.source,
    COALESCE(
        ARRAY_AGG(DISTINCT tr.url ORDER BY tr.url) FILTER (WHERE tr.url IS NOT NULL),
        ARRAY[]::text[]
    ) AS trackers
FROM torrent_stream ts
JOIN stream st ON st.id = ts.stream_id
JOIN stream_media_link sml ON sml.stream_id = st.id
JOIN media m ON m.id = sml.media_id
LEFT JOIN media_external_id mei ON mei.media_id = m.id
LEFT JOIN torrent_tracker_link ttl ON ttl.torrent_id = ts.id
LEFT JOIN tracker tr ON tr.id = ttl.tracker_id
WHERE st.is_active = true
  AND st.is_blocked = false
  AND st.is_public = true"#;

const GROUP_BY: &str = r#"
GROUP BY ts.id, ts.info_hash, st.name, ts.total_size, ts.seeders, ts.leechers,
         ts.uploaded_at, st.created_at, st.resolution, m.type, st.source"#;

pub async fn search_by_imdb(
    pool: &PgPool,
    imdb_id: &str,
    media_type: Option<&str>,
    season: Option<i32>,
    episode: Option<i32>,
    limit: i64,
) -> Vec<TorznabRow> {
    let mt = parse_media_type_filter(media_type);
    let ep = episode_filter_sql(season, episode);
    let (mt_clause, limit_ph) = media_type_limit_placeholders(mt.is_some());
    let sql = format!(
        "SELECT {SELECT_COLS}
         AND EXISTS (
             SELECT 1 FROM media_external_id x
             WHERE x.media_id = m.id AND x.provider = 'imdb' AND x.external_id = $1
         ){mt_clause}{ep}{GROUP_BY}
         LIMIT {limit_ph}"
    );
    run(pool, &sql, imdb_id, mt, limit).await
}

pub async fn search_by_tmdb(
    pool: &PgPool,
    tmdb_id: &str,
    media_type: Option<&str>,
    season: Option<i32>,
    episode: Option<i32>,
    limit: i64,
) -> Vec<TorznabRow> {
    let mt = parse_media_type_filter(media_type);
    let ep = episode_filter_sql(season, episode);
    let (mt_clause, limit_ph) = media_type_limit_placeholders(mt.is_some());
    let sql = format!(
        "SELECT {SELECT_COLS}
         AND EXISTS (
             SELECT 1 FROM media_external_id x
             WHERE x.media_id = m.id AND x.provider = 'tmdb' AND x.external_id = $1
         ){mt_clause}{ep}{GROUP_BY}
         LIMIT {limit_ph}"
    );
    run(pool, &sql, tmdb_id, mt, limit).await
}

pub async fn search_by_title(
    pool: &PgPool,
    query: &str,
    media_type: Option<&str>,
    year: Option<i32>,
    limit: i64,
) -> Vec<TorznabRow> {
    let mt = parse_media_type_filter(media_type);
    let pattern = format!("%{query}%");

    let mut next_param = 2i32;
    let year_clause = if year.is_some() {
        let clause = format!("\n  AND m.year = ${next_param}");
        next_param += 1;
        clause
    } else {
        String::new()
    };
    let mt_clause = if mt.is_some() {
        let clause = format!("\n  AND m.type = ${next_param}");
        next_param += 1;
        clause
    } else {
        String::new()
    };
    let limit_ph = format!("${next_param}");

    // UNION splits the OR into two single-table ILIKE branches so each can use
    // its own GIN trigram index instead of forcing a cross-join seq-scan.
    let sql = format!(
        "(SELECT {SELECT_COLS}
          AND m.title ILIKE $1{year_clause}{mt_clause}{GROUP_BY})
         UNION
         (SELECT {SELECT_COLS}
          AND st.name ILIKE $1{year_clause}{mt_clause}{GROUP_BY})
         LIMIT {limit_ph}"
    );

    let mut q = sqlx::query_as::<_, Row>(sqlx::AssertSqlSafe(sql.as_str())).bind(&pattern);
    if let Some(y) = year {
        q = q.bind(y);
    }
    if let Some(mt_val) = mt {
        q = q.bind(mt_val);
    }
    match q.bind(limit).fetch_all(pool).await {
        Ok(rows) => rows.into_iter().map(TorznabRow::from).collect(),
        Err(e) => {
            tracing::warn!("torznab db query failed: {e}");
            vec![]
        }
    }
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

fn parse_media_type_filter(mt: Option<&str>) -> Option<MediaType> {
    mt.and_then(|s| MediaType::from_wire(&s.to_ascii_lowercase()))
}

fn media_type_limit_placeholders(has_mt: bool) -> (&'static str, &'static str) {
    if has_mt {
        ("\n  AND m.type = $2", "$3")
    } else {
        ("", "$2")
    }
}

fn episode_filter_sql(season: Option<i32>, episode: Option<i32>) -> String {
    if season.is_none() && episode.is_none() {
        return String::new();
    }
    let mut clauses = Vec::new();
    if let Some(s) = season {
        clauses.push(format!("fml.season_number = {s}"));
    }
    if let Some(e) = episode {
        clauses.push(format!(
            "(fml.episode_number = {e} OR (fml.episode_end IS NOT NULL \
             AND fml.episode_number <= {e} AND fml.episode_end >= {e}))"
        ));
    }
    format!(
        "\n  AND EXISTS (\
        \n      SELECT 1 FROM stream_file sf\
        \n      JOIN file_media_link fml ON fml.file_id = sf.id AND fml.media_id = m.id\
        \n      WHERE sf.stream_id = st.id AND {}\
        \n  )",
        clauses.join(" AND ")
    )
}

async fn run(
    pool: &PgPool,
    sql: &str,
    param: &str,
    media_type: Option<MediaType>,
    limit: i64,
) -> Vec<TorznabRow> {
    let mut q = sqlx::query_as::<_, Row>(sqlx::AssertSqlSafe(sql)).bind(param);
    if let Some(mt) = media_type {
        q = q.bind(mt);
    }
    match q.bind(limit).fetch_all(pool).await {
        Ok(rows) => rows.into_iter().map(TorznabRow::from).collect(),
        Err(e) => {
            tracing::warn!("torznab db query failed: {e}");
            vec![]
        }
    }
}
