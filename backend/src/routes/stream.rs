use std::sync::Arc;

use axum::{
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Json},
};
use serde_json::{json, Value};

use futures::future::join_all;
use std::collections::HashMap;

use crate::{
    cache::{self, codec, stream_cache},
    crypto, db,
    db::TorrentType,
    models::user_data::{provider_short_name, SortingOption, UserData},
    parser::{
        self, compare_sort_keys, filter_streams_by_preferences, resolution_cap_key,
        sort_and_cap_stream_rows, torrent_sort_key, FilterContext,
    },
    scrapers::{orchestrator, torrent_metadata},
    state::{AppState, KeywordFilterCache},
    template,
    usenet_compat::is_usenet_stream_compatible,
    util::{live_stream, mediaflow, trackers},
};

use urlencoding;

use crate::models::user_data::MediaFlowConfig;

// ─── Provider capability constants ────────────────────────────────────────────

/// Providers that can generate /playback URLs for torrent streams
pub(crate) const TORRENT_CAPABLE: &[&str] = &[
    "alldebrid",
    "debridlink",
    "offcloud",
    "pikpak",
    "premiumize",
    "realdebrid",
    "seedr",
    "torbox",
    "stremthru",
    "easydebrid",
    "debrider",
];
/// Providers that can handle usenet NZB playback
pub(crate) const USENET_CAPABLE: &[&str] = &[
    "torbox",
    "debrider",
    "sabnzbd",
    "nzbget",
    "nzbdav",
    "easynews",
    "stremio_nntp",
];

// ─── Route handlers ────────────────────────────────────────────────────────────

pub async fn public_tv(
    Path(video_id): Path<String>,
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
) -> impl IntoResponse {
    let clean_id = video_id.trim_end_matches(".json").to_string();
    dispatch_tv(state, String::new(), clean_id, headers).await
}

pub async fn tv(
    Path((secret_str, video_id)): Path<(String, String)>,
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
) -> impl IntoResponse {
    let clean_id = video_id.trim_end_matches(".json").to_string();
    dispatch_tv(state, secret_str, clean_id, headers).await
}

pub async fn public_movie(
    Path(video_id): Path<String>,
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
) -> impl IntoResponse {
    let imdb_id = video_id.trim_end_matches(".json").to_string();
    dispatch(state, String::new(), imdb_id, "movie", None, None, headers).await
}

pub async fn public_series(
    Path(video_id): Path<String>,
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
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
    dispatch(
        state,
        String::new(),
        imdb_id,
        "series",
        Some(season),
        Some(episode),
        headers,
    )
    .await
}

pub async fn movie(
    Path((secret_str, video_id)): Path<(String, String)>,
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
) -> impl IntoResponse {
    let imdb_id = video_id.trim_end_matches(".json").to_string();
    if let Some(service) = crate::routes::delete_all_watchlist::parse_service(&imdb_id) {
        let raw_user_data = if let Some(hv) = headers
            .get("encoded_user_data")
            .and_then(|v| v.to_str().ok())
        {
            crypto::decode_encoded_user_data(hv)
                .unwrap_or_else(|| serde_json::Value::Object(Default::default()))
        } else {
            match crypto::resolve_user_data(
                &secret_str,
                &state.config.secret_key,
                &state.pool,
                &state.redis,
            )
            .await
            {
                Ok(v) => v,
                Err(e) => {
                    tracing::debug!("stream movie delete: {e}");
                    return (
                        StatusCode::UNPROCESSABLE_ENTITY,
                        Json(json!({"error": "Invalid user data"})),
                    )
                        .into_response();
                }
            }
        };
        let user_data: crate::models::user_data::UserData =
            serde_json::from_value(raw_user_data).unwrap_or_default();
        return crate::routes::delete_all_watchlist::delete_all_streams_response(
            state,
            &user_data,
            &secret_str,
            service,
        );
    }
    dispatch(state, secret_str, imdb_id, "movie", None, None, headers).await
}

pub async fn series(
    Path((secret_str, video_id)): Path<(String, String)>,
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
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
    dispatch(
        state,
        secret_str,
        imdb_id,
        "series",
        Some(season),
        Some(episode),
        headers,
    )
    .await
}

// ─── Live TV dispatch ─────────────────────────────────────────────────────────

async fn dispatch_tv(
    state: Arc<AppState>,
    secret_str: String,
    video_id: String,
    headers: HeaderMap,
) -> axum::response::Response {
    use axum::http::header;

    let raw_user_data = if let Some(hv) = headers
        .get("encoded_user_data")
        .and_then(|v| v.to_str().ok())
    {
        crypto::decode_encoded_user_data(hv)
            .unwrap_or_else(|| serde_json::Value::Object(Default::default()))
    } else {
        match crypto::resolve_user_data(
            &secret_str,
            &state.config.secret_key,
            &state.pool,
            &state.redis,
        )
        .await
        {
            Ok(v) => v,
            Err(e) => {
                tracing::debug!("dispatch_tv: {e}");
                let error_video = format!(
                    "{}/static/exceptions/invalid_config.mp4",
                    state.config.host_url
                );
                return Json(json!({
                    "streams": [{
                        "name": state.config.addon_name,
                        "description": "Invalid MediaFusion configuration.\nDelete and reconfigure the addon.",
                        "url": error_video,
                        "behaviorHints": { "notWebReady": true }
                    }]
                }))
                .into_response();
            }
        }
    };
    let user_data: crate::models::user_data::UserData =
        serde_json::from_value(raw_user_data).unwrap_or_default();

    // Resolve media_id from the video_id (same as movies)
    let cache_key = format!("{video_id}:tv");
    let (media_id, _related_ids) = if let Some(ids) = state.id_cache.get(&cache_key).await {
        ids
    } else {
        match db::resolve_media_ids(&state.pool, &video_id, "tv").await {
            Ok(ids) => {
                state.id_cache.insert(cache_key, ids.clone()).await;
                ids
            }
            Err(e) => {
                tracing::warn!("tv stream id lookup failed for {video_id}: {e}");
                return (
                    axum::http::StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({"error": e.to_string()})),
                )
                    .into_response();
            }
        }
    };

    if media_id == db::MediaId(0) {
        return Json(json!({"streams": []})).into_response();
    }

    let rows = db::fetch_tv_streams_for_media(&state.pool_ro, media_id).await;
    let is_mediaflow_proxy_enabled = user_data
        .mediaflow_config
        .as_ref()
        .is_some_and(|m| m.proxy_live_streams);
    let addon_name = if is_mediaflow_proxy_enabled {
        format!("{} 🕵🏼‍♂️", state.config.addon_name)
    } else {
        format!("{} 📡", state.config.addon_name)
    };

    let mediaflow = user_data.mediaflow_config.as_ref();
    let validate_liveness = state.config.validate_m3u8_urls_liveness;
    let mut rows_rev = rows;
    rows_rev.reverse();

    let tasks: Vec<_> = rows_rev
        .iter()
        .map(|row| {
            process_tv_stream_row(
                &state,
                row,
                &addon_name,
                is_mediaflow_proxy_enabled,
                mediaflow,
                validate_liveness,
            )
        })
        .collect();
    let processed = join_all(tasks).await;

    let mut formatted = Vec::new();
    let mut mediaflow_needed = false;
    for result in processed {
        match result {
            TvProcessResult::Stream(v) => formatted.push(v),
            TvProcessResult::MediaflowNeeded => mediaflow_needed = true,
            TvProcessResult::Skip => {}
        }
    }

    if formatted.is_empty() {
        let (description, file) = if mediaflow_needed {
            (
                "🚫 MediaFlow Proxy is required to watch this stream.",
                "mediaflow_proxy_required.mp4",
            )
        } else {
            (
                "🚫 No streams are live at the moment.",
                "no_streams_live.mp4",
            )
        };
        formatted.push(create_tv_exception_stream(
            &state.config.host_url,
            &addon_name,
            description,
            file,
        ));
    }

    // Live streams must never be cached by the client
    (
        axum::http::StatusCode::OK,
        [(header::CACHE_CONTROL, "no-store, no-cache, must-revalidate")],
        Json(json!({"streams": formatted})),
    )
        .into_response()
}

enum TvProcessResult {
    Stream(Value),
    MediaflowNeeded,
    Skip,
}

async fn process_tv_stream_row(
    state: &AppState,
    row: &Value,
    addon_name: &str,
    is_mediaflow_proxy_enabled: bool,
    mediaflow: Option<&MediaFlowConfig>,
    validate_liveness: bool,
) -> TvProcessResult {
    let kind = row
        .get("stream_kind")
        .and_then(|v| v.as_str())
        .unwrap_or("http");

    if kind == "youtube" {
        return format_tv_youtube_stream(row, addon_name)
            .map(TvProcessResult::Stream)
            .unwrap_or(TvProcessResult::Skip);
    }

    let url = match row
        .get("url")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
    {
        Some(u) => u,
        None => return TvProcessResult::Skip,
    };

    let behavior_hints = row.get("behavior_hints").cloned().unwrap_or(json!({}));
    if validate_liveness
        && !live_stream::validate_m3u8_or_mpd_url_with_cache(
            &state.http,
            &state.redis,
            url,
            &behavior_hints,
        )
        .await
    {
        return TvProcessResult::Skip;
    }

    let drm_key = row
        .get("drm_key")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty());
    if drm_key.is_some() && !is_mediaflow_proxy_enabled {
        return TvProcessResult::MediaflowNeeded;
    }

    let mut out_hints = behavior_hints.clone();
    let stream_url = if is_mediaflow_proxy_enabled {
        let mf = match mediaflow {
            Some(m) => m,
            None => return TvProcessResult::Skip,
        };
        match build_tv_mediaflow_url(row, url, mf) {
            Ok(u) => {
                if let Some(obj) = out_hints.as_object_mut() {
                    obj.insert("proxyHeaders".into(), Value::Null);
                }
                u
            }
            Err(e) => {
                tracing::warn!("tv mediaflow url failed: {e}");
                return TvProcessResult::Skip;
            }
        }
    } else {
        url.to_string()
    };

    let stream_name = row.get("name").and_then(|v| v.as_str()).unwrap_or("");
    let source = row.get("source").and_then(|v| v.as_str()).unwrap_or("");
    let country = row
        .get("country")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty());
    let country_info = country.map(|c| format!("\n🌐 {c}")).unwrap_or_default();
    let description = if source.is_empty() {
        format!("📺 {stream_name}{country_info}")
    } else {
        format!("📺 {stream_name}{country_info}\n🔗 {source}")
    };

    TvProcessResult::Stream(json!({
        "name": addon_name,
        "description": description,
        "url": stream_url,
        "behaviorHints": out_hints,
    }))
}

fn build_tv_mediaflow_url(
    row: &Value,
    url: &str,
    mediaflow: &MediaFlowConfig,
) -> Result<String, String> {
    use std::collections::BTreeMap;

    let proxy_url = mediaflow
        .proxy_url
        .as_deref()
        .filter(|s| !s.is_empty())
        .ok_or_else(|| "MediaFlow proxy_url missing".to_string())?;
    let api_password = mediaflow.api_password.as_deref();

    let drm_key = row
        .get("drm_key")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty());
    let endpoint = if drm_key.is_some() {
        "/proxy/mpd/manifest.m3u8"
    } else {
        "/proxy/hls/manifest.m3u8"
    };

    let mut query_params = BTreeMap::new();
    if let Some(key_id) = row
        .get("drm_key_id")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
    {
        query_params.insert("key_id".into(), key_id.to_string());
    }
    if let Some(key) = drm_key {
        query_params.insert("key".into(), key.to_string());
    }

    let behavior_hints = row.get("behavior_hints").and_then(|v| v.as_object());
    let request_headers = behavior_hints
        .and_then(|bh| bh.get("proxyHeaders"))
        .and_then(|ph| ph.get("request"))
        .and_then(|v| v.as_object());
    let response_headers = behavior_hints
        .and_then(|bh| bh.get("proxyHeaders"))
        .and_then(|ph| ph.get("response"))
        .and_then(|v| v.as_object());

    mediaflow::encode_mediaflow_proxy_url(
        proxy_url,
        endpoint,
        Some(url),
        query_params,
        request_headers,
        response_headers,
        api_password,
    )
}

fn format_tv_youtube_stream(row: &Value, addon_name: &str) -> Option<Value> {
    let video_id = row
        .get("video_id")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())?;
    let stream_name = row.get("name").and_then(|v| v.as_str()).unwrap_or("");
    let source = row.get("source").and_then(|v| v.as_str()).unwrap_or("");
    let description = if source.is_empty() {
        "▶️ YouTube".to_string()
    } else {
        format!("▶️ {source}")
    };

    Some(json!({
        "name": format!("{addon_name}\n{stream_name}"),
        "description": description,
        "ytId": video_id,
    }))
}

fn create_tv_exception_stream(
    host_url: &str,
    addon_name: &str,
    description: &str,
    exc_file_name: &str,
) -> Value {
    json!({
        "name": addon_name,
        "description": description,
        "url": format!("{host_url}/static/exceptions/{exc_file_name}"),
        "behaviorHints": { "notWebReady": true },
    })
}

// ─── Core orchestration ────────────────────────────────────────────────────────

async fn dispatch(
    state: Arc<AppState>,
    secret_str: String,
    imdb_id: String,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    headers: HeaderMap,
) -> axum::response::Response {
    match resolve(
        &state,
        &secret_str,
        &imdb_id,
        media_type,
        season,
        episode,
        &headers,
    )
    .await
    {
        Ok(streams) => Json(json!({"streams": streams})).into_response(),
        Err(e) => {
            if e.downcast_ref::<crypto::DecryptError>().is_some() {
                tracing::debug!("stream: D-profile decrypt failed for imdb={imdb_id}");
                let error_video = format!(
                    "{}/static/exceptions/invalid_config.mp4",
                    state.config.host_url
                );
                return Json(json!({
                    "streams": [{
                        "name": state.config.addon_name,
                        "description": "Invalid MediaFusion configuration.\nDelete and reconfigure the addon.",
                        "url": error_video,
                        "behaviorHints": { "notWebReady": true }
                    }]
                }))
                .into_response();
            }
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
    headers: &HeaderMap,
) -> Result<Vec<Value>, Box<dyn std::error::Error + Send + Sync>> {
    let mut p = build_pipeline(
        state, secret_str, imdb_id, media_type, season, episode, headers,
    )
    .await?;

    if p.media_id == db::MediaId(0)
        && p.all_torrents.is_empty()
        && p.http_rows.is_empty()
        && p.youtube_rows.is_empty()
        && p.telegram_rows.is_empty()
        && p.acestream_rows.is_empty()
        && p.usenet_rows.is_empty()
    {
        return Ok(vec![]);
    }

    let addon_name = &state.config.addon_name;
    let host_url = &state.config.host_url;

    let allow_public_usenet = state.config.is_scrap_from_public_usenet_indexers;
    let kf = state
        .keyword_filters
        .read()
        .map(|g| g.clone())
        .unwrap_or_default();
    let sorting_priority = p.user_data.sorting_priority();
    let selected_resolutions = p.user_data.effective_selected_resolutions();
    let quality_filter = if p.user_data.quality_filter.is_empty() {
        parser::default_quality_filter_groups()
    } else {
        p.user_data.quality_filter.clone()
    };
    let language_sorting = p.user_data.language_sorting_list();

    apply_content_filters_to_pipeline(&mut p, season, episode, allow_public_usenet, &kf);

    // Build refs for formatting (borrowing from pipeline)
    let torrent_providers_refs: Vec<&crate::models::user_data::StreamingProvider> =
        p.torrent_providers.iter().collect();
    let usenet_providers_refs: Vec<&crate::models::user_data::StreamingProvider> =
        p.usenet_providers.iter().collect();

    let type_mixed = p.user_data.stream_type_grouping == "mixed";

    // Expand torrent × provider pairs.
    // "mixed" provider grouping: one pair per torrent with best (first cached) provider.
    // "separate" provider grouping: full cross-product — one pair per (torrent × provider).
    let provider_mixed = p.user_data.provider_grouping.as_deref() == Some("mixed");
    let torrent_pairs: Vec<(Value, usize)> = if torrent_providers_refs.is_empty() {
        // No debrid: include torrents only if P2P is allowed for this user/instance.
        if p.show_p2p {
            p.all_torrents
                .iter()
                .filter(|t| {
                    torrent_metadata::private_torrent_visible_for_provider(
                        torrent_metadata::torrent_type_from_json_value(t),
                        "p2p",
                        false,
                    )
                })
                .cloned()
                .map(|t| (t, 0))
                .collect()
        } else {
            vec![]
        }
    } else if provider_mixed && torrent_providers_refs.len() > 1 {
        p.all_torrents
            .iter()
            .cloned()
            .map(|t| {
                let hash = t.get("info_hash").and_then(|v| v.as_str()).unwrap_or("");
                let best_pi = (0..torrent_providers_refs.len())
                    .find(|&pi| {
                        p.per_provider_cached
                            .get(pi)
                            .is_some_and(|m| m.get(hash).copied().unwrap_or(false))
                    })
                    .unwrap_or(0);
                (t, best_pi)
            })
            .collect()
    } else {
        p.all_torrents
            .iter()
            .flat_map(|t| (0..torrent_providers_refs.len()).map(move |pi| (t.clone(), pi)))
            .collect()
    };

    // Drop torrent pairs that RealDebrid would block based on filename patterns.
    let torrent_pairs: Vec<(Value, usize)> = torrent_pairs
        .into_iter()
        .filter(|(t, pi)| {
            let svc = torrent_providers_refs
                .get(*pi)
                .map(|pr| pr.service.as_str())
                .unwrap_or("");
            if svc != "realdebrid" {
                return true;
            }
            let check = t
                .get("filename")
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty())
                .unwrap_or_else(|| t.get("name").and_then(|v| v.as_str()).unwrap_or(""));
            !is_rd_blocked_filename(
                check,
                &state.config.rd_blocked_substrings,
                &state.config.rd_blocked_dot_pairs,
            )
        })
        .collect();

    // Drop uncached torrents for providers that have only_show_cached_streams enabled.
    let torrent_pairs: Vec<(Value, usize)> = torrent_pairs
        .into_iter()
        .filter(|(t, pi)| {
            let Some(provider) = torrent_providers_refs.get(*pi) else {
                return true;
            };
            if !provider.only_show_cached_streams {
                // fall through to private-tracker filter below
            } else {
                let hash = t.get("info_hash").and_then(|v| v.as_str()).unwrap_or("");
                if !p
                    .per_provider_cached
                    .get(*pi)
                    .is_some_and(|m| m.get(hash).copied().unwrap_or(false))
                {
                    return false;
                }
            }
            torrent_metadata::private_torrent_visible_for_provider(
                torrent_metadata::torrent_type_from_json_value(t),
                &provider.service,
                true,
            )
        })
        .collect();

    let tpl = p.user_data.stream_template.as_ref();

    // ── MIXED type grouping: unified sort across all types before formatting ─────
    if type_mixed {
        return Ok(format_unified_pool(
            torrent_pairs,
            &p,
            addon_name,
            host_url,
            secret_str,
            season,
            episode,
            &sorting_priority,
            &selected_resolutions,
            &quality_filter,
            &language_sorting,
            allow_public_usenet,
        ));
    }

    // ── SEPARATE type grouping: format each type independently ─────────────────
    let torrent_streams: Vec<Value> = if torrent_providers_refs.is_empty() {
        if p.show_p2p {
            // P2P/WebTorrent: no debrid but P2P is allowed (explicit or fallback).
            let raw: Vec<Value> = torrent_pairs.into_iter().map(|(t, _)| t).collect();
            let sorted = sort_and_cap_torrents(
                raw,
                &sorting_priority,
                &selected_resolutions,
                &quality_filter,
                &language_sorting,
                &HashMap::new(),
                season,
                episode,
                p.user_data.max_streams_per_resolution,
                p.user_data.effective_max_streams(),
            );
            format_streams(
                &sorted,
                addon_name,
                host_url,
                secret_str,
                None,
                season,
                episode,
                p.user_data.stream_template.as_ref(),
                &HashMap::new(),
                p.media_meta.as_ref(),
            )
        } else {
            // No torrent-capable provider and P2P not allowed (usenet-only or disabled).
            vec![]
        }
    } else {
        let mut pairs = torrent_pairs;

        // Sort the expanded list: each pair's sort key uses its own provider's cache map.
        if !sorting_priority.is_empty() {
            pairs.sort_by(|(ta, pa), (tb, pb)| {
                let ka = torrent_sort_key(
                    ta,
                    &sorting_priority,
                    &selected_resolutions,
                    &quality_filter,
                    &language_sorting,
                    &p.per_provider_cached[*pa],
                    season,
                    episode,
                );
                let kb = torrent_sort_key(
                    tb,
                    &sorting_priority,
                    &selected_resolutions,
                    &quality_filter,
                    &language_sorting,
                    &p.per_provider_cached[*pb],
                    season,
                    episode,
                );
                compare_sort_keys(&ka, &kb)
            });
        }

        // Apply per-resolution cap across the full expanded+sorted list.
        let max_per_res = p.user_data.max_streams_per_resolution;
        let mut res_counts: HashMap<String, u32> = HashMap::new();
        let capped: Vec<(Value, usize)> = pairs
            .into_iter()
            .filter(|(t, _)| {
                let res = resolution_cap_key(t);
                let count = res_counts.entry(res).or_insert(0);
                if *count < max_per_res {
                    *count += 1;
                    true
                } else {
                    false
                }
            })
            .collect();

        // Emit one stream per surviving pair using that pair's provider and cached map.
        capped
            .iter()
            .filter_map(|(t, pi)| {
                format_single_stream(
                    t,
                    addon_name,
                    host_url,
                    secret_str,
                    Some(torrent_providers_refs[*pi].service.as_str()),
                    season,
                    episode,
                    p.user_data.stream_template.as_ref(),
                    &p.per_provider_cached[*pi],
                    p.media_meta.as_ref(),
                )
            })
            .collect()
    };

    // ── SEPARATE type grouping: format each type independently ─────────────────
    // Sort + format usenet rows (same sort pipeline as torrents, with cached=false).
    let empty_cache: HashMap<String, bool> = HashMap::new();
    let mut usenet_pool: Vec<(Value, usize)> = Vec::new(); // (row_with_cached, provider_idx)
    for row in &p.usenet_rows {
        if let Some(pi) = usenet_providers_refs
            .iter()
            .position(|up| is_usenet_stream_compatible(row, up, &p.user_data, allow_public_usenet))
        {
            let mut r = row.clone();
            r["cached"] = json!(false);
            usenet_pool.push((r, pi));
        }
    }
    for s in &p.live_usenet_raw {
        let row = scraped_usenet_to_value(s);
        let filtered = filter_pipeline_rows(
            vec![row],
            &p.user_data,
            season,
            episode,
            true,
            allow_public_usenet,
            usenet_providers_refs.first().copied(),
            &kf,
        );
        for row in filtered {
            if let Some(pi) = usenet_providers_refs.iter().position(|up| {
                is_usenet_stream_compatible(&row, up, &p.user_data, allow_public_usenet)
            }) {
                let mut r = row;
                r["cached"] = json!(false);
                usenet_pool.push((r, pi));
            }
        }
    }
    if !sorting_priority.is_empty() {
        usenet_pool.sort_by(|(a, _), (b, _)| {
            let ka = torrent_sort_key(
                a,
                &sorting_priority,
                &selected_resolutions,
                &quality_filter,
                &language_sorting,
                &empty_cache,
                season,
                episode,
            );
            let kb = torrent_sort_key(
                b,
                &sorting_priority,
                &selected_resolutions,
                &quality_filter,
                &language_sorting,
                &empty_cache,
                season,
                episode,
            );
            compare_sort_keys(&ka, &kb)
        });
    }
    let usenet_streams: Vec<Value> = usenet_pool
        .into_iter()
        .filter_map(|(row, pi)| {
            format_single_usenet_stream(
                &row,
                addon_name,
                host_url,
                secret_str,
                Some(usenet_providers_refs[pi].service.as_str()),
                season,
                episode,
                tpl,
                p.media_meta.as_ref(),
            )
        })
        .collect();

    let max_per = p.user_data.max_streams_per_resolution;
    let max_total = p.user_data.effective_max_streams();

    let http_sorted = sort_stream_rows(
        p.http_rows.clone(),
        &sorting_priority,
        &selected_resolutions,
        &quality_filter,
        &language_sorting,
        &empty_cache,
        season,
        episode,
        max_per,
        max_total,
    );
    let http_streams: Vec<Value> = http_sorted
        .iter()
        .filter_map(|row| {
            format_http_stream(
                row,
                addon_name,
                tpl,
                false,
                p.media_meta.as_ref(),
                season,
                episode,
            )
        })
        .collect();

    let youtube_sorted = sort_stream_rows(
        p.youtube_rows.clone(),
        &sorting_priority,
        &selected_resolutions,
        &quality_filter,
        &language_sorting,
        &empty_cache,
        season,
        episode,
        max_per,
        max_total,
    );
    let youtube_streams: Vec<Value> = youtube_sorted
        .iter()
        .filter_map(|row| {
            format_youtube_stream(row, addon_name, tpl, p.media_meta.as_ref(), season, episode)
        })
        .collect();

    let mediaflow = p.user_data.mediaflow_config.as_ref();
    let telegram_sorted = sort_stream_rows(
        p.telegram_rows.clone(),
        &sorting_priority,
        &selected_resolutions,
        &quality_filter,
        &language_sorting,
        &empty_cache,
        season,
        episode,
        max_per,
        max_total,
    );
    let telegram_streams: Vec<Value> = telegram_sorted
        .iter()
        .filter_map(|row| {
            format_telegram_stream(
                row,
                addon_name,
                host_url,
                secret_str,
                tpl,
                p.media_meta.as_ref(),
                season,
                episode,
            )
        })
        .collect();

    let acestream_sorted = sort_stream_rows(
        p.acestream_rows.clone(),
        &sorting_priority,
        &selected_resolutions,
        &quality_filter,
        &language_sorting,
        &empty_cache,
        season,
        episode,
        max_per,
        max_total,
    );
    let acestream_streams: Vec<Value> = acestream_sorted
        .iter()
        .filter_map(|row| format_acestream_stream(row, addon_name, mediaflow, tpl))
        .collect();

    // Separate mode: cap each type at max_streams before combining.
    // combine_streams_by_type will NOT re-cap for "separate" mode.
    let max = p.user_data.max_streams as usize;
    let mut torrent_streams = torrent_streams;
    torrent_streams.truncate(max);
    let mut usenet_streams = usenet_streams;
    usenet_streams.truncate(max);
    let mut http_streams = http_streams;
    http_streams.truncate(max);
    let mut youtube_streams = youtube_streams;
    youtube_streams.truncate(max);
    let mut telegram_streams = telegram_streams;
    telegram_streams.truncate(max);
    let mut acestream_streams = acestream_streams;
    acestream_streams.truncate(max);

    let mut groups: std::collections::HashMap<&str, Vec<Value>> = std::collections::HashMap::new();
    groups.insert("torrent", torrent_streams);
    groups.insert("usenet", usenet_streams);
    groups.insert("http", http_streams);
    groups.insert("youtube", youtube_streams);
    groups.insert("telegram", telegram_streams);
    groups.insert("acestream", acestream_streams);

    Ok(p.user_data.combine_streams_by_type(&groups))
}

// ─── P2P eligibility ─────────────────────────────────────────────────────────

/// Mirrors Python's P2P provider decision in `utils/parser.py`:
/// 1. Debrid providers present → no P2P (they handle torrents).
/// 2. Explicit `service == "p2p"` provider configured and not disabled → P2P.
/// 3. Any other active provider (usenet-only etc.) → no P2P.
/// 4. No providers at all → P2P unless "p2p" is in disabled_providers.
fn compute_show_p2p(
    user_data: &crate::models::user_data::UserData,
    torrent_providers: &[crate::models::user_data::StreamingProvider],
    disabled: &[String],
) -> bool {
    if !torrent_providers.is_empty() {
        return false;
    }
    let active: Vec<_> = user_data
        .streaming_providers
        .iter()
        .filter(|p| p.enabled && !disabled.contains(&p.service))
        .collect();
    if active.iter().any(|p| p.service == "p2p") {
        return true;
    }
    if !active.is_empty() {
        return false; // usenet-only or other non-torrent provider
    }
    !disabled.contains(&"p2p".to_string())
}

fn filter_pipeline_rows(
    rows: Vec<Value>,
    user_data: &UserData,
    season: Option<i32>,
    episode: Option<i32>,
    is_usenet: bool,
    allow_public_usenet: bool,
    primary_provider: Option<&crate::models::user_data::StreamingProvider>,
    keyword_filters: &KeywordFilterCache,
) -> Vec<Value> {
    let ctx = FilterContext {
        user_data,
        season,
        episode,
        primary_provider,
        is_usenet,
        allow_public_usenet,
        keyword_filters,
    };
    filter_streams_by_preferences(rows, &ctx)
}

fn apply_content_filters_to_pipeline(
    p: &mut StreamPipeline,
    season: Option<i32>,
    episode: Option<i32>,
    allow_public_usenet: bool,
    keyword_filters: &KeywordFilterCache,
) {
    p.all_torrents = filter_pipeline_rows(
        std::mem::take(&mut p.all_torrents),
        &p.user_data,
        season,
        episode,
        false,
        allow_public_usenet,
        None,
        keyword_filters,
    );
    p.usenet_rows = filter_pipeline_rows(
        std::mem::take(&mut p.usenet_rows),
        &p.user_data,
        season,
        episode,
        true,
        allow_public_usenet,
        p.usenet_providers.first(),
        keyword_filters,
    );
    p.http_rows = filter_pipeline_rows(
        std::mem::take(&mut p.http_rows),
        &p.user_data,
        season,
        episode,
        false,
        allow_public_usenet,
        None,
        keyword_filters,
    );
    p.youtube_rows = filter_pipeline_rows(
        std::mem::take(&mut p.youtube_rows),
        &p.user_data,
        season,
        episode,
        false,
        allow_public_usenet,
        None,
        keyword_filters,
    );
    p.telegram_rows = filter_pipeline_rows(
        std::mem::take(&mut p.telegram_rows),
        &p.user_data,
        season,
        episode,
        false,
        allow_public_usenet,
        None,
        keyword_filters,
    );
    p.acestream_rows = filter_pipeline_rows(
        std::mem::take(&mut p.acestream_rows),
        &p.user_data,
        season,
        episode,
        false,
        allow_public_usenet,
        None,
        keyword_filters,
    );
}

fn sort_stream_rows(
    rows: Vec<Value>,
    priority: &[SortingOption],
    selected_resolutions: &[Option<String>],
    quality_filter: &[String],
    language_sorting: &[Option<String>],
    cached_hashes: &HashMap<String, bool>,
    season: Option<i32>,
    episode: Option<i32>,
    max_per_resolution: u32,
    max_total: u32,
) -> Vec<Value> {
    sort_and_cap_stream_rows(
        rows,
        priority,
        selected_resolutions,
        quality_filter,
        language_sorting,
        cached_hashes,
        season,
        episode,
        max_per_resolution,
        max_total,
    )
}

// ─── Pipeline struct and build_pipeline() ────────────────────────────────────

struct StreamPipeline {
    all_torrents: Vec<Value>,
    torrent_providers: Vec<crate::models::user_data::StreamingProvider>,
    usenet_providers: Vec<crate::models::user_data::StreamingProvider>,
    per_provider_cached: Vec<HashMap<String, bool>>,
    usenet_rows: Vec<Value>,
    http_rows: Vec<Value>,
    youtube_rows: Vec<Value>,
    telegram_rows: Vec<Value>,
    acestream_rows: Vec<Value>,
    live_usenet_raw: Vec<crate::scrapers::ScrapedUsenetStream>,
    user_data: crate::models::user_data::UserData,
    media_id: db::MediaId,
    /// Whether P2P/WebTorrent streams should be shown (mirrors Python's P2P decision).
    show_p2p: bool,
    /// Media-level metadata for template rendering (title, year, rating, etc.).
    media_meta: Option<Value>,
}

async fn build_pipeline(
    state: &AppState,
    secret_str: &str,
    imdb_id: &str,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    headers: &HeaderMap,
) -> Result<StreamPipeline, Box<dyn std::error::Error + Send + Sync>> {
    // 1. Decrypt user config → parse into UserData → derive scope
    // If the `encoded_user_data` header is present, decode it directly (no encryption).
    let raw_user_data = if let Some(hv) = headers
        .get("encoded_user_data")
        .and_then(|v| v.to_str().ok())
    {
        crypto::decode_encoded_user_data(hv).unwrap_or_else(|| Value::Object(Default::default()))
    } else {
        crypto::resolve_user_data(
            secret_str,
            &state.config.secret_key,
            &state.pool,
            &state.redis,
        )
        .await
        .map_err(|e| Box::new(e) as Box<dyn std::error::Error + Send + Sync>)?
    };
    let user_data: crate::models::user_data::UserData =
        serde_json::from_value(raw_user_data).unwrap_or_default();

    let scope = match user_data.user_id {
        Some(id) if id.0 > 0 => format!("user:{id}"),
        _ => "public".into(),
    };

    // 2. Resolve imdb_id → (primary_id, related_ids)
    let cache_key = format!("{imdb_id}:{media_type}");
    let (mut media_id, mut related_ids) = if let Some(ids) = state.id_cache.get(&cache_key).await {
        ids
    } else {
        let ids = db::resolve_media_ids(&state.pool, imdb_id, media_type).await?;
        state.id_cache.insert(cache_key.clone(), ids.clone()).await;
        ids
    };

    // 2b. On-demand fetch-and-create when the id is unknown but live search is active.
    // Mirrors the Python `_fetch_missing_media_for_live_search` path: fetch metadata from
    // TMDB (via find/{imdb_id}) / Cinemeta and insert a media row so the live-scrape step
    // below (Prowlarr / Jackett) can run.  We only do this when `live_search_streams` is on
    // to preserve Python parity — it was always gated on that flag.
    if media_id == db::MediaId(0)
        && related_ids.is_empty()
        && user_data.live_search_streams
        && crate::scrapers::metadata::parse_import_meta_id(imdb_id).is_some()
    {
        if let Some(created_raw_id) = crate::scrapers::media_resolve::ensure_media_for_import(
            &state.pool,
            &state.http,
            imdb_id,
            media_type,
            state.config.tmdb_api_key.as_deref(),
            state.config.tvdb_api_key.as_deref(),
            crate::scrapers::media_resolve::ImportMediaOverrides {
                title: None,
                poster: None,
                background: None,
                release_date: None,
                year: None,
            },
            None,
        )
        .await
        {
            let new_id = db::MediaId(created_raw_id);
            // Update the id cache so subsequent requests hit the DB row directly.
            state.id_cache.insert(cache_key, (new_id, vec![])).await;
            media_id = new_id;
            related_ids = vec![];
            tracing::info!(
                "stream: on-demand metadata created for {imdb_id} (media_id={created_raw_id})"
            );
        }
    }

    if state.config.background_search_enabled && media_id != db::MediaId(0) {
        let item_key = match (media_type, season, episode) {
            ("series", Some(s), Some(e)) => {
                crate::scrapers::background_queue::series_item_key(media_id.0, s, e)
            }
            ("movie", _, _) => crate::scrapers::background_queue::movie_item_key(media_id.0),
            _ => String::new(),
        };
        if !item_key.is_empty() {
            let queue_key = if media_type == "series" {
                crate::scrapers::background_queue::SERIES_KEY
            } else {
                crate::scrapers::background_queue::MOVIES_KEY
            };
            crate::scrapers::background_queue::enqueue(&state.redis, queue_key, &item_key).await;
        }
    }

    let disabled = &state.config.disabled_providers;

    // Guard: don't serve streams for blocked media (manual or keyword block).
    if media_id != db::MediaId(0) {
        let blocked: bool = sqlx::query_scalar::<_, bool>(
            "SELECT (is_keyword_blocked OR is_blocked) FROM media WHERE id = $1",
        )
        .bind(media_id.0)
        .fetch_optional(&state.pool)
        .await
        .unwrap_or(None)
        .unwrap_or(false);

        if blocked {
            let torrent_providers: Vec<crate::models::user_data::StreamingProvider> = user_data
                .streaming_providers
                .iter()
                .filter(|p| {
                    p.enabled
                        && TORRENT_CAPABLE.contains(&p.service.as_str())
                        && !disabled.contains(&p.service)
                })
                .cloned()
                .collect();
            let usenet_providers: Vec<crate::models::user_data::StreamingProvider> = user_data
                .streaming_providers
                .iter()
                .filter(|p| {
                    p.enabled
                        && USENET_CAPABLE.contains(&p.service.as_str())
                        && (!TORRENT_CAPABLE.contains(&p.service.as_str()) || p.enable_usenet)
                        && !disabled.contains(&p.service)
                })
                .cloned()
                .collect();
            let show_p2p = compute_show_p2p(&user_data, &torrent_providers, disabled);
            return Ok(StreamPipeline {
                all_torrents: vec![],
                torrent_providers,
                usenet_providers,
                per_provider_cached: vec![],
                usenet_rows: vec![],
                http_rows: vec![],
                youtube_rows: vec![],
                telegram_rows: vec![],
                acestream_rows: vec![],
                live_usenet_raw: vec![],
                user_data,
                media_id: db::MediaId(0),
                show_p2p,
                media_meta: None,
            });
        }
    }

    if media_id == db::MediaId(0) && related_ids.is_empty() {
        let torrent_providers: Vec<crate::models::user_data::StreamingProvider> = user_data
            .streaming_providers
            .iter()
            .filter(|p| {
                p.enabled
                    && TORRENT_CAPABLE.contains(&p.service.as_str())
                    && !disabled.contains(&p.service)
            })
            .cloned()
            .collect();
        let usenet_providers: Vec<crate::models::user_data::StreamingProvider> = user_data
            .streaming_providers
            .iter()
            .filter(|p| {
                p.enabled
                    && USENET_CAPABLE.contains(&p.service.as_str())
                    // Hybrid providers (also torrent-capable) require explicit opt-in for usenet
                    && (!TORRENT_CAPABLE.contains(&p.service.as_str()) || p.enable_usenet)
                    && !disabled.contains(&p.service)
            })
            .cloned()
            .collect();
        let show_p2p = compute_show_p2p(&user_data, &torrent_providers, disabled);
        return Ok(StreamPipeline {
            all_torrents: vec![],
            torrent_providers,
            usenet_providers,
            per_provider_cached: vec![],
            usenet_rows: vec![],
            http_rows: vec![],
            youtube_rows: vec![],
            telegram_rows: vec![],
            acestream_rows: vec![],
            live_usenet_raw: vec![],
            user_data,
            media_id: db::MediaId(0),
            show_p2p,
            media_meta: None,
        });
    }

    // 3. Build unique ID list + Redis keys
    let mut all_ids: Vec<db::MediaId> = std::iter::once(media_id)
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
    let mut misses: Vec<db::MediaId> = Vec::new();

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
                let _ = stream_cache::set_with_ttl(
                    &state.redis,
                    &key,
                    blob,
                    state.config.stream_raw_redis_cache_ttl,
                )
                .await;
            }
        }
    }

    let torrent_providers: Vec<crate::models::user_data::StreamingProvider> = user_data
        .streaming_providers
        .iter()
        .filter(|p| {
            p.enabled
                && TORRENT_CAPABLE.contains(&p.service.as_str())
                && !disabled.contains(&p.service)
        })
        .cloned()
        .collect();

    let usenet_providers: Vec<crate::models::user_data::StreamingProvider> = user_data
        .streaming_providers
        .iter()
        .filter(|p| {
            p.enabled
                && USENET_CAPABLE.contains(&p.service.as_str())
                // Hybrid providers (also torrent-capable) require explicit opt-in for usenet
                && (!TORRENT_CAPABLE.contains(&p.service.as_str()) || p.enable_usenet)
                && !disabled.contains(&p.service)
        })
        .cloned()
        .collect();

    // 6. Fetch usenet, http, youtube, telegram, acestream from DB in parallel
    let (usenet_rows, http_rows, youtube_rows, telegram_rows, acestream_rows) = tokio::join!(
        async {
            if user_data.enable_usenet_streams && !usenet_providers.is_empty() {
                db::fetch_usenet_streams_bulk(&state.pool_ro, &all_ids, media_type, season, episode)
                    .await
                    .into_iter()
                    .flat_map(|(_, rows)| rows)
                    .collect::<Vec<_>>()
            } else {
                vec![]
            }
        },
        async {
            db::fetch_http_streams_bulk(&state.pool_ro, &all_ids, media_type, season, episode)
                .await
                .into_iter()
                .flat_map(|(_, rows)| rows)
                .collect::<Vec<_>>()
        },
        async {
            db::fetch_youtube_streams_bulk(&state.pool_ro, &all_ids)
                .await
                .into_iter()
                .flat_map(|(_, rows)| rows)
                .collect::<Vec<_>>()
        },
        async {
            if user_data.enable_telegram_streams && user_data.has_mediaflow_config() {
                db::fetch_telegram_streams_bulk(
                    &state.pool_ro,
                    &all_ids,
                    media_type,
                    season,
                    episode,
                )
                .await
                .into_iter()
                .flat_map(|(_, rows)| rows)
                .collect::<Vec<_>>()
            } else {
                vec![]
            }
        },
        async {
            if user_data.enable_acestream_streams && user_data.has_mediaflow_config() {
                db::fetch_acestream_streams_bulk(&state.pool_ro, &all_ids)
                    .await
                    .into_iter()
                    .flat_map(|(_, rows)| rows)
                    .collect::<Vec<_>>()
            } else {
                vec![]
            }
        },
    );

    // 7. Live scrape
    let mut live_usenet_raw: Vec<crate::scrapers::ScrapedUsenetStream> = Vec::new();
    if user_data.live_search_streams {
        if let Ok(Some(meta)) = db::get_media_meta(&state.pool, media_id, imdb_id).await {
            let (scraped_torrents, scraped_usenet) = orchestrator::run_live_search(
                state, &user_data, &meta, media_type, season, episode, &scope,
            )
            .await;
            for s in scraped_torrents {
                all_torrents.push(scraped_to_json(&s));
            }
            if !usenet_providers.is_empty() {
                live_usenet_raw = scraped_usenet;
            }
        }
    }

    // Build all_hashes after live scrape (complete set)
    let all_hashes: Vec<String> = all_torrents
        .iter()
        .filter_map(|t| {
            t.get("info_hash")
                .and_then(|v| v.as_str())
                .map(str::to_string)
        })
        .collect();

    // Per-provider cache lookup
    let per_provider_cached: Vec<HashMap<String, bool>> = {
        let futs = torrent_providers.iter().map(|provider| {
            let provider_service = provider.service.clone();
            let cache_service = provider.cache_service_name();
            let tok = provider.token.clone().unwrap_or_default();
            let hashes = all_hashes.clone();
            let store_stremthru = state.config.store_stremthru_magnet_cache;
            async move {
                let mut cached = cache::get_debrid_cache_status_federated(
                    &state.redis,
                    Some(&state.http),
                    &cache_service,
                    &provider_service,
                    &hashes,
                    state.config.sync_debrid_cache_streams,
                    &state.config.mediafusion_url,
                )
                .await;
                if !tok.is_empty() {
                    let uncached: Vec<String> = hashes
                        .iter()
                        .filter(|h| !cached.get(*h).copied().unwrap_or(false))
                        .cloned()
                        .collect();
                    if !uncached.is_empty() {
                        let live = crate::providers::torrents::cache::live_check(
                            &state.http,
                            &state.redis,
                            &provider_service,
                            &cache_service,
                            &tok,
                            &uncached,
                            i32::from(media_id),
                            store_stremthru,
                        )
                        .await;
                        for (hash, is_cached) in live {
                            if is_cached {
                                cached.insert(hash, true);
                            }
                        }
                    }
                }
                cached
            }
        });
        join_all(futs).await
    };

    let show_p2p = compute_show_p2p(&user_data, &torrent_providers, disabled);

    let media_meta: Option<Value> = if let Some(mt) = db::MediaType::from_wire(media_type) {
        crate::db::meta::fetch_media_meta_by_id(&state.pool, media_id, mt)
            .await
            .and_then(|row| serde_json::to_value(row).ok())
    } else {
        None
    };

    Ok(StreamPipeline {
        all_torrents,
        torrent_providers,
        usenet_providers,
        per_provider_cached,
        usenet_rows,
        http_rows,
        youtube_rows,
        telegram_rows,
        acestream_rows,
        live_usenet_raw,
        user_data,
        media_id,
        show_p2p,
        media_meta,
    })
}

pub async fn resolve_rich(
    state: &AppState,
    secret_str: &str,
    imdb_id: &str,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    headers: &HeaderMap,
) -> Result<Vec<Value>, Box<dyn std::error::Error + Send + Sync>> {
    let mut p = build_pipeline(
        state, secret_str, imdb_id, media_type, season, episode, headers,
    )
    .await?;

    let allow_public_usenet = state.config.is_scrap_from_public_usenet_indexers;
    let kf2 = state
        .keyword_filters
        .read()
        .map(|g| g.clone())
        .unwrap_or_default();
    apply_content_filters_to_pipeline(&mut p, season, episode, allow_public_usenet, &kf2);

    let addon_name = &state.config.addon_name;
    let host_url = &state.config.host_url;

    let sorting_priority = p.user_data.sorting_priority();
    let selected_resolutions = p.user_data.effective_selected_resolutions();
    let quality_filter = if p.user_data.quality_filter.is_empty() {
        parser::default_quality_filter_groups()
    } else {
        p.user_data.quality_filter.clone()
    };
    let language_sorting = p.user_data.language_sorting_list();

    let mut rich_streams: Vec<Value> = Vec::new();

    if p.torrent_providers.is_empty() && p.show_p2p {
        // No debrid provider but P2P allowed — emit as WebTorrent streams
        let sorted = sort_and_cap_torrents(
            p.all_torrents,
            &sorting_priority,
            &selected_resolutions,
            &quality_filter,
            &language_sorting,
            &HashMap::new(),
            season,
            episode,
            p.user_data.max_streams_per_resolution,
            p.user_data.effective_max_streams(),
        );
        for t in &sorted {
            let formatted = format_single_stream(
                t,
                addon_name,
                host_url,
                secret_str,
                None,
                season,
                episode,
                p.user_data.stream_template.as_ref(),
                &HashMap::new(),
                p.media_meta.as_ref(),
            );
            if let Some(stream_val) = formatted {
                let meta = build_torrent_metadata(t, None, false);
                rich_streams.push(json!({ "stream": stream_val, "metadata": meta }));
            }
        }
    } else {
        let mut pairs: Vec<(Value, usize)> = p
            .all_torrents
            .iter()
            .flat_map(|t| {
                let t = t.clone();
                (0..p.torrent_providers.len()).map(move |pi| (t.clone(), pi))
            })
            .collect();

        if !sorting_priority.is_empty() {
            pairs.sort_by(|(ta, pa), (tb, pb)| {
                let ka = torrent_sort_key(
                    ta,
                    &sorting_priority,
                    &selected_resolutions,
                    &quality_filter,
                    &language_sorting,
                    &p.per_provider_cached[*pa],
                    season,
                    episode,
                );
                let kb = torrent_sort_key(
                    tb,
                    &sorting_priority,
                    &selected_resolutions,
                    &quality_filter,
                    &language_sorting,
                    &p.per_provider_cached[*pb],
                    season,
                    episode,
                );
                compare_sort_keys(&ka, &kb)
            });
        }

        let max_per_res = p.user_data.max_streams_per_resolution;
        let mut res_counts: HashMap<String, u32> = HashMap::new();
        let capped: Vec<(Value, usize)> = pairs
            .into_iter()
            .filter(|(t, _)| {
                let res = resolution_cap_key(t);
                let count = res_counts.entry(res).or_insert(0);
                if *count < max_per_res {
                    *count += 1;
                    true
                } else {
                    false
                }
            })
            .collect();

        for (t, pi) in &capped {
            let provider_name = p.torrent_providers[*pi].service.as_str();
            let hash = t.get("info_hash").and_then(|v| v.as_str()).unwrap_or("");
            let is_cached = p.per_provider_cached[*pi]
                .get(hash)
                .copied()
                .unwrap_or(false);
            if let Some(stream_val) = format_single_stream(
                t,
                addon_name,
                host_url,
                secret_str,
                Some(provider_name),
                season,
                episode,
                p.user_data.stream_template.as_ref(),
                &p.per_provider_cached[*pi],
                p.media_meta.as_ref(),
            ) {
                let meta = build_torrent_metadata(t, Some(provider_name), is_cached);
                rich_streams.push(json!({ "stream": stream_val, "metadata": meta }));
            }
        }
    }

    let tpl = p.user_data.stream_template.as_ref();
    let allow_public_usenet = state.config.is_scrap_from_public_usenet_indexers;

    // Usenet streams
    for row in &p.usenet_rows {
        for up in &p.usenet_providers {
            if !is_usenet_stream_compatible(row, up, &p.user_data, allow_public_usenet) {
                continue;
            }
            if let Some(stream_val) = format_single_usenet_stream(
                row,
                addon_name,
                host_url,
                secret_str,
                Some(up.service.as_str()),
                season,
                episode,
                tpl,
                p.media_meta.as_ref(),
            ) {
                let meta = json!({
                    "stream_type": "usenet",
                    "name": row.get("name").and_then(|v| v.as_str()).unwrap_or(""),
                    "source": row.get("indexer").and_then(|v| v.as_str()).unwrap_or(""),
                    "quality": row.get("quality").and_then(|v| v.as_str()),
                    "resolution": row.get("resolution").and_then(|v| v.as_str()),
                    "size": row.get("size").and_then(|v| v.as_i64()),
                });
                rich_streams.push(json!({ "stream": stream_val, "metadata": meta }));
            }
        }
    }

    // Live scraped usenet
    for s in &p.live_usenet_raw {
        let row = scraped_usenet_to_value(s);
        for up in &p.usenet_providers {
            if !is_usenet_stream_compatible(&row, up, &p.user_data, allow_public_usenet) {
                continue;
            }
            if let Some(stream_val) = format_single_usenet_stream(
                &row,
                addon_name,
                host_url,
                secret_str,
                Some(up.service.as_str()),
                season,
                episode,
                tpl,
                p.media_meta.as_ref(),
            ) {
                let meta = json!({
                    "stream_type": "usenet",
                    "name": s.name,
                    "source": s.source,
                    "quality": s.parsed.quality,
                    "resolution": s.parsed.resolution,
                    "size": s.size,
                });
                rich_streams.push(json!({ "stream": stream_val, "metadata": meta }));
            }
        }
    }

    // HTTP streams
    for row in &p.http_rows {
        if let Some(stream_val) = format_http_stream(
            row,
            addon_name,
            tpl,
            false,
            p.media_meta.as_ref(),
            season,
            episode,
        ) {
            let meta = json!({
                "stream_type": "http",
                "name": row.get("name").and_then(|v| v.as_str()).unwrap_or(""),
                "source": row.get("source").and_then(|v| v.as_str()).unwrap_or(""),
                "quality": row.get("quality").and_then(|v| v.as_str()),
                "resolution": row.get("resolution").and_then(|v| v.as_str()),
                "size": row.get("size").and_then(|v| v.as_i64()),
            });
            rich_streams.push(json!({ "stream": stream_val, "metadata": meta }));
        }
    }

    // YouTube streams
    for row in &p.youtube_rows {
        if let Some(stream_val) =
            format_youtube_stream(row, addon_name, tpl, p.media_meta.as_ref(), season, episode)
        {
            let meta = json!({
                "stream_type": "youtube",
                "name": row.get("name").and_then(|v| v.as_str()).unwrap_or(""),
                "source": row.get("source").and_then(|v| v.as_str()).unwrap_or(""),
                "quality": row.get("quality").and_then(|v| v.as_str()),
                "resolution": row.get("resolution").and_then(|v| v.as_str()),
                "size": Value::Null,
            });
            rich_streams.push(json!({ "stream": stream_val, "metadata": meta }));
        }
    }

    // Telegram streams
    for row in &p.telegram_rows {
        if let Some(stream_val) = format_telegram_stream(
            row,
            addon_name,
            host_url,
            secret_str,
            tpl,
            p.media_meta.as_ref(),
            season,
            episode,
        ) {
            let meta = json!({
                "stream_type": "telegram",
                "name": row.get("name").and_then(|v| v.as_str()).unwrap_or(""),
                "source": "telegram",
                "quality": row.get("quality").and_then(|v| v.as_str()),
                "resolution": row.get("resolution").and_then(|v| v.as_str()),
                "size": row.get("size").and_then(|v| v.as_i64()),
            });
            rich_streams.push(json!({ "stream": stream_val, "metadata": meta }));
        }
    }

    // AceStream streams
    let mediaflow = p.user_data.mediaflow_config.as_ref();
    for row in &p.acestream_rows {
        if let Some(stream_val) = format_acestream_stream(row, addon_name, mediaflow, tpl) {
            let meta = json!({
                "stream_type": "acestream",
                "name": row.get("name").and_then(|v| v.as_str()).unwrap_or(""),
                "source": row.get("source").and_then(|v| v.as_str()).unwrap_or(""),
                "quality": row.get("quality").and_then(|v| v.as_str()),
                "resolution": row.get("resolution").and_then(|v| v.as_str()),
                "size": Value::Null,
            });
            rich_streams.push(json!({ "stream": stream_val, "metadata": meta }));
        }
    }

    Ok(rich_streams)
}

fn build_torrent_metadata(t: &Value, provider: Option<&str>, is_cached: bool) -> Value {
    json!({
        "id": t.get("info_hash").and_then(|v| v.as_str()).unwrap_or(""),
        "info_hash": t.get("info_hash").and_then(|v| v.as_str()).unwrap_or(""),
        "name": t.get("name").and_then(|v| v.as_str()).unwrap_or(""),
        "resolution": t.get("resolution").and_then(|v| v.as_str()),
        "quality": t.get("quality").and_then(|v| v.as_str()),
        "codec": t.get("codec").and_then(|v| v.as_str()),
        "bit_depth": t.get("bit_depth").and_then(|v| v.as_str()),
        "audio_formats": t.get("audio_formats").cloned().unwrap_or(json!([])),
        "channels": t.get("channels").cloned().unwrap_or(json!([])),
        "hdr_formats": t.get("hdr_formats").cloned().unwrap_or(json!([])),
        "languages": t.get("languages").cloned().unwrap_or(json!([])),
        "is_proper": t.get("is_proper").and_then(|v| v.as_bool()).unwrap_or(false),
        "is_repack": t.get("is_repack").and_then(|v| v.as_bool()).unwrap_or(false),
        "is_extended": t.get("is_extended").and_then(|v| v.as_bool()).unwrap_or(false),
        "is_complete": t.get("is_complete").and_then(|v| v.as_bool()).unwrap_or(false),
        "is_dubbed": t.get("is_dubbed").and_then(|v| v.as_bool()).unwrap_or(false),
        "source": t.get("source").and_then(|v| v.as_str()).unwrap_or(""),
        "size": t.get("size").and_then(|v| v.as_i64()),
        "seeders": t.get("seeders").and_then(|v| v.as_i64()),
        "cached": is_cached,
        "stream_type": "torrent",
        "provider_name": provider,
        "provider_short_name": provider.map(provider_short_name),
        "filename": t.get("filename").and_then(|v| v.as_str()),
        "uploaded_at": t.get("created_at").and_then(|v| v.as_str()),
    })
}

// ─── Unified pool sort (mixed stream_type_grouping) ───────────────────────────

/// Sort all stream types together as one pool, then format in sorted order.
///
/// Rules:
/// - Torrent: `cached` = from debrid provider's cache map (per assigned provider)
/// - Usenet:  `cached` = false (playback marks it cached; not pre-cached)
/// - HTTP / Telegram / YouTube / AceStream: `cached` = true (always directly accessible)
#[allow(clippy::too_many_arguments)]
fn format_unified_pool(
    torrent_pairs: Vec<(Value, usize)>,
    p: &StreamPipeline,
    addon_name: &str,
    host_url: &str,
    secret_str: &str,
    season: Option<i32>,
    episode: Option<i32>,
    sorting_priority: &[SortingOption],
    selected_resolutions: &[Option<String>],
    quality_filter: &[String],
    language_sorting: &[Option<String>],
    allow_public_usenet: bool,
) -> Vec<Value> {
    let tpl = p.user_data.stream_template.as_ref();
    let mediaflow = p.user_data.mediaflow_config.as_ref();
    let max_per_res = p.user_data.max_streams_per_resolution;
    let max_total = p.user_data.effective_max_streams();

    // Each item: (raw_value_with_cached_annotated, sort_key, resolution, type_tag, provider_idx)
    // type_tag: 0=torrent, 1=usenet, 2=http, 3=youtube, 4=telegram, 5=acestream
    let empty_cache: HashMap<String, bool> = HashMap::new();

    struct Item {
        value: Value,
        sort_key: Vec<f64>,
        resolution: String,
        type_tag: u8,
        provider_idx: usize, // index into torrent_providers or usenet_providers; 0 for direct types
    }

    let mut pool: Vec<Item> = Vec::new();

    // Torrents
    for (mut t, pi) in torrent_pairs {
        let hash = t
            .get("info_hash")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let cached_map = p.per_provider_cached.get(pi).unwrap_or(&empty_cache);
        let is_cached = cached_map.get(&hash).copied().unwrap_or(false);
        t["cached"] = json!(is_cached);
        let sk = torrent_sort_key(
            &t,
            sorting_priority,
            selected_resolutions,
            quality_filter,
            language_sorting,
            cached_map,
            season,
            episode,
        );
        let res = resolution_cap_key(&t);
        pool.push(Item {
            value: t,
            sort_key: sk,
            resolution: res,
            type_tag: 0,
            provider_idx: pi,
        });
    }

    // Usenet — one entry per (row × first-compatible-provider); cached=false
    for row in &p.usenet_rows {
        let pi = p
            .usenet_providers
            .iter()
            .position(|up| is_usenet_stream_compatible(row, up, &p.user_data, allow_public_usenet));
        if let Some(pi) = pi {
            let mut r = row.clone();
            r["cached"] = json!(false);
            let sk = torrent_sort_key(
                &r,
                sorting_priority,
                selected_resolutions,
                quality_filter,
                language_sorting,
                &empty_cache,
                season,
                episode,
            );
            let res = resolution_cap_key(&r);
            pool.push(Item {
                value: r,
                sort_key: sk,
                resolution: res,
                type_tag: 1,
                provider_idx: pi,
            });
        }
    }
    // Live-scraped usenet
    for s in &p.live_usenet_raw {
        let row = scraped_usenet_to_value(s);
        let pi = p.usenet_providers.iter().position(|up| {
            is_usenet_stream_compatible(&row, up, &p.user_data, allow_public_usenet)
        });
        if let Some(pi) = pi {
            let mut r = row;
            r["cached"] = json!(false);
            let sk = torrent_sort_key(
                &r,
                sorting_priority,
                selected_resolutions,
                quality_filter,
                language_sorting,
                &empty_cache,
                season,
                episode,
            );
            let res = resolution_cap_key(&r);
            pool.push(Item {
                value: r,
                sort_key: sk,
                resolution: res,
                type_tag: 1,
                provider_idx: pi,
            });
        }
    }

    // HTTP — cached=true
    for row in &p.http_rows {
        let mut r = row.clone();
        r["cached"] = json!(true);
        let sk = torrent_sort_key(
            &r,
            sorting_priority,
            selected_resolutions,
            quality_filter,
            language_sorting,
            &empty_cache,
            season,
            episode,
        );
        let res = resolution_cap_key(&r);
        pool.push(Item {
            value: r,
            sort_key: sk,
            resolution: res,
            type_tag: 2,
            provider_idx: 0,
        });
    }

    // YouTube — cached=true
    for row in &p.youtube_rows {
        let mut r = row.clone();
        r["cached"] = json!(true);
        let sk = torrent_sort_key(
            &r,
            sorting_priority,
            selected_resolutions,
            quality_filter,
            language_sorting,
            &empty_cache,
            season,
            episode,
        );
        let res = resolution_cap_key(&r);
        pool.push(Item {
            value: r,
            sort_key: sk,
            resolution: res,
            type_tag: 3,
            provider_idx: 0,
        });
    }

    // Telegram — cached=true
    for row in &p.telegram_rows {
        let mut r = row.clone();
        r["cached"] = json!(true);
        let sk = torrent_sort_key(
            &r,
            sorting_priority,
            selected_resolutions,
            quality_filter,
            language_sorting,
            &empty_cache,
            season,
            episode,
        );
        let res = resolution_cap_key(&r);
        pool.push(Item {
            value: r,
            sort_key: sk,
            resolution: res,
            type_tag: 4,
            provider_idx: 0,
        });
    }

    // AceStream — cached=true
    for row in &p.acestream_rows {
        let mut r = row.clone();
        r["cached"] = json!(true);
        let sk = torrent_sort_key(
            &r,
            sorting_priority,
            selected_resolutions,
            quality_filter,
            language_sorting,
            &empty_cache,
            season,
            episode,
        );
        let res = resolution_cap_key(&r);
        pool.push(Item {
            value: r,
            sort_key: sk,
            resolution: res,
            type_tag: 5,
            provider_idx: 0,
        });
    }

    // Sort the unified pool
    pool.sort_by(|a, b| {
        for (va, vb) in a.sort_key.iter().zip(b.sort_key.iter()) {
            match va.partial_cmp(vb) {
                Some(std::cmp::Ordering::Equal) | None => continue,
                Some(ord) => return ord,
            }
        }
        std::cmp::Ordering::Equal
    });

    // Per-resolution cap
    let mut res_counts: HashMap<String, u32> = HashMap::new();
    let capped: Vec<Item> = pool
        .into_iter()
        .filter(|item| {
            let count = res_counts.entry(item.resolution.clone()).or_insert(0);
            if *count < max_per_res {
                *count += 1;
                true
            } else {
                false
            }
        })
        .take(max_total as usize)
        .collect();

    // Format each item in sorted order
    let mut result: Vec<Value> = Vec::with_capacity(capped.len());
    for item in capped {
        let formatted = match item.type_tag {
            0 => {
                // Torrent
                let cached_map = p
                    .per_provider_cached
                    .get(item.provider_idx)
                    .unwrap_or(&empty_cache);
                let svc = p
                    .torrent_providers
                    .get(item.provider_idx)
                    .map(|pr| pr.service.as_str());
                format_single_stream(
                    &item.value,
                    addon_name,
                    host_url,
                    secret_str,
                    svc,
                    season,
                    episode,
                    tpl,
                    cached_map,
                    p.media_meta.as_ref(),
                )
            }
            1 => {
                // Usenet
                let svc = p
                    .usenet_providers
                    .get(item.provider_idx)
                    .map(|pr| pr.service.as_str());
                format_single_usenet_stream(
                    &item.value,
                    addon_name,
                    host_url,
                    secret_str,
                    svc,
                    season,
                    episode,
                    tpl,
                    p.media_meta.as_ref(),
                )
            }
            2 => format_http_stream(
                &item.value,
                addon_name,
                tpl,
                false,
                p.media_meta.as_ref(),
                season,
                episode,
            ),
            3 => format_youtube_stream(
                &item.value,
                addon_name,
                tpl,
                p.media_meta.as_ref(),
                season,
                episode,
            ),
            4 => format_telegram_stream(
                &item.value,
                addon_name,
                host_url,
                secret_str,
                tpl,
                p.media_meta.as_ref(),
                season,
                episode,
            ),
            5 => format_acestream_stream(&item.value, addon_name, mediaflow, tpl),
            _ => None,
        };
        if let Some(v) = formatted {
            result.push(v);
        }
    }
    result
}

/// Sort torrents by user's `torrent_sorting_priority` and apply caps.
fn sort_and_cap_torrents(
    torrents: Vec<Value>,
    priority: &[SortingOption],
    selected_resolutions: &[Option<String>],
    quality_filter: &[String],
    language_sorting: &[Option<String>],
    cached_hashes: &HashMap<String, bool>,
    season: Option<i32>,
    episode: Option<i32>,
    max_per_resolution: u32,
    max_total: u32,
) -> Vec<Value> {
    sort_stream_rows(
        torrents,
        priority,
        selected_resolutions,
        quality_filter,
        language_sorting,
        cached_hashes,
        season,
        episode,
        max_per_resolution,
        max_total,
    )
}

// ─── RealDebrid filename block list ──────────────────────────────────────────

/// Returns true if `filename` matches any of RealDebrid's blocked filename
/// patterns (causes RD to report the content as infringing and refuse to serve
/// it). Should be checked against the torrent filename, falling back to the
/// release name when no filename is stored.
///
/// Returns true if `filename` matches any RealDebrid-blocked pattern.
/// Patterns are operator-configurable via `RD_BLOCKED_SUBSTRINGS` and
/// `RD_BLOCKED_DOT_PAIRS` env vars; all comparisons are case-insensitive.
pub(crate) fn is_rd_blocked_filename(
    filename: &str,
    blocked_substrings: &[String],
    blocked_dot_pairs: &[String],
) -> bool {
    let lower = filename.to_lowercase();
    blocked_substrings
        .iter()
        .any(|p| lower.contains(p.as_str()))
        || blocked_dot_pairs.iter().any(|p| lower.contains(p.as_str()))
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

fn stream_key(
    id: db::MediaId,
    media_type: &str,
    season: Option<i32>,
    episode: Option<i32>,
    scope: &str,
) -> String {
    let id = i32::from(id);
    match (media_type, season, episode) {
        ("series", Some(s), Some(e)) => format!("stream_data:series:{id}:{s}:{e}:{scope}"),
        _ => format!("stream_data:movie:{id}:{scope}"),
    }
}

/// Convert a scraped usenet stream to the same JSON shape as a DB usenet row so it can
/// go through `format_single_usenet_stream` for consistent template rendering.
fn scraped_usenet_to_value(s: &crate::scrapers::ScrapedUsenetStream) -> Value {
    let mut obj = serde_json::Map::new();
    obj.insert("nzb_guid".into(), json!(s.nzb_guid));
    obj.insert("name".into(), json!(s.name));
    obj.insert("size".into(), json!(s.size));
    obj.insert("indexer".into(), json!(s.indexer));
    obj.insert("source".into(), json!(s.source));
    if let Some(q) = s.parsed.quality.as_deref() {
        obj.insert("quality".into(), json!(q));
    }
    if let Some(r) = s.parsed.resolution.as_deref() {
        obj.insert("resolution".into(), json!(r));
    }
    if let Some(c) = s.parsed.codec.as_deref() {
        obj.insert("codec".into(), json!(c));
    }
    if !s.parsed.languages.is_empty() {
        obj.insert("languages".into(), json!(s.parsed.languages));
    }
    if !s.parsed.hdr.is_empty() {
        obj.insert("hdr_formats".into(), json!(s.parsed.hdr));
    }
    if !s.parsed.audio.is_empty() {
        obj.insert("audio_formats".into(), json!(s.parsed.audio));
    }
    if !s.parsed.channels.is_empty() {
        obj.insert("channels".into(), json!(s.parsed.channels));
    }
    Value::Object(obj)
}

fn scraped_to_json(s: &crate::scrapers::ScrapedStream) -> Value {
    let torrent_type = s.torrent_type;
    json!({
        "info_hash": s.info_hash,
        "name": s.name,
        "source": s.source,
        "quality": s.parsed.quality,
        "resolution": s.parsed.resolution,
        "codec": s.parsed.codec,
        "seeders": s.seeders,
        "size": s.size,
        "torrent_type": torrent_type.as_wire(),
        "is_public": matches!(torrent_type, TorrentType::Public | TorrentType::WebSeed),
        "languages": s.parsed.languages,
        "hdr_formats": s.parsed.hdr,
        "audio_formats": s.parsed.audio,
        "channels": s.parsed.channels,
    })
}

// Default Python-compatible stream templates
const DEFAULT_TITLE_TEMPLATE: &str =
    "{addon.name} {if stream.type = torrent}🧲 {service.shortName} {if service.cached}⚡️{else}⏳{/if}{elif stream.type = usenet}📰 {service.shortName}{elif stream.type = telegram}📱{elif stream.type = youtube}▶️{elif stream.type = http}🌐{else}🔗{/if} {if stream.resolution}{stream.resolution}{/if}";

const DEFAULT_DESC_TEMPLATE: &str =
    "{if stream.hdr_formats}🎨 {stream.hdr_formats|join('|')} {/if}{if stream.quality}📺 {stream.quality} {/if}{if stream.codec}🎞️ {stream.codec} {/if}{if stream.audio_formats}🎵 {stream.audio_formats|join('|')} {/if}{if stream.channels}🔊 {stream.channels|join(' ')}{/if}\n{if stream.size > 0}📦 {stream.size|bytes}{if stream.folderSize > stream.size} / {stream.folderSize|bytes}{/if} {/if}{if stream.seeders > 0}👤 {stream.seeders}{/if}\n{if stream.languages}🌐 {stream.languages|join(' + ')}{/if}\n🔗 {stream.source}{if stream.uploader} | 🧑‍💻 {stream.uploader}{/if}";

/// Build quality detail string for bingeGroup (mirrors Python parser.py:805-815).
fn build_quality_detail(t: &Value) -> String {
    let hdr_display: Vec<String> = t
        .get("hdr_formats")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str())
                .filter(|h| *h != "Unknown")
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default();
    let audio_formats: Vec<String> = t
        .get("audio_formats")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str())
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default();
    let quality = t.get("quality").and_then(|v| v.as_str()).unwrap_or("");
    let codec = t.get("codec").and_then(|v| v.as_str()).unwrap_or("");

    let mut parts = Vec::new();
    if !hdr_display.is_empty() {
        parts.push(format!("🎨 {}", hdr_display.join("|")));
    }
    if !quality.is_empty() {
        parts.push(format!("📺 {quality}"));
    }
    if !codec.is_empty() {
        parts.push(format!("🎞️ {codec}"));
    }
    if !audio_formats.is_empty() {
        parts.push(format!("🎵 {}", audio_formats.join("|")));
    }
    parts.join(" ")
}

fn build_binge_group(t: &Value, addon_name: &str) -> String {
    let addon_name_dashed = addon_name.replace(' ', "-");
    let quality_detail = build_quality_detail(t);
    let resolution_upper = t
        .get("resolution")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .map(|r| r.to_uppercase())
        .unwrap_or_else(|| "N/A".to_string());
    format!("{addon_name_dashed}-{quality_detail}-{resolution_upper}")
}

fn build_p2p_sources(t: &Value, hash: &str) -> Vec<String> {
    let mut tracker_urls = trackers::all_trackers();
    if let Some(announce_list) = t.get("announce_list").and_then(|v| v.as_array()) {
        for item in announce_list {
            if let Some(url) = item.as_str() {
                if !tracker_urls.iter().any(|existing| existing == url) {
                    tracker_urls.push(url.to_string());
                }
            }
        }
    }
    let mut sources: Vec<String> = tracker_urls
        .iter()
        .map(|url| format!("tracker:{url}"))
        .collect();
    sources.push(format!("dht:{hash}"));
    sources
}

fn stream_behavior_filename(t: &Value, filename: &str) -> Option<String> {
    if !filename.is_empty() {
        Some(filename.to_string())
    } else {
        t.get("name")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .map(str::to_string)
    }
}

fn has_user_stream_template(stream_template: Option<&Value>) -> bool {
    stream_template.is_some_and(|tpl| {
        let title = tpl
            .get("t")
            .or_else(|| tpl.get("title"))
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let desc = tpl
            .get("d")
            .or_else(|| tpl.get("description"))
            .and_then(|v| v.as_str())
            .unwrap_or("");
        !title.is_empty() || !desc.is_empty()
    })
}

/// Build the `meta` context object from a serialized `MediaMetaRow` value.
/// Includes season/episode numbers when available (for series).
fn build_media_context(meta: Option<&Value>, season: Option<i32>, episode: Option<i32>) -> Value {
    let mut obj = serde_json::Map::new();
    if let Some(m) = meta {
        for key in &[
            "title",
            "imdb_id",
            "tmdb_id",
            "language",
            "country",
            "website",
            "poster_url",
            "background_url",
            "description",
        ] {
            if let Some(v) = m.get(*key).filter(|v| !v.is_null()) {
                obj.insert(key.to_string(), v.clone());
            }
        }
        for key in &["year", "end_year", "runtime_minutes"] {
            if let Some(v) = m.get(*key).filter(|v| !v.is_null()) {
                obj.insert(key.to_string(), v.clone());
            }
        }
        if let Some(v) = m.get("imdb_rating").filter(|v| !v.is_null()) {
            obj.insert("imdb_rating".to_string(), v.clone());
        }
        if let Some(v) = m.get("media_type").and_then(|v| v.as_str()) {
            obj.insert("type".to_string(), json!(v));
        }
    }
    if let Some(s) = season {
        obj.insert("season".to_string(), json!(s));
    }
    if let Some(e) = episode {
        obj.insert("episode".to_string(), json!(e));
    }
    Value::Object(obj)
}

/// Build a template context for a torrent stream row.
fn build_stream_context(
    t: &Value,
    stream_type: &str,
    addon_name: &str,
    provider: Option<&str>,
    is_cached: bool,
    media_meta: Option<&Value>,
    season: Option<i32>,
    episode: Option<i32>,
) -> Value {
    let mut stream_obj = serde_json::Map::new();
    macro_rules! copy_str {
        ($key:expr) => {
            if let Some(v) = t.get($key).and_then(|v| v.as_str()) {
                stream_obj.insert($key.to_string(), Value::String(v.to_string()));
            }
        };
    }
    macro_rules! copy_num {
        ($key:expr) => {
            if let Some(v) = t.get($key).and_then(|v| v.as_i64()) {
                stream_obj.insert($key.to_string(), Value::Number(v.into()));
            }
        };
    }
    macro_rules! copy_bool {
        ($key:expr) => {
            if let Some(v) = t.get($key).and_then(|v| v.as_bool()) {
                stream_obj.insert($key.to_string(), Value::Bool(v));
            }
        };
    }
    stream_obj.insert("type".to_string(), Value::String(stream_type.to_string()));
    copy_str!("name");
    copy_str!("filename");
    copy_str!("resolution");
    copy_str!("quality");
    copy_str!("codec");
    copy_str!("source");
    copy_str!("release_group");
    copy_str!("uploader");
    copy_str!("bit_depth");
    copy_num!("seeders");
    copy_num!("size");
    copy_num!("folderSize");
    // boolean: cached + quality flags
    stream_obj.insert("cached".to_string(), Value::Bool(is_cached));
    copy_bool!("is_proper");
    copy_bool!("is_repack");
    copy_bool!("is_extended");
    copy_bool!("is_complete");
    copy_bool!("is_dubbed");
    copy_bool!("is_subbed");
    copy_bool!("is_remastered");
    copy_bool!("is_upscaled");
    // arrays: audio_formats, channels, hdr_formats, languages, language_flags
    for arr_key in &[
        "audio_formats",
        "channels",
        "hdr_formats",
        "languages",
        "language_flags",
    ] {
        if let Some(arr) = t.get(*arr_key).and_then(|v| v.as_array()) {
            stream_obj.insert(arr_key.to_string(), Value::Array(arr.clone()));
        }
    }

    let service_obj: Value = if let Some(svc) = provider {
        let short_name = provider_short_name(svc);
        json!({ "name": svc, "shortName": short_name, "cached": is_cached })
    } else {
        json!({})
    };

    json!({
        "stream": Value::Object(stream_obj),
        "service": service_obj,
        "addon": { "name": addon_name },
        "meta": build_media_context(media_meta, season, episode),
    })
}

#[allow(clippy::too_many_arguments)]
fn format_streams(
    torrents: &[Value],
    addon_name: &str,
    host_url: &str,
    secret_str: &str,
    primary_provider: Option<&str>,
    season: Option<i32>,
    episode: Option<i32>,
    stream_template: Option<&Value>,
    cached_hashes: &HashMap<String, bool>,
    media_meta: Option<&Value>,
) -> Vec<Value> {
    let (title_tpl, desc_tpl) = resolve_templates(stream_template);

    torrents
        .iter()
        .filter_map(|t| {
            let hash = t.get("info_hash").and_then(|v| v.as_str())?;
            let size = t.get("size").and_then(|v| v.as_i64());
            let file_index = t.get("file_index").and_then(|v| v.as_i64());
            let filename = t.get("filename").and_then(|v| v.as_str()).unwrap_or("");

            // Build template context and render title + description
            let is_cached =
                primary_provider.is_some() && cached_hashes.get(hash).copied().unwrap_or(false);
            let ctx = build_stream_context(
                t,
                "torrent",
                addon_name,
                primary_provider,
                is_cached,
                media_meta,
                season,
                episode,
            );
            let title_str = template::render(&title_tpl, &ctx);
            let desc_str = template::render(&desc_tpl, &ctx);
            // Use original "name" as fallback if title template produces nothing
            let title_str = if title_str.is_empty() {
                t.get("name")
                    .and_then(|v| v.as_str())
                    .unwrap_or(addon_name)
                    .to_string()
            } else {
                title_str
            };

            let binge_group = build_binge_group(t, addon_name);
            let behavior_filename = stream_behavior_filename(t, filename);

            let mut behavior: serde_json::Map<String, Value> = serde_json::Map::new();
            behavior.insert("bingeGroup".into(), json!(binge_group));
            if let Some(sz) = size.filter(|&s| s > 0) {
                behavior.insert("videoSize".into(), json!(sz));
            }

            let mut obj = serde_json::Map::new();
            obj.insert("name".into(), json!(title_str));
            obj.insert("description".into(), json!(desc_str));

            if !secret_str.is_empty() {
                if let Some(provider) = primary_provider {
                    // Generate debrid proxy URL
                    let url = build_playback_url(
                        host_url, secret_str, provider, hash, filename, season, episode,
                    );
                    obj.insert("url".into(), json!(url));
                    behavior.insert("notWebReady".into(), json!(false));
                    if let Some(fname) = behavior_filename {
                        behavior.insert("filename".into(), json!(fname));
                    }
                    obj.insert("behaviorHints".into(), Value::Object(behavior));
                    if let Some(fi) = file_index {
                        obj.insert("fileIdx".into(), json!(fi as i32));
                    }
                    return Some(Value::Object(obj));
                }
            }

            // No provider — use infoHash for WebTorrent
            behavior.insert("notWebReady".into(), json!(true));
            if let Some(fname) = behavior_filename {
                behavior.insert("filename".into(), json!(fname));
            }
            obj.insert("infoHash".into(), json!(hash));
            obj.insert("sources".into(), json!(build_p2p_sources(t, hash)));
            obj.insert("behaviorHints".into(), Value::Object(behavior));
            if let Some(fi) = file_index {
                obj.insert("fileIdx".into(), json!(fi as i32));
            }

            Some(Value::Object(obj))
        })
        .collect()
}

#[allow(clippy::too_many_arguments)]
fn format_single_stream(
    t: &Value,
    addon_name: &str,
    host_url: &str,
    secret_str: &str,
    primary_provider: Option<&str>,
    season: Option<i32>,
    episode: Option<i32>,
    stream_template: Option<&Value>,
    cached_hashes: &HashMap<String, bool>,
    media_meta: Option<&Value>,
) -> Option<Value> {
    let (title_tpl, desc_tpl) = resolve_templates(stream_template);

    let hash = t.get("info_hash").and_then(|v| v.as_str())?;
    let size = t.get("size").and_then(|v| v.as_i64());
    let file_index = t.get("file_index").and_then(|v| v.as_i64());
    let filename = t.get("filename").and_then(|v| v.as_str()).unwrap_or("");

    let is_cached = primary_provider.is_some() && cached_hashes.get(hash).copied().unwrap_or(false);
    let ctx = build_stream_context(
        t,
        "torrent",
        addon_name,
        primary_provider,
        is_cached,
        media_meta,
        season,
        episode,
    );
    let title_str = template::render(&title_tpl, &ctx);
    let desc_str = template::render(&desc_tpl, &ctx);
    let title_str = if title_str.is_empty() {
        t.get("name")
            .and_then(|v| v.as_str())
            .unwrap_or(addon_name)
            .to_string()
    } else {
        title_str
    };

    let binge_group = build_binge_group(t, addon_name);
    let behavior_filename = stream_behavior_filename(t, filename);
    let mut behavior: serde_json::Map<String, Value> = serde_json::Map::new();
    behavior.insert("bingeGroup".into(), json!(binge_group));
    if let Some(sz) = size.filter(|&s| s > 0) {
        behavior.insert("videoSize".into(), json!(sz));
    }

    let mut obj = serde_json::Map::new();
    obj.insert("name".into(), json!(title_str));
    obj.insert("description".into(), json!(desc_str));

    if !secret_str.is_empty() {
        if let Some(provider) = primary_provider {
            let url = build_playback_url(
                host_url, secret_str, provider, hash, filename, season, episode,
            );
            obj.insert("url".into(), json!(url));
            behavior.insert("notWebReady".into(), json!(false));
            if let Some(fname) = behavior_filename {
                behavior.insert("filename".into(), json!(fname));
            }
            obj.insert("behaviorHints".into(), Value::Object(behavior));
            if let Some(fi) = file_index {
                obj.insert("fileIdx".into(), json!(fi as i32));
            }
            return Some(Value::Object(obj));
        }
    }

    behavior.insert("notWebReady".into(), json!(true));
    if let Some(fname) = behavior_filename {
        behavior.insert("filename".into(), json!(fname));
    }
    obj.insert("infoHash".into(), json!(hash));
    obj.insert("sources".into(), json!(build_p2p_sources(t, hash)));
    obj.insert("behaviorHints".into(), Value::Object(behavior));
    if let Some(fi) = file_index {
        obj.insert("fileIdx".into(), json!(fi as i32));
    }
    Some(Value::Object(obj))
}

fn format_http_stream(
    row: &Value,
    addon_name: &str,
    stream_template: Option<&Value>,
    is_tv: bool,
    media_meta: Option<&Value>,
    season: Option<i32>,
    episode: Option<i32>,
) -> Option<Value> {
    let url = row
        .get("url")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())?;
    let quality = row.get("quality").and_then(|v| v.as_str()).unwrap_or("");
    let resolution = row.get("resolution").and_then(|v| v.as_str()).unwrap_or("");
    let label = if !resolution.is_empty() {
        resolution
    } else if !quality.is_empty() {
        quality
    } else {
        "HTTP"
    };

    let stream_name = row.get("name").and_then(|v| v.as_str()).unwrap_or("");
    let source = row.get("source").and_then(|v| v.as_str()).unwrap_or("");

    let (title_str, desc_str) = if is_tv && !has_user_stream_template(stream_template) {
        let title = format!("{addon_name}\n{stream_name}");
        let description = if !source.is_empty() {
            format!("📺 {source}")
        } else {
            "📺 Live".to_string()
        };
        (title, description)
    } else {
        let (title_tpl, desc_tpl) = resolve_templates(stream_template);

        let mut stream_obj = serde_json::Map::new();
        stream_obj.insert("type".into(), json!("http"));
        for key in &["name", "quality", "resolution", "codec", "source"] {
            if let Some(v) = row
                .get(*key)
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty())
            {
                stream_obj.insert(key.to_string(), json!(v));
            }
        }
        if let Some(sz) = row.get("size").and_then(|v| v.as_i64()) {
            stream_obj.insert("size".into(), json!(sz));
        }
        if let Some(arr) = row.get("languages").and_then(|v| v.as_array()) {
            stream_obj.insert("languages".into(), json!(arr));
        }
        let ctx = json!({
            "stream": Value::Object(stream_obj),
            "service": {},
            "addon": { "name": addon_name },
            "meta": build_media_context(media_meta, season, episode),
        });

        let title_str = template::render(&title_tpl, &ctx);
        let desc_str = template::render(&desc_tpl, &ctx);
        let title_str = if title_str.trim().is_empty() {
            format!("{addon_name} 🌐 {label}")
        } else {
            title_str
        };
        (title_str, desc_str)
    };

    let bh = row.get("behavior_hints").cloned().unwrap_or(json!({}));
    Some(json!({ "name": title_str, "description": desc_str, "url": url, "behaviorHints": bh }))
}

fn format_youtube_stream(
    row: &Value,
    addon_name: &str,
    stream_template: Option<&Value>,
    media_meta: Option<&Value>,
    season: Option<i32>,
    episode: Option<i32>,
) -> Option<Value> {
    let video_id = row
        .get("video_id")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())?;
    let quality = row.get("quality").and_then(|v| v.as_str()).unwrap_or("");
    let resolution = row.get("resolution").and_then(|v| v.as_str()).unwrap_or("");
    let label = if !resolution.is_empty() {
        resolution
    } else if !quality.is_empty() {
        quality
    } else {
        "YouTube"
    };

    let (title_tpl, desc_tpl) = resolve_templates(stream_template);

    let mut stream_obj = serde_json::Map::new();
    stream_obj.insert("type".into(), json!("youtube"));
    for key in &["name", "quality", "resolution", "codec", "source"] {
        if let Some(v) = row
            .get(*key)
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
        {
            stream_obj.insert(key.to_string(), json!(v));
        }
    }
    if let Some(arr) = row.get("languages").and_then(|v| v.as_array()) {
        stream_obj.insert("languages".into(), json!(arr));
    }
    let ctx = json!({
        "stream": Value::Object(stream_obj),
        "service": {},
        "addon": { "name": addon_name },
        "meta": build_media_context(media_meta, season, episode),
    });

    let title_str = template::render(&title_tpl, &ctx);
    let desc_str = template::render(&desc_tpl, &ctx);
    let mut title_str = if title_str.trim().is_empty() {
        format!("{addon_name} ▶️ {label}")
    } else {
        title_str
    };

    // Append geo-restriction label if present
    if let Some(geo_type) = row.get("geo_restriction_type").and_then(|v| v.as_str()) {
        if !geo_type.is_empty() && geo_type != "none" {
            let geo_label = if let Some(countries) = row
                .get("geo_restriction_countries")
                .and_then(|v| v.as_array())
            {
                let c: Vec<&str> = countries.iter().filter_map(|v| v.as_str()).collect();
                format!("{geo_type}: {}", c.join(", "))
            } else {
                geo_type.to_string()
            };
            if !title_str.contains(&geo_label) {
                title_str = format!("{title_str} | {geo_label}");
            }
        }
    }

    Some(json!({ "name": title_str, "description": desc_str, "ytId": video_id }))
}

fn format_telegram_stream(
    row: &Value,
    addon_name: &str,
    host_url: &str,
    secret_str: &str,
    stream_template: Option<&Value>,
    media_meta: Option<&Value>,
    season: Option<i32>,
    episode: Option<i32>,
) -> Option<Value> {
    let chat_id = row
        .get("chat_id")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())?;
    let message_id = row.get("message_id").and_then(|v| v.as_i64())?;
    let quality = row.get("quality").and_then(|v| v.as_str()).unwrap_or("");
    let resolution = row.get("resolution").and_then(|v| v.as_str()).unwrap_or("");
    let label = if !resolution.is_empty() {
        resolution
    } else if !quality.is_empty() {
        quality
    } else {
        "Telegram"
    };

    let url = format!("{host_url}/streaming_provider/{secret_str}/telegram/{chat_id}/{message_id}");

    let (title_tpl, desc_tpl) = resolve_templates(stream_template);
    let mut stream_obj = serde_json::Map::new();
    stream_obj.insert("type".into(), json!("telegram"));
    for key in &["name", "quality", "resolution", "codec", "source"] {
        if let Some(v) = row
            .get(*key)
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
        {
            stream_obj.insert(key.to_string(), json!(v));
        }
    }
    if let Some(sz) = row.get("size").and_then(|v| v.as_i64()) {
        stream_obj.insert("size".into(), json!(sz));
    }
    if let Some(arr) = row.get("languages").and_then(|v| v.as_array()) {
        stream_obj.insert("languages".into(), json!(arr));
    }
    let ctx = json!({
        "stream": Value::Object(stream_obj),
        "service": {},
        "addon": { "name": addon_name },
        "meta": build_media_context(media_meta, season, episode),
    });

    let title_str = template::render(&title_tpl, &ctx);
    let desc_str = template::render(&desc_tpl, &ctx);
    let title_str = if title_str.trim().is_empty() {
        format!("{addon_name} 📱 {label}")
    } else {
        title_str
    };

    Some(json!({
        "name": title_str,
        "description": desc_str,
        "url": url,
        "behaviorHints": { "notWebReady": false }
    }))
}

fn format_acestream_stream(
    row: &Value,
    addon_name: &str,
    mediaflow: Option<&crate::models::user_data::MediaFlowConfig>,
    _stream_template: Option<&Value>,
) -> Option<Value> {
    let mf = mediaflow?;
    let proxy_url = mf.proxy_url.as_deref().filter(|s| !s.is_empty())?;
    let content_id = row
        .get("content_id")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty());
    let info_hash = row
        .get("info_hash")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty());
    if content_id.is_none() && info_hash.is_none() {
        return None;
    }

    // Build MediaFlow acestream URL: {proxy_url}/proxy/acestream/stream?id=...&api_password=...
    let mut params = Vec::new();
    if let Some(id) = content_id {
        params.push(format!("id={}", urlencoding::encode(id)));
    } else if let Some(ih) = info_hash {
        params.push(format!("infohash={}", urlencoding::encode(ih)));
    }
    if let Some(ap) = mf.api_password.as_deref().filter(|s| !s.is_empty()) {
        params.push(format!("api_password={}", urlencoding::encode(ap)));
    }
    let base = proxy_url.trim_end_matches('/');
    let url = format!("{base}/proxy/acestream/stream?{}", params.join("&"));

    let quality = row.get("quality").and_then(|v| v.as_str()).unwrap_or("");
    let resolution = row.get("resolution").and_then(|v| v.as_str()).unwrap_or("");
    let label = if !resolution.is_empty() {
        resolution
    } else if !quality.is_empty() {
        quality
    } else {
        "AceStream"
    };

    // AceStream uses hardcoded formatting (Python doesn't use template engine for this type)
    let raw_name = row.get("name").and_then(|v| v.as_str()).unwrap_or("");
    let mut desc_parts = vec!["📡 AceStream".to_string()];
    if !resolution.is_empty() {
        desc_parts.push(resolution.to_string());
    }
    if !quality.is_empty() {
        desc_parts.push(quality.to_string());
    }
    if let Some(codec) = row
        .get("codec")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
    {
        desc_parts.push(codec.to_string());
    }
    if let Some(src) = row
        .get("source")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty() && *s != "acestream")
    {
        desc_parts.push(format!("| {src}"));
    }

    Some(json!({
        "name": format!("{addon_name}\n{raw_name}"),
        "description": desc_parts.join(" "),
        "url": url,
        "behaviorHints": {
            "notWebReady": false,
            "bingeGroup": format!("{addon_name}-{label}")
        }
    }))
}

/// Extract title/desc templates from stream_template config. Returns owned Strings.
fn resolve_templates(stream_template: Option<&Value>) -> (String, String) {
    let (title, desc) = if let Some(tpl) = stream_template {
        let t = tpl
            .get("t")
            .or_else(|| tpl.get("title"))
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let d = tpl
            .get("d")
            .or_else(|| tpl.get("description"))
            .and_then(|v| v.as_str())
            .unwrap_or("");
        (t, d)
    } else {
        ("", "")
    };
    let title_out = if title.is_empty() {
        DEFAULT_TITLE_TEMPLATE.to_string()
    } else {
        title.to_string()
    };
    let desc_out = if desc.is_empty() {
        DEFAULT_DESC_TEMPLATE.to_string()
    } else {
        desc.to_string()
    };
    (title_out, desc_out)
}

#[allow(clippy::too_many_arguments)]
fn format_single_usenet_stream(
    row: &Value,
    addon_name: &str,
    host_url: &str,
    secret_str: &str,
    provider: Option<&str>,
    season: Option<i32>,
    episode: Option<i32>,
    stream_template: Option<&Value>,
    media_meta: Option<&Value>,
) -> Option<Value> {
    let nzb_guid = row.get("nzb_guid")?.as_str()?;
    let nzb_name = row.get("name").and_then(|v| v.as_str()).unwrap_or("");
    let quality = row.get("quality").and_then(|v| v.as_str()).unwrap_or("");
    let resolution = row.get("resolution").and_then(|v| v.as_str()).unwrap_or("");
    let size = row.get("size").and_then(|v| v.as_i64());

    let label = if !quality.is_empty() {
        quality
    } else if !resolution.is_empty() {
        resolution
    } else {
        "Unknown"
    };

    let (title_tpl, desc_tpl) = resolve_templates(stream_template);

    // Build template context for usenet — mirrors build_stream_context but type="usenet"
    let mut stream_obj = serde_json::Map::new();
    stream_obj.insert(
        "type".to_string(),
        serde_json::Value::String("usenet".to_string()),
    );
    stream_obj.insert(
        "source".to_string(),
        serde_json::Value::String(
            row.get("indexer")
                .and_then(|v| v.as_str())
                .unwrap_or("Usenet")
                .to_string(),
        ),
    );
    if !quality.is_empty() {
        stream_obj.insert(
            "quality".to_string(),
            serde_json::Value::String(quality.to_string()),
        );
    }
    if !resolution.is_empty() {
        stream_obj.insert(
            "resolution".to_string(),
            serde_json::Value::String(resolution.to_string()),
        );
    }
    if let Some(c) = row
        .get("codec")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
    {
        stream_obj.insert(
            "codec".to_string(),
            serde_json::Value::String(c.to_string()),
        );
    }
    if let Some(sz) = size {
        stream_obj.insert("size".to_string(), serde_json::json!(sz));
    }
    if !nzb_name.is_empty() {
        stream_obj.insert(
            "name".to_string(),
            serde_json::Value::String(nzb_name.to_string()),
        );
    }

    let service_obj: Value = if let Some(svc) = provider {
        let short = provider_short_name(svc);
        json!({ "name": svc, "shortName": short })
    } else {
        json!({})
    };

    let ctx = json!({
        "stream": Value::Object(stream_obj),
        "service": service_obj,
        "addon": { "name": addon_name },
        "meta": build_media_context(media_meta, season, episode),
    });

    let title_str = template::render(&title_tpl, &ctx);
    let desc_str = template::render(&desc_tpl, &ctx);
    let title_str = if title_str.trim().is_empty() {
        format!("{addon_name} 📰 {label}")
    } else {
        title_str
    };

    let url = if !secret_str.is_empty() {
        if let Some(svc) = provider {
            match (season, episode) {
                (Some(s), Some(e)) => format!(
                    "{host_url}/streaming_provider/{secret_str}/usenet/{svc}/{nzb_guid}/{s}/{e}"
                ),
                _ => format!("{host_url}/streaming_provider/{secret_str}/usenet/{svc}/{nzb_guid}"),
            }
        } else {
            format!("{host_url}/usenet/{nzb_guid}")
        }
    } else {
        format!("{host_url}/usenet/{nzb_guid}")
    };

    Some(json!({
        "name": title_str,
        "description": desc_str,
        "url": url,
        "behaviorHints": {
            "notWebReady": false,
            "bingeGroup": format!("{addon_name}-{label}-{resolution}"),
            "videoSize": size
        }
    }))
}

// Usenet provider compatibility: see `crate::usenet_compat`.

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
        _ => format!("{host_url}/streaming_provider/{secret_str}/playback/{provider}/{info_hash}"),
    };
    if filename.is_empty() {
        base
    } else {
        format!("{base}/{}", urlencoding::encode(filename))
    }
}
