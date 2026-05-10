use chrono::{DateTime, Utc};
use sqlx::PgPool;

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
    media_type: String,
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
            media_type: r.media_type,
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
    m.type::text AS media_type,
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
    let mt = media_type_sql(media_type);
    let ep = episode_filter_sql(season, episode);
    let sql = format!(
        "SELECT {SELECT_COLS}
         AND EXISTS (
             SELECT 1 FROM media_external_id x
             WHERE x.media_id = m.id AND x.provider = 'imdb' AND x.external_id = $1
         ){mt}{ep}{GROUP_BY}
         LIMIT $2"
    );
    run(pool, &sql, imdb_id, limit).await
}

pub async fn search_by_tmdb(
    pool: &PgPool,
    tmdb_id: &str,
    media_type: Option<&str>,
    season: Option<i32>,
    episode: Option<i32>,
    limit: i64,
) -> Vec<TorznabRow> {
    let mt = media_type_sql(media_type);
    let ep = episode_filter_sql(season, episode);
    let sql = format!(
        "SELECT {SELECT_COLS}
         AND EXISTS (
             SELECT 1 FROM media_external_id x
             WHERE x.media_id = m.id AND x.provider = 'tmdb' AND x.external_id = $1
         ){mt}{ep}{GROUP_BY}
         LIMIT $2"
    );
    run(pool, &sql, tmdb_id, limit).await
}

pub async fn search_by_title(
    pool: &PgPool,
    query: &str,
    media_type: Option<&str>,
    limit: i64,
) -> Vec<TorznabRow> {
    let mt = media_type_sql(media_type);
    let pattern = format!("%{query}%");
    let sql = format!(
        "SELECT {SELECT_COLS}
         AND (m.title ILIKE $1 OR st.name ILIKE $1){mt}{GROUP_BY}
         LIMIT $2"
    );
    run(pool, &sql, &pattern, limit).await
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

fn media_type_sql(mt: Option<&str>) -> &'static str {
    match mt {
        Some("movie") => "\n  AND m.type = 'MOVIE'::mediatype",
        Some("series") => "\n  AND m.type = 'SERIES'::mediatype",
        _ => "",
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

async fn run(pool: &PgPool, sql: &str, param: &str, limit: i64) -> Vec<TorznabRow> {
    match sqlx::query_as::<_, Row>(sql)
        .bind(param)
        .bind(limit)
        .fetch_all(pool)
        .await
    {
        Ok(rows) => rows.into_iter().map(TorznabRow::from).collect(),
        Err(e) => {
            tracing::warn!("torznab db query failed: {e}");
            vec![]
        }
    }
}
