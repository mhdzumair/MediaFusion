use serde_json::{json, Value};
use sqlx::PgPool;
use tracing::warn;

/// Fetch raw stream data for a set of media IDs (cold path).
/// Returns Vec of (media_id, JSON object with "torrents" key).
pub async fn fetch_streams_bulk(
    pool: &PgPool,
    media_ids: &[i64],
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> Result<Vec<(i64, Value)>, Box<dyn std::error::Error + Send + Sync>> {
    if media_ids.is_empty() {
        return Ok(vec![]);
    }

    let ids_i32: Vec<i32> = media_ids.iter().map(|&x| x as i32).collect();

    let rows: Vec<(i32, Value)> = match (media_type, season, episode) {
        ("series", Some(s), Some(e)) => sqlx::query_as(
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
                            'file_index', sf.file_index,
                            'filename',   sf.filename,
                            'is_public',  st.is_public
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
        .bind(&ids_i32)
        .bind(s)
        .bind(e)
        .fetch_all(pool)
        .await
        .unwrap_or_else(|e| {
            warn!("series streams query: {e}");
            vec![]
        }),

        _ => sqlx::query_as(
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
                            'is_public',  st.is_public
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
        .bind(&ids_i32)
        .fetch_all(pool)
        .await
        .unwrap_or_else(|e| {
            warn!("movie streams query: {e}");
            vec![]
        }),
    };

    Ok(rows.into_iter().map(|(id, v)| (id as i64, v)).collect())
}

/// Fetch usenet streams for a set of media IDs.
/// Returns Vec of (media_id, JSON array of usenet stream objects).
pub async fn fetch_usenet_streams_bulk(
    pool: &PgPool,
    media_ids: &[i64],
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> Vec<(i64, Vec<Value>)> {
    if media_ids.is_empty() {
        return vec![];
    }

    let ids_i32: Vec<i32> = media_ids.iter().map(|&x| x as i32).collect();

    // Series: join via file_media_link; movies: join via stream_media_link
    let rows: Vec<(i32, Value)> = match (media_type, season, episode) {
        ("series", Some(s), Some(e)) => sqlx::query_as(
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
        .bind(&ids_i32)
        .bind(s)
        .bind(e)
        .fetch_all(pool)
        .await
        .unwrap_or_else(|e| { warn!("usenet series streams query: {e}"); vec![] }),

        _ => sqlx::query_as(
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
        .bind(&ids_i32)
        .fetch_all(pool)
        .await
        .unwrap_or_else(|e| { warn!("usenet movie streams query: {e}"); vec![] }),
    };

    // Group by media_id
    let mut map: std::collections::HashMap<i64, Vec<Value>> = std::collections::HashMap::new();
    for (id, item) in rows {
        map.entry(id as i64).or_default().push(item);
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
    let indexer = row.get("indexer").and_then(|v| v.as_str()).unwrap_or("Usenet");
    let size = row.get("size").and_then(|v| v.as_i64());

    let label = if !quality.is_empty() { quality } else if !resolution.is_empty() { resolution } else { "Unknown" };

    let mut desc_parts: Vec<String> = Vec::new();
    let mut q_parts: Vec<&str> = Vec::new();
    if !quality.is_empty() { q_parts.push(quality); }
    if !resolution.is_empty() { q_parts.push(resolution); }
    if let Some(c) = codec.filter(|s| !s.is_empty()) { q_parts.push(c); }
    if !q_parts.is_empty() { desc_parts.push(format!("📺 {}", q_parts.join(" | "))); }
    if let Some(s) = size.filter(|&s| s > 0) { desc_parts.push(format!("💾 {}", readable_size(s))); }
    desc_parts.push(format!("🔗 {indexer}"));
    let description = desc_parts.join("\n");

    let url = match provider_info {
        Some((secret_str, provider)) => match (season, episode) {
            (Some(s), Some(e)) => format!(
                "{host_url}/streaming_provider/{secret_str}/usenet/{provider}/{nzb_guid}/{s}/{e}"
            ),
            _ => format!(
                "{host_url}/streaming_provider/{secret_str}/usenet/{provider}/{nzb_guid}"
            ),
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
}

/// Fetch stream playback info for the given info_hash.
/// Returns `None` if the hash is not in the DB.
pub async fn fetch_stream_playback_info(
    pool: &PgPool,
    info_hash: &str,
) -> Option<StreamPlaybackInfo> {
    let row: (String, Option<serde_json::Value>, Option<i32>, Option<String>) = sqlx::query_as(
        r#"
        SELECT
            st.name,
            ts.announce_list,
            (SELECT sf.file_index
             FROM stream_file sf
             WHERE sf.stream_id = st.id
             ORDER BY sf.file_index ASC NULLS LAST
             LIMIT 1) AS file_index,
            (SELECT sf.filename
             FROM stream_file sf
             WHERE sf.stream_id = st.id
             ORDER BY sf.file_index ASC NULLS LAST
             LIMIT 1) AS filename
        FROM torrent_stream ts
        JOIN stream st ON st.id = ts.stream_id
        WHERE ts.info_hash = $1
        "#,
    )
    .bind(info_hash)
    .fetch_optional(pool)
    .await
    .unwrap_or(None)?;

    let announce_list: Vec<String> = row.1
        .and_then(|v| serde_json::from_value(v).ok())
        .unwrap_or_default();

    Some(StreamPlaybackInfo {
        name: row.0,
        announce_list,
        file_index: row.2,
        filename: row.3,
    })
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
