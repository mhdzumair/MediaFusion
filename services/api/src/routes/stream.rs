use std::sync::Arc;

use axum::{
    extract::{Path, State},
    http::StatusCode,
    response::{IntoResponse, Json},
};
use serde_json::{json, Value};

use crate::{
    cache::{codec, stream_cache},
    crypto,
    db,
    models::user_data::UserData,
    scrapers::orchestrator,
    state::AppState,
};

use urlencoding;

// ─── Route handlers ────────────────────────────────────────────────────────────

pub async fn public_movie(
    Path(video_id): Path<String>,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    let imdb_id = video_id.trim_end_matches(".json").to_string();
    dispatch(state, String::new(), imdb_id, "movie", None, None).await
}

pub async fn public_series(
    Path(video_id): Path<String>,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    let raw = video_id.trim_end_matches(".json");
    let parts: Vec<&str> = raw.splitn(3, ':').collect();
    if parts.len() != 3 || parts[0].is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "invalid video_id"})),
        )
            .into_response();
    }
    let imdb_id = parts[0].to_string();
    let season: i32 = parts[1].parse().unwrap_or(1);
    let episode: i32 = parts[2].parse().unwrap_or(1);
    dispatch(state, String::new(), imdb_id, "series", Some(season), Some(episode)).await
}

pub async fn movie(
    Path((secret_str, video_id)): Path<(String, String)>,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    let imdb_id = video_id.trim_end_matches(".json").to_string();
    dispatch(state, secret_str, imdb_id, "movie", None, None).await
}

pub async fn series(
    Path((secret_str, video_id)): Path<(String, String)>,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    let raw = video_id.trim_end_matches(".json");
    let parts: Vec<&str> = raw.splitn(3, ':').collect();
    if parts.len() != 3 || parts[0].is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "invalid video_id"})),
        )
            .into_response();
    }
    let imdb_id = parts[0].to_string();
    let season: i32 = parts[1].parse().unwrap_or(1);
    let episode: i32 = parts[2].parse().unwrap_or(1);
    dispatch(state, secret_str, imdb_id, "series", Some(season), Some(episode)).await
}

// ─── Core orchestration ────────────────────────────────────────────────────────

async fn dispatch(
    state: Arc<AppState>,
    secret_str: String,
    imdb_id: String,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> axum::response::Response {
    match resolve(&state, &secret_str, &imdb_id, media_type, season, episode).await {
        Ok(streams) => Json(json!({"streams": streams})).into_response(),
        Err(e) => {
            tracing::warn!("stream error imdb={imdb_id} type={media_type}: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": e.to_string()})),
            )
                .into_response()
        }
    }
}

pub async fn resolve(
    state: &AppState,
    secret_str: &str,
    imdb_id: &str,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> Result<Vec<Value>, Box<dyn std::error::Error + Send + Sync>> {
    // 1. Decrypt user config → parse into UserData → derive scope
    let raw_user_data = crypto::resolve_user_data(secret_str, &state.config.secret_key, &state.pool, &state.redis).await;
    let user_data: UserData = serde_json::from_value(raw_user_data).unwrap_or_default();

    // 2. api_password gate — mirrors Python's @auth_required + middleware check.
    // When the instance has API_PASSWORD set, the user's config must contain the matching password.
    if let Some(ref required) = state.config.api_password {
        let provided = user_data.api_password.as_deref().unwrap_or("");
        if provided != required.as_str() {
            let error_video = if let Some(ref base) = state.config.python_proxy_url {
                format!("{base}/static/exceptions/invalid_config.mp4")
            } else {
                format!("{}/static/exceptions/invalid_config.mp4", state.config.host_url)
            };
            return Ok(vec![json!({
                "name": state.config.addon_name,
                "description": "Unauthorized.\nInvalid MediaFusion configuration.\nDelete the Invalid MediaFusion installed addon and reconfigure it.",
                "url": error_video,
                "behaviorHints": { "notWebReady": true }
            })]);
        }
    }
    let scope = match user_data.user_id {
        Some(id) if id > 0 => format!("user:{id}"),
        _ => "public".into(),
    };

    // 2. Resolve imdb_id → (primary_id, related_ids) via L1 moka, L2 Redis, L3 DB
    let cache_key = format!("{imdb_id}:{media_type}");
    let (media_id, related_ids) = if let Some(ids) = state.id_cache.get(&cache_key).await {
        ids
    } else {
        let ids = db::resolve_media_ids(&state.pool, imdb_id, media_type).await?;
        state.id_cache.insert(cache_key, ids.clone()).await;
        ids
    };

    if media_id == 0 && related_ids.is_empty() {
        return Ok(vec![]);
    }

    // 3. Build unique ID list (primary first) + Redis keys
    let mut all_ids: Vec<i64> = std::iter::once(media_id)
        .chain(related_ids.iter().copied())
        .collect();
    all_ids.dedup();

    let redis_keys: Vec<String> = all_ids
        .iter()
        .map(|id| stream_key(*id, media_type, season, episode, &scope))
        .collect();

    // 4. Redis MGET (warm path)
    let blobs = stream_cache::mget(&state.redis, &redis_keys).await?;

    let mut all_torrents: Vec<Value> = Vec::new();
    let mut misses: Vec<i64> = Vec::new();

    for (idx, blob_opt) in blobs.into_iter().enumerate() {
        match blob_opt {
            Some(blob) => match codec::decode_blob(&blob) {
                Some(decoded) => {
                    if let Some(arr) = decoded.get("torrents").and_then(|v| v.as_array()) {
                        all_torrents.extend(arr.iter().cloned());
                    }
                }
                None => misses.push(all_ids[idx]),
            },
            None => misses.push(all_ids[idx]),
        }
    }

    // 5. Cold path: DB query + Redis writeback
    if !misses.is_empty() {
        let fetched =
            db::fetch_streams_bulk(&state.pool, &misses, media_type, season, episode).await?;
        for (miss_id, raw_data) in &fetched {
            if let Some(arr) = raw_data.get("torrents").and_then(|v| v.as_array()) {
                all_torrents.extend(arr.iter().cloned());
            }
            let key = stream_key(*miss_id, media_type, season, episode, &scope);
            if let Some(blob) = codec::encode_blob(raw_data) {
                let _ = stream_cache::set_with_ttl(&state.redis, &key, blob, 900).await;
            }
        }
    }

    // 6. Fetch usenet streams from DB for the same media IDs
    let usenet_rows: Vec<serde_json::Value> = {
        let usenet_map =
            db::fetch_usenet_streams_bulk(&state.pool_ro, &all_ids, media_type, season, episode).await;
        usenet_map
            .into_iter()
            .flat_map(|(_, rows)| rows)
            .collect()
    };

    // Determine primary provider for debrid URL generation
    let primary_provider = user_data.streaming_providers.first().map(|p| p.service.as_str());
    let has_provider = !secret_str.is_empty() && primary_provider.is_some();

    // 7. Live scrape when DB has no results and user/global setting allows it.
    let mut live_usenet: Vec<serde_json::Value> = Vec::new();
    if all_torrents.is_empty() && usenet_rows.is_empty()
        && user_data.live_search_streams && state.config.live_search_streams
    {
        if let Ok(Some(meta)) =
            db::get_media_meta(&state.pool, media_id, imdb_id).await
        {
            let (scraped_torrents, scraped_usenet) = tokio::join!(
                orchestrator::run(state, &user_data, &meta, media_type, season, episode, &scope),
                orchestrator::run_usenet(state, &user_data, &meta, media_type, season, episode, &scope),
            );
            for s in scraped_torrents {
                all_torrents.push(scraped_to_json(&s));
            }
            let pinfo: Option<(&str, &str)> = if has_provider {
                primary_provider.map(|p| (secret_str, p))
            } else {
                None
            };
            for s in scraped_usenet {
                live_usenet.push(scraped_usenet_to_json(&s, &state.config.host_url, &state.config.addon_name, pinfo, season, episode));
            }
        }
    }

    let addon_name = &state.config.addon_name;
    let host_url = &state.config.host_url;
    let provider_info: Option<(&str, &str)> = if has_provider {
        primary_provider.map(|p| (secret_str, p))
    } else {
        None
    };

    // Format torrents into Stremio objects
    let torrent_streams = format_streams(
        all_torrents,
        addon_name,
        host_url,
        secret_str,
        primary_provider,
        season,
        episode,
    );

    // Format usenet streams into Stremio objects
    let mut usenet_streams: Vec<Value> = usenet_rows
        .iter()
        .filter_map(|row| db::usenet_row_to_stremio(row, host_url, addon_name, provider_info, season, episode))
        .collect();
    usenet_streams.extend(live_usenet);

    // Combine by type using user's stg/sto/mxs preferences (mirrors Python _combine_streams_by_type)
    let mut groups: std::collections::HashMap<&str, Vec<Value>> = std::collections::HashMap::new();
    groups.insert("torrent", torrent_streams);
    groups.insert("usenet", usenet_streams);

    Ok(user_data.combine_streams_by_type(&groups))
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

fn stream_key(id: i64, media_type: &str, season: Option<i32>, episode: Option<i32>, scope: &str) -> String {
    match (media_type, season, episode) {
        ("series", Some(s), Some(e)) => format!("stream_data:series:{id}:{s}:{e}:{scope}"),
        _ => format!("stream_data:movie:{id}:{scope}"),
    }
}

fn scraped_usenet_to_json(
    s: &crate::scrapers::ScrapedUsenetStream,
    host_url: &str,
    addon_name: &str,
    provider_info: Option<(&str, &str)>,
    season: Option<i32>,
    episode: Option<i32>,
) -> Value {
    let quality = s.parsed.quality.as_deref().unwrap_or("");
    let resolution = s.parsed.resolution.as_deref().unwrap_or("");
    let label = if !quality.is_empty() { quality } else if !resolution.is_empty() { resolution } else { "Unknown" };
    let description = build_description(quality, resolution, s.parsed.codec.as_deref(), Some(&s.source), None, Some(s.size), &s.source);
    let url = build_usenet_url(host_url, &s.nzb_guid, provider_info, season, episode);
    json!({
        "name": s.name,
        "description": description,
        "url": url,
        "behaviorHints": {
            "notWebReady": false,
            "bingeGroup": format!("{addon_name}-{label}-{resolution}"),
            "videoSize": s.size
        }
    })
}

fn scraped_to_json(s: &crate::scrapers::ScrapedStream) -> Value {
    let quality = s.parsed.quality.as_deref().unwrap_or("");
    let resolution = s.parsed.resolution.as_deref().unwrap_or("");
    let label = if !quality.is_empty() { quality } else if !resolution.is_empty() { resolution } else { "Unknown" };
    let description = build_description(quality, resolution, s.parsed.codec.as_deref(), None, s.seeders, s.size, &s.source);
    json!({
        "name": s.name,
        "description": description,
        "infoHash": s.info_hash,
        "sources": [format!("dht:{}", s.info_hash)],
        "behaviorHints": {
            "bingeGroup": format!("MediaFusion-{label}-{resolution}"),
            "notWebReady": true
        }
    })
}

fn build_description(
    quality: &str,
    resolution: &str,
    codec: Option<&str>,
    source: Option<&str>,
    seeders: Option<i32>,
    size: Option<i64>,
    fallback_source: &str,
) -> String {
    let mut parts: Vec<String> = Vec::new();

    // Line 1: quality / resolution / codec
    let mut quality_parts: Vec<&str> = Vec::new();
    if !quality.is_empty() { quality_parts.push(quality); }
    if !resolution.is_empty() { quality_parts.push(resolution); }
    if let Some(c) = codec.filter(|s| !s.is_empty()) { quality_parts.push(c); }
    if !quality_parts.is_empty() {
        parts.push(format!("📺 {}", quality_parts.join(" | ")));
    }

    // Line 2: size + seeders
    let mut stats: Vec<String> = Vec::new();
    if let Some(s) = size.filter(|&s| s > 0) {
        stats.push(format!("💾 {}", readable_size(s)));
    }
    if let Some(sd) = seeders.filter(|&s| s > 0) {
        stats.push(format!("👤 {sd}"));
    }
    if !stats.is_empty() {
        parts.push(stats.join(" "));
    }

    // Line 3: source
    let src = source.filter(|s| !s.is_empty()).unwrap_or(fallback_source);
    if !src.is_empty() {
        parts.push(format!("🔗 {src}"));
    }

    parts.join("\n")
}

fn readable_size(bytes: i64) -> String {
    const GB: i64 = 1_073_741_824;
    const MB: i64 = 1_048_576;
    if bytes >= GB {
        format!("{:.2} GB", bytes as f64 / GB as f64)
    } else if bytes >= MB {
        format!("{:.0} MB", bytes as f64 / MB as f64)
    } else {
        format!("{} B", bytes)
    }
}

fn format_streams(
    torrents: Vec<Value>,
    addon_name: &str,
    host_url: &str,
    secret_str: &str,
    primary_provider: Option<&str>,
    season: Option<i32>,
    episode: Option<i32>,
) -> Vec<Value> {
    torrents
        .into_iter()
        .filter_map(|t| {
            let name = t.get("name").and_then(|v| v.as_str())?;
            let hash = t.get("info_hash").and_then(|v| v.as_str())?;
            let quality    = t.get("quality").and_then(|v| v.as_str()).unwrap_or("");
            let resolution = t.get("resolution").and_then(|v| v.as_str()).unwrap_or("");
            let codec      = t.get("codec").and_then(|v| v.as_str());
            let source     = t.get("source").and_then(|v| v.as_str());
            let seeders    = t.get("seeders").and_then(|v| v.as_i64()).map(|s| s as i32);
            let size       = t.get("size").and_then(|v| v.as_i64());
            let file_index = t.get("file_index").and_then(|v| v.as_i64());
            let filename   = t.get("filename").and_then(|v| v.as_str()).unwrap_or("");

            let label = if !quality.is_empty() { quality } else if !resolution.is_empty() { resolution } else { "Unknown" };
            let description = build_description(quality, resolution, codec, source, seeders, size, "");
            let binge_group = format!("{addon_name}-{label}-{resolution}");

            let mut behavior: serde_json::Map<String, Value> = serde_json::Map::new();
            behavior.insert("bingeGroup".into(), json!(binge_group));
            if let Some(sz) = size.filter(|&s| s > 0) {
                behavior.insert("videoSize".into(), json!(sz));
            }

            let mut obj = serde_json::Map::new();
            obj.insert("name".into(), json!(name));
            obj.insert("description".into(), json!(description));

            if !secret_str.is_empty() {
                if let Some(provider) = primary_provider {
                    // Generate debrid proxy URL
                    let url = build_playback_url(host_url, secret_str, provider, hash, filename, season, episode);
                    obj.insert("url".into(), json!(url));
                    behavior.insert("notWebReady".into(), json!(false));
                    obj.insert("behaviorHints".into(), Value::Object(behavior));
                    if let Some(fi) = file_index {
                        obj.insert("fileIdx".into(), json!(fi as i32));
                    }
                    return Some(Value::Object(obj));
                }
            }

            // No provider — use infoHash for WebTorrent
            behavior.insert("notWebReady".into(), json!(true));
            if !filename.is_empty() {
                behavior.insert("filename".into(), json!(filename));
            }
            obj.insert("infoHash".into(), json!(hash));
            obj.insert("sources".into(), json!([format!("dht:{hash}")]));
            obj.insert("behaviorHints".into(), Value::Object(behavior));
            if let Some(fi) = file_index {
                obj.insert("fileIdx".into(), json!(fi as i32));
            }

            Some(Value::Object(obj))
        })
        .collect()
}

fn build_playback_url(
    host_url: &str,
    secret_str: &str,
    provider: &str,
    info_hash: &str,
    filename: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> String {
    let base = match (season, episode) {
        (Some(s), Some(e)) => format!(
            "{host_url}/streaming_provider/{secret_str}/playback/{provider}/{info_hash}/{s}/{e}"
        ),
        _ => format!(
            "{host_url}/streaming_provider/{secret_str}/playback/{provider}/{info_hash}"
        ),
    };
    if filename.is_empty() {
        base
    } else {
        format!("{base}/{}", urlencoding::encode(filename))
    }
}

fn build_usenet_url(
    host_url: &str,
    nzb_guid: &str,
    provider_info: Option<(&str, &str)>,
    season: Option<i32>,
    episode: Option<i32>,
) -> String {
    match provider_info {
        Some((secret_str, provider)) => match (season, episode) {
            (Some(s), Some(e)) => format!(
                "{host_url}/streaming_provider/{secret_str}/usenet/{provider}/{nzb_guid}/{s}/{e}"
            ),
            _ => format!(
                "{host_url}/streaming_provider/{secret_str}/usenet/{provider}/{nzb_guid}"
            ),
        },
        None => format!("{host_url}/usenet/{nzb_guid}"),
    }
}
