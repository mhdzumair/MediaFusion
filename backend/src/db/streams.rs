use serde_json::{json, Value};
use sqlx::PgPool;
use tracing::warn;

use super::types::{LinkSource, MediaId, MediaType, StreamId};

fn is_series_episode(media_type: &str, season: Option<i32>, episode: Option<i32>) -> bool {
    matches!(
        (MediaType::from_wire(media_type), season, episode),
        (Some(MediaType::Series), Some(_), Some(_))
    )
}

/// Fetch raw stream data for a set of media IDs (cold path).
/// Returns Vec of (media_id, JSON object with "torrents" key).
pub async fn fetch_streams_bulk(
    pool: &PgPool,
    media_ids: &[MediaId],
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> Result<Vec<(MediaId, Value)>, Box<dyn std::error::Error + Send + Sync>> {
    if media_ids.is_empty() {
        return Ok(vec![]);
    }

    let rows: Vec<(MediaId, Value)> = if is_series_episode(media_type, season, episode) {
        let (s, e) = (season.unwrap(), episode.unwrap());
        sqlx::query_as(
            r#"
            SELECT
                fml.media_id,
                jsonb_build_object(
                    'torrents', COALESCE(jsonb_agg(
                        jsonb_build_object(
                            'name',       st.name,
                            'info_hash',  ts.info_hash,
                            'quality',    st.quality,
                            'resolution', st.resolution,
                            'codec',      st.codec,
                            'source',     st.source,
                            'seeders',    ts.seeders,
                            'size',       ts.total_size,
                            'torrent_type', ts.torrent_type,
                            'file_index', sf.file_index,
                            'filename',   sf.filename,
                            'is_public',  st.is_public,
                            'created_at', st.created_at,
                            'languages',  COALESCE((
                                SELECT jsonb_agg(l.name ORDER BY l.name)
                                FROM stream_language_link sll
                                JOIN language l ON l.id = sll.language_id
                                WHERE sll.stream_id = st.id AND sll.language_type = 'AUDIO'
                            ), '[]'::jsonb)
                        ) ORDER BY ts.seeders DESC NULLS LAST
                    ) FILTER (WHERE ts.info_hash IS NOT NULL AND st.is_active AND NOT st.is_blocked), '[]')
                ) AS data
            FROM file_media_link fml
            JOIN stream_file sf ON sf.id = fml.file_id
            JOIN stream st ON st.id = sf.stream_id
            JOIN torrent_stream ts ON ts.stream_id = st.id
            WHERE fml.media_id = ANY($1)
              AND fml.season_number = $2
              AND fml.episode_number = $3
            GROUP BY fml.media_id
            "#,
        )
        .bind(media_ids)
        .bind(s)
        .bind(e)
        .fetch_all(pool)
        .await
        .unwrap_or_else(|e| {
            warn!("series streams query: {e}");
            vec![]
        })
    } else {
        sqlx::query_as(
            r#"
            SELECT
                sml.media_id,
                jsonb_build_object(
                    'torrents', COALESCE(jsonb_agg(
                        jsonb_build_object(
                            'name',       st.name,
                            'info_hash',  ts.info_hash,
                            'quality',    st.quality,
                            'resolution', st.resolution,
                            'codec',      st.codec,
                            'source',     st.source,
                            'seeders',    ts.seeders,
                            'size',       ts.total_size,
                            'torrent_type', ts.torrent_type,
                            'is_public',  st.is_public,
                            'created_at', st.created_at,
                            'languages',  COALESCE((
                                SELECT jsonb_agg(l.name ORDER BY l.name)
                                FROM stream_language_link sll
                                JOIN language l ON l.id = sll.language_id
                                WHERE sll.stream_id = st.id AND sll.language_type = 'AUDIO'
                            ), '[]'::jsonb)
                        ) ORDER BY ts.seeders DESC NULLS LAST
                    ) FILTER (WHERE ts.info_hash IS NOT NULL AND st.is_active AND NOT st.is_blocked), '[]')
                ) AS data
            FROM stream_media_link sml
            JOIN stream st ON st.id = sml.stream_id
            JOIN torrent_stream ts ON ts.stream_id = st.id
            WHERE sml.media_id = ANY($1)
            GROUP BY sml.media_id
            "#,
        )
        .bind(media_ids)
        .fetch_all(pool)
        .await
        .unwrap_or_else(|e| {
            warn!("movie streams query: {e}");
            vec![]
        })
    };

    Ok(rows)
}

/// Fetch usenet streams for a set of media IDs.
/// Returns Vec of (media_id, JSON array of usenet stream objects).
pub async fn fetch_usenet_streams_bulk(
    pool: &PgPool,
    media_ids: &[MediaId],
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> Vec<(MediaId, Vec<Value>)> {
    if media_ids.is_empty() {
        return vec![];
    }

    // Series: join via file_media_link; movies: join via stream_media_link
    let rows: Vec<(MediaId, Value)> = if is_series_episode(media_type, season, episode) {
        let (s, e) = (season.unwrap(), episode.unwrap());
        sqlx::query_as(
            r#"
            SELECT
                fml.media_id,
                jsonb_build_object(
                    'name',       st.name,
                    'nzb_guid',   us.nzb_guid,
                    'nzb_url',    us.nzb_url,
                    'quality',    st.quality,
                    'resolution', st.resolution,
                    'codec',      st.codec,
                    'source',     st.source,
                    'size',       us.size,
                    'indexer',    us.indexer
                ) AS item
            FROM file_media_link fml
            JOIN stream_file sf ON sf.id = fml.file_id
            JOIN stream st ON st.id = sf.stream_id
            JOIN usenet_stream us ON us.stream_id = st.id
            WHERE fml.media_id = ANY($1)
              AND fml.season_number = $2
              AND fml.episode_number = $3
              AND st.is_active = true
              AND st.is_blocked = false
            ORDER BY us.size DESC
            "#,
        )
        .bind(media_ids)
        .bind(s)
        .bind(e)
        .fetch_all(pool)
        .await
        .unwrap_or_else(|e| {
            warn!("usenet series streams query: {e}");
            vec![]
        })
    } else {
        sqlx::query_as(
            r#"
            SELECT
                sml.media_id,
                jsonb_build_object(
                    'name',       st.name,
                    'nzb_guid',   us.nzb_guid,
                    'nzb_url',    us.nzb_url,
                    'quality',    st.quality,
                    'resolution', st.resolution,
                    'codec',      st.codec,
                    'source',     st.source,
                    'size',       us.size,
                    'indexer',    us.indexer
                ) AS item
            FROM stream_media_link sml
            JOIN stream st ON st.id = sml.stream_id
            JOIN usenet_stream us ON us.stream_id = st.id
            WHERE sml.media_id = ANY($1)
              AND st.is_active = true
              AND st.is_blocked = false
            ORDER BY us.size DESC
            "#,
        )
        .bind(media_ids)
        .fetch_all(pool)
        .await
        .unwrap_or_else(|e| {
            warn!("usenet movie streams query: {e}");
            vec![]
        })
    };

    // Group by media_id
    let mut map: std::collections::HashMap<MediaId, Vec<Value>> = std::collections::HashMap::new();
    for (id, item) in rows {
        map.entry(id).or_default().push(item);
    }
    map.into_iter().collect()
}

/// Fetch HTTP streams for a set of media IDs.
pub async fn fetch_http_streams_bulk(
    pool: &PgPool,
    media_ids: &[MediaId],
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> Vec<(MediaId, Vec<Value>)> {
    if media_ids.is_empty() {
        return vec![];
    }
    let rows: Vec<(MediaId, Value)> = if is_series_episode(media_type, season, episode) {
        let (s, e) = (season.unwrap(), episode.unwrap());
        sqlx::query_as(
            r#"
            SELECT fml.media_id,
                jsonb_build_object(
                    'name', st.name, 'url', hs.url, 'format', hs.format,
                    'quality', st.quality, 'resolution', st.resolution,
                    'codec', st.codec, 'source', st.source,
                    'size', hs.size, 'behavior_hints', hs.behavior_hints,
                    'languages', COALESCE((
                        SELECT jsonb_agg(l.name ORDER BY l.name)
                        FROM stream_language_link sll JOIN language l ON l.id = sll.language_id
                        WHERE sll.stream_id = st.id AND sll.language_type = 'AUDIO'
                    ), '[]'::jsonb)
                ) AS item
            FROM file_media_link fml
            JOIN stream_file sf ON sf.id = fml.file_id
            JOIN stream st ON st.id = sf.stream_id
            JOIN http_stream hs ON hs.stream_id = st.id
            WHERE fml.media_id = ANY($1) AND fml.season_number = $2 AND fml.episode_number = $3
              AND st.is_active AND NOT st.is_blocked
        "#,
        )
        .bind(media_ids)
        .bind(s)
        .bind(e)
        .fetch_all(pool)
        .await
    } else {
        sqlx::query_as(
            r#"
            SELECT sml.media_id,
                jsonb_build_object(
                    'name', st.name, 'url', hs.url, 'format', hs.format,
                    'quality', st.quality, 'resolution', st.resolution,
                    'codec', st.codec, 'source', st.source,
                    'size', hs.size, 'behavior_hints', hs.behavior_hints,
                    'languages', COALESCE((
                        SELECT jsonb_agg(l.name ORDER BY l.name)
                        FROM stream_language_link sll JOIN language l ON l.id = sll.language_id
                        WHERE sll.stream_id = st.id AND sll.language_type = 'AUDIO'
                    ), '[]'::jsonb)
                ) AS item
            FROM stream_media_link sml
            JOIN stream st ON st.id = sml.stream_id
            JOIN http_stream hs ON hs.stream_id = st.id
            WHERE sml.media_id = ANY($1) AND st.is_active AND NOT st.is_blocked
        "#,
        )
        .bind(media_ids)
        .fetch_all(pool)
        .await
    }
    .unwrap_or_else(|e| {
        warn!("http streams query: {e}");
        vec![]
    });
    let mut map: std::collections::HashMap<MediaId, Vec<Value>> = std::collections::HashMap::new();
    for (id, item) in rows {
        map.entry(id).or_default().push(item);
    }
    map.into_iter().collect()
}

/// Fetch YouTube streams for a set of media IDs.
pub async fn fetch_youtube_streams_bulk(
    pool: &PgPool,
    media_ids: &[MediaId],
) -> Vec<(MediaId, Vec<Value>)> {
    if media_ids.is_empty() {
        return vec![];
    }
    let rows: Vec<(MediaId, Value)> = sqlx::query_as(
        r#"
        SELECT sml.media_id,
            jsonb_build_object(
                'name', st.name, 'video_id', ys.video_id,
                'quality', st.quality, 'resolution', st.resolution,
                'codec', st.codec, 'source', st.source,
                'is_live', ys.is_live,
                'geo_restriction_type', ys.geo_restriction_type,
                'geo_restriction_countries', ys.geo_restriction_countries,
                'languages', COALESCE((
                    SELECT jsonb_agg(l.name ORDER BY l.name)
                    FROM stream_language_link sll JOIN language l ON l.id = sll.language_id
                    WHERE sll.stream_id = st.id AND sll.language_type = 'AUDIO'
                ), '[]'::jsonb)
            ) AS item
        FROM stream_media_link sml
        JOIN stream st ON st.id = sml.stream_id
        JOIN youtube_stream ys ON ys.stream_id = st.id
        WHERE sml.media_id = ANY($1) AND st.is_active AND NOT st.is_blocked
    "#,
    )
    .bind(media_ids)
    .fetch_all(pool)
    .await
    .unwrap_or_else(|e| {
        warn!("youtube streams query: {e}");
        vec![]
    });
    let mut map: std::collections::HashMap<MediaId, Vec<Value>> = std::collections::HashMap::new();
    for (id, item) in rows {
        map.entry(id).or_default().push(item);
    }
    map.into_iter().collect()
}

/// Fetch Telegram streams for a set of media IDs.
pub async fn fetch_telegram_streams_bulk(
    pool: &PgPool,
    media_ids: &[MediaId],
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> Vec<(MediaId, Vec<Value>)> {
    if media_ids.is_empty() {
        return vec![];
    }
    let rows: Vec<(MediaId, Value)> = if is_series_episode(media_type, season, episode) {
        let (s, e) = (season.unwrap(), episode.unwrap());
        sqlx::query_as(
            r#"
            SELECT fml.media_id,
                jsonb_build_object(
                    'name', st.name, 'chat_id', ts.chat_id, 'message_id', ts.message_id,
                    'file_name', ts.file_name, 'size', ts.size,
                    'quality', st.quality, 'resolution', st.resolution,
                    'codec', st.codec, 'source', st.source,
                    'languages', COALESCE((
                        SELECT jsonb_agg(l.name ORDER BY l.name)
                        FROM stream_language_link sll JOIN language l ON l.id = sll.language_id
                        WHERE sll.stream_id = st.id AND sll.language_type = 'AUDIO'
                    ), '[]'::jsonb)
                ) AS item
            FROM file_media_link fml
            JOIN stream_file sf ON sf.id = fml.file_id
            JOIN stream st ON st.id = sf.stream_id
            JOIN telegram_stream ts ON ts.stream_id = st.id
            WHERE fml.media_id = ANY($1) AND fml.season_number = $2 AND fml.episode_number = $3
              AND st.is_active AND NOT st.is_blocked
        "#,
        )
        .bind(media_ids)
        .bind(s)
        .bind(e)
        .fetch_all(pool)
        .await
    } else {
        sqlx::query_as(
            r#"
            SELECT sml.media_id,
                jsonb_build_object(
                    'name', st.name, 'chat_id', ts.chat_id, 'message_id', ts.message_id,
                    'file_name', ts.file_name, 'size', ts.size,
                    'quality', st.quality, 'resolution', st.resolution,
                    'codec', st.codec, 'source', st.source,
                    'languages', COALESCE((
                        SELECT jsonb_agg(l.name ORDER BY l.name)
                        FROM stream_language_link sll JOIN language l ON l.id = sll.language_id
                        WHERE sll.stream_id = st.id AND sll.language_type = 'AUDIO'
                    ), '[]'::jsonb)
                ) AS item
            FROM stream_media_link sml
            JOIN stream st ON st.id = sml.stream_id
            JOIN telegram_stream ts ON ts.stream_id = st.id
            WHERE sml.media_id = ANY($1) AND st.is_active AND NOT st.is_blocked
        "#,
        )
        .bind(media_ids)
        .fetch_all(pool)
        .await
    }
    .unwrap_or_else(|e| {
        warn!("telegram streams query: {e}");
        vec![]
    });
    let mut map: std::collections::HashMap<MediaId, Vec<Value>> = std::collections::HashMap::new();
    for (id, item) in rows {
        map.entry(id).or_default().push(item);
    }
    map.into_iter().collect()
}

/// Fetch AceStream streams for a set of media IDs.
pub async fn fetch_acestream_streams_bulk(
    pool: &PgPool,
    media_ids: &[MediaId],
) -> Vec<(MediaId, Vec<Value>)> {
    if media_ids.is_empty() {
        return vec![];
    }
    let rows: Vec<(MediaId, Value)> = sqlx::query_as(
        r#"
        SELECT sml.media_id,
            jsonb_build_object(
                'name', st.name, 'content_id', ace.content_id, 'info_hash', ace.info_hash,
                'quality', st.quality, 'resolution', st.resolution,
                'codec', st.codec, 'source', st.source,
                'languages', COALESCE((
                    SELECT jsonb_agg(l.name ORDER BY l.name)
                    FROM stream_language_link sll JOIN language l ON l.id = sll.language_id
                    WHERE sll.stream_id = st.id AND sll.language_type = 'AUDIO'
                ), '[]'::jsonb)
            ) AS item
        FROM stream_media_link sml
        JOIN stream st ON st.id = sml.stream_id
        JOIN acestream_stream ace ON ace.stream_id = st.id
        WHERE sml.media_id = ANY($1) AND st.is_active AND NOT st.is_blocked
    "#,
    )
    .bind(media_ids)
    .fetch_all(pool)
    .await
    .unwrap_or_else(|e| {
        warn!("acestream streams query: {e}");
        vec![]
    });
    let mut map: std::collections::HashMap<MediaId, Vec<Value>> = std::collections::HashMap::new();
    for (id, item) in rows {
        map.entry(id).or_default().push(item);
    }
    map.into_iter().collect()
}

/// Build a Stremio stream object for a usenet row.
///
/// `provider_info`: `Some((secret_str, provider_name))` for authenticated users with a debrid
/// provider — generates a provider-scoped URL; `None` falls back to the public `/usenet/{guid}` URL.
pub fn usenet_row_to_stremio(
    row: &Value,
    host_url: &str,
    addon_name: &str,
    provider_info: Option<(&str, &str)>,
    season: Option<i32>,
    episode: Option<i32>,
) -> Option<Value> {
    let name = row.get("name")?.as_str()?;
    let nzb_guid = row.get("nzb_guid")?.as_str()?;
    let quality = row.get("quality").and_then(|v| v.as_str()).unwrap_or("");
    let resolution = row.get("resolution").and_then(|v| v.as_str()).unwrap_or("");
    let codec = row.get("codec").and_then(|v| v.as_str());
    let indexer = row
        .get("indexer")
        .and_then(|v| v.as_str())
        .unwrap_or("Usenet");
    let size = row.get("size").and_then(|v| v.as_i64());

    let label = if !quality.is_empty() {
        quality
    } else if !resolution.is_empty() {
        resolution
    } else {
        "Unknown"
    };

    let mut desc_parts: Vec<String> = Vec::new();
    let mut q_parts: Vec<&str> = Vec::new();
    if !quality.is_empty() {
        q_parts.push(quality);
    }
    if !resolution.is_empty() {
        q_parts.push(resolution);
    }
    if let Some(c) = codec.filter(|s| !s.is_empty()) {
        q_parts.push(c);
    }
    if !q_parts.is_empty() {
        desc_parts.push(format!("📺 {}", q_parts.join(" | ")));
    }
    if let Some(s) = size.filter(|&s| s > 0) {
        desc_parts.push(format!("💾 {}", readable_size(s)));
    }
    desc_parts.push(format!("🔗 {indexer}"));
    let description = desc_parts.join("\n");

    let url = match provider_info {
        Some((secret_str, provider)) => match (season, episode) {
            (Some(s), Some(e)) => format!(
                "{host_url}/streaming_provider/{secret_str}/usenet/{provider}/{nzb_guid}/{s}/{e}"
            ),
            _ => format!("{host_url}/streaming_provider/{secret_str}/usenet/{provider}/{nzb_guid}"),
        },
        None => format!("{host_url}/usenet/{nzb_guid}"),
    };

    Some(json!({
        "name": name,
        "description": description,
        "url": url,
        "behaviorHints": {
            "notWebReady": false,
            "bingeGroup": format!("{addon_name}-{label}-{resolution}"),
            "videoSize": size
        }
    }))
}

/// Minimal stream info needed for playback proxy.
pub struct StreamPlaybackInfo {
    /// The stream name (used in the `name` field of Stremio stream objects).
    pub name: String,
    /// Tracker URLs (announce list) for magnet link construction.
    pub announce_list: Vec<String>,
    /// Optional file index hint from the DB.
    pub file_index: Option<i32>,
    /// Optional filename hint (base name of the torrent file).
    pub filename: Option<String>,
    /// True when no stream_file rows exist at all (metadata not yet stored).
    pub has_no_files: bool,
    /// Total torrent size in bytes (from torrent_stream.total_size), if known.
    pub size_bytes: Option<i64>,
    /// Raw .torrent bytes for private/semi-private streams (when stored in DB).
    pub torrent_file: Option<Vec<u8>>,
}

/// Fetch stream playback info for the given info_hash.
/// When season/episode are provided, picks the matching stream_file via file_media_link.
/// Returns `None` if the hash is not in the DB.
#[allow(clippy::type_complexity)]
pub async fn fetch_stream_playback_info(
    pool: &PgPool,
    info_hash: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> Option<StreamPlaybackInfo> {
    let row: (
        String,
        Option<Vec<String>>,
        Option<i32>,
        Option<String>,
        Option<i64>,
        Option<i64>,
        Option<Vec<u8>>,
    ) = match (season, episode) {
        (Some(s), Some(e)) => {
            sqlx::query_as(
                r#"
                SELECT
                    st.name,
                    ARRAY_AGG(DISTINCT t.url) FILTER (WHERE t.url IS NOT NULL) AS announce_list,
                    sf.file_index,
                    sf.filename,
                    COUNT(sf2.id) AS total_files,
                    ts.total_size,
                    ts.torrent_file
                FROM torrent_stream ts
                JOIN stream st ON st.id = ts.stream_id
                LEFT JOIN torrent_tracker_link ttl ON ttl.torrent_id = ts.id
                LEFT JOIN tracker t ON t.id = ttl.tracker_id
                LEFT JOIN stream_file sf2 ON sf2.stream_id = st.id
                LEFT JOIN (
                    SELECT sf_inner.id, sf_inner.stream_id, sf_inner.file_index, sf_inner.filename
                    FROM stream_file sf_inner
                    JOIN file_media_link fml ON fml.file_id = sf_inner.id
                    WHERE fml.season_number = $2 AND fml.episode_number = $3
                    LIMIT 1
                ) sf ON sf.stream_id = st.id
                WHERE ts.info_hash = $1
                GROUP BY st.id, st.name, sf.file_index, sf.filename, ts.total_size, ts.torrent_file
                "#,
            )
            .bind(info_hash)
            .bind(s)
            .bind(e)
            .fetch_optional(pool)
            .await
        }

        _ => {
            sqlx::query_as(
                r#"
                SELECT
                    st.name,
                    ARRAY_AGG(DISTINCT t.url) FILTER (WHERE t.url IS NOT NULL) AS announce_list,
                    (SELECT sf.file_index
                     FROM stream_file sf
                     WHERE sf.stream_id = st.id
                     ORDER BY sf.file_index ASC NULLS LAST
                     LIMIT 1) AS file_index,
                    (SELECT sf.filename
                     FROM stream_file sf
                     WHERE sf.stream_id = st.id
                     ORDER BY sf.file_index ASC NULLS LAST
                     LIMIT 1) AS filename,
                    (SELECT COUNT(*) FROM stream_file sf WHERE sf.stream_id = st.id) AS total_files,
                    ts.total_size,
                    ts.torrent_file
                FROM torrent_stream ts
                JOIN stream st ON st.id = ts.stream_id
                LEFT JOIN torrent_tracker_link ttl ON ttl.torrent_id = ts.id
                LEFT JOIN tracker t ON t.id = ttl.tracker_id
                WHERE ts.info_hash = $1
                GROUP BY st.id, st.name, ts.total_size, ts.torrent_file
                "#,
            )
            .bind(info_hash)
            .fetch_optional(pool)
            .await
        }
    }
    .inspect_err(|e| tracing::error!("fetch_stream_playback_info error hash={info_hash}: {e}"))
    .unwrap_or(None)?;

    let total_files = row.4.unwrap_or(0);
    Some(StreamPlaybackInfo {
        name: row.0,
        announce_list: row.1.unwrap_or_default(),
        file_index: row.2,
        filename: row.3,
        has_no_files: total_files == 0,
        size_bytes: row.5.filter(|&s| s > 0),
        torrent_file: row.6.filter(|bytes| !bytes.is_empty()),
    })
}

/// A single file entry to store for a torrent.
pub struct TorrentFileEntry {
    pub file_index: i32,
    pub filename: String,
    pub size: i64,
    pub season: Option<i32>,
    pub episode: Option<i32>,
}

/// Insert stream_file rows (and file_media_link rows for series) for a torrent.
/// Idempotent — skips if files already exist for this stream.
pub async fn upsert_stream_files(
    pool: &PgPool,
    info_hash: &str,
    files: &[TorrentFileEntry],
) -> Result<(), sqlx::Error> {
    super::stream_store::upsert_torrent_files_by_hash(
        pool,
        info_hash,
        files,
        LinkSource::TorrentMetadata,
    )
    .await
}

/// Fetch HTTP streams for a single media_id (used for live TV playback).
pub async fn fetch_tv_streams_for_media(pool: &PgPool, media_id: MediaId) -> Vec<Value> {
    let rows: Vec<(Value,)> = sqlx::query_as(
        r#"
        SELECT
            jsonb_build_object(
                'name', st.name,
                'url', hs.url,
                'format', hs.format,
                'quality', st.quality,
                'resolution', st.resolution,
                'codec', st.codec,
                'source', st.source,
                'size', hs.size,
                'behavior_hints', hs.behavior_hints,
                'languages', COALESCE((
                    SELECT jsonb_agg(l.name ORDER BY l.name)
                    FROM stream_language_link sll JOIN language l ON l.id = sll.language_id
                    WHERE sll.stream_id = st.id AND sll.language_type = 'AUDIO'
                ), '[]'::jsonb)
            ) AS item
        FROM stream_media_link sml
        JOIN stream st ON st.id = sml.stream_id
        JOIN http_stream hs ON hs.stream_id = st.id
        WHERE sml.media_id = $1
          AND st.is_active = true
          AND st.is_blocked = false
        ORDER BY st.created_at DESC
        "#,
    )
    .bind(media_id)
    .fetch_all(pool)
    .await
    .unwrap_or_else(|e| {
        warn!("tv streams query media_id={media_id}: {e}");
        vec![]
    });

    rows.into_iter().map(|(v,)| v).collect()
}

/// Return which info_hashes from the provided list already exist in the DB.
pub async fn filter_existing_hashes(pool: &PgPool, hashes: &[String]) -> Vec<String> {
    if hashes.is_empty() {
        return vec![];
    }
    let rows: Vec<(String,)> =
        sqlx::query_as("SELECT info_hash FROM torrent_stream WHERE info_hash = ANY($1)")
            .bind(hashes)
            .fetch_all(pool)
            .await
            .unwrap_or_default();
    rows.into_iter().map(|(h,)| h).collect()
}

/// Link announce tracker URLs to a torrent via `stream.id`.
pub async fn link_torrent_trackers(
    pool: &PgPool,
    stream_id: StreamId,
    tracker_urls: &[String],
) -> Result<(), sqlx::Error> {
    let torrent_id: Option<i32> =
        sqlx::query_scalar("SELECT id FROM torrent_stream WHERE stream_id = $1 LIMIT 1")
            .bind(stream_id.0)
            .fetch_optional(pool)
            .await?;
    let Some(torrent_id) = torrent_id else {
        return Ok(());
    };

    let urls: Vec<String> = tracker_urls
        .iter()
        .map(|u| u.trim())
        .filter(|u| !u.is_empty())
        .map(str::to_string)
        .collect();
    if urls.is_empty() {
        return Ok(());
    }

    sqlx::query(
        r#"
        WITH input AS (SELECT unnest($1::text[]) AS url)
        INSERT INTO tracker(url)
        SELECT url FROM input
        ON CONFLICT(url) DO NOTHING
        "#,
    )
    .bind(&urls)
    .execute(pool)
    .await?;

    sqlx::query(
        r#"
        INSERT INTO torrent_tracker_link(torrent_id, tracker_id)
        SELECT $1, t.id
        FROM tracker t
        WHERE t.url = ANY($2::text[])
        ON CONFLICT DO NOTHING
        "#,
    )
    .bind(torrent_id)
    .bind(&urls)
    .execute(pool)
    .await?;

    Ok(())
}

fn readable_size(bytes: i64) -> String {
    const GB: i64 = 1_073_741_824;
    const MB: i64 = 1_048_576;
    if bytes >= GB {
        format!("{:.2} GB", bytes as f64 / GB as f64)
    } else if bytes >= MB {
        format!("{:.0} MB", bytes as f64 / MB as f64)
    } else {
        format!("{bytes} B")
    }
}
