/// Streaming provider playback proxy.
///
/// Routes:
///   GET /{secret_str}/playback/{provider_name}/{info_hash}
///   GET /{secret_str}/playback/{provider_name}/{info_hash}/{filename}
///   GET /{secret_str}/playback/{provider_name}/{info_hash}/{season}/{episode}
///   GET /{secret_str}/playback/{provider_name}/{info_hash}/{season}/{episode}/{filename}
///
/// Flow:
///   1. Decrypt secret_str → UserData → find provider token
///   2. Check Redis cache for previously resolved URL
///   3. Fetch stream announce list from DB
///   4. Call provider-specific resolver (currently: Real-Debrid)
///   5. Cache result → 302 redirect to direct video URL
///   6. On any error → 302 to static error video
use std::sync::Arc;

use axum::{
    body::Body,
    extract::{Path, State},
    http::{header, StatusCode},
    response::{IntoResponse, Response},
};
use fred::prelude::{Expiration, KeysInterface};
use serde::Deserialize;
use sha2::{Digest, Sha256};

use crate::{
    crypto, db, models::user_data::UserData, providers,
    providers::torrents::metadata_update::ProviderFile, state::AppState,
};

const URL_CACHE_TTL: i64 = 3600;

// ─── Route path extractors ────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct PlaybackPath {
    pub secret_str: String,
    pub provider_name: String,
    pub info_hash: String,
}

#[derive(Deserialize)]
pub struct PlaybackPathWithFilename {
    pub secret_str: String,
    pub provider_name: String,
    pub info_hash: String,
    pub filename: String,
}

#[derive(Deserialize)]
pub struct PlaybackPathSeEp {
    pub secret_str: String,
    pub provider_name: String,
    pub info_hash: String,
    pub season: i32,
    pub episode: i32,
}

#[derive(Deserialize)]
pub struct PlaybackPathSeEpFilename {
    pub secret_str: String,
    pub provider_name: String,
    pub info_hash: String,
    pub season: i32,
    pub episode: i32,
    pub filename: String,
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

pub async fn handler_base(
    Path(p): Path<PlaybackPath>,
    State(state): State<Arc<AppState>>,
) -> Response {
    dispatch(
        &state,
        p.secret_str,
        p.provider_name,
        p.info_hash,
        None,
        None,
        None,
    )
    .await
}

pub async fn handler_with_filename(
    Path(p): Path<PlaybackPathWithFilename>,
    State(state): State<Arc<AppState>>,
) -> Response {
    dispatch(
        &state,
        p.secret_str,
        p.provider_name,
        p.info_hash,
        None,
        None,
        Some(p.filename),
    )
    .await
}

pub async fn handler_seep(
    Path(p): Path<PlaybackPathSeEp>,
    State(state): State<Arc<AppState>>,
) -> Response {
    dispatch(
        &state,
        p.secret_str,
        p.provider_name,
        p.info_hash,
        Some(p.season),
        Some(p.episode),
        None,
    )
    .await
}

pub async fn handler_seep_filename(
    Path(p): Path<PlaybackPathSeEpFilename>,
    State(state): State<Arc<AppState>>,
) -> Response {
    dispatch(
        &state,
        p.secret_str,
        p.provider_name,
        p.info_hash,
        Some(p.season),
        Some(p.episode),
        Some(p.filename),
    )
    .await
}

// ─── Core logic ───────────────────────────────────────────────────────────────

async fn dispatch(
    state: &AppState,
    secret_str: String,
    provider_name: String,
    info_hash: String,
    season: Option<i32>,
    episode: Option<i32>,
    filename: Option<String>,
) -> Response {
    let info_hash = info_hash.to_lowercase();

    let video_url = match resolve(
        state,
        &secret_str,
        &provider_name,
        &info_hash,
        season,
        episode,
        filename.as_deref(),
    )
    .await
    {
        Ok(url) => url,
        Err(e) => {
            tracing::warn!("playback error hash={info_hash} provider={provider_name}: {e}");
            error_video_url(state, e.video_file())
        }
    };

    redirect(video_url)
}

async fn resolve(
    state: &AppState,
    secret_str: &str,
    provider_name: &str,
    info_hash: &str,
    season: Option<i32>,
    episode: Option<i32>,
    filename: Option<&str>,
) -> Result<String, providers::ProviderError> {
    // 1. Decrypt user config
    let raw_user_data = crypto::resolve_user_data(
        secret_str,
        &state.config.secret_key,
        &state.pool,
        &state.redis,
    )
    .await;
    let user_data: UserData = serde_json::from_value(raw_user_data).unwrap_or_default();

    // 2. Find provider
    let provider = user_data
        .get_provider_by_name(provider_name)
        .or_else(|| user_data.get_primary_provider())
        .ok_or_else(|| {
            providers::ProviderError::api("No streaming provider configured", "api_error.mp4")
        })?;

    let token = provider.token.as_deref().ok_or_else(|| {
        providers::ProviderError::api("Provider token is missing", "invalid_token.mp4")
    })?;

    // 3. Check Redis cache
    let cache_key = playback_cache_key(secret_str, info_hash, season, episode);
    if let Ok(Some(cached)) = state.redis.get::<Option<Vec<u8>>, _>(&cache_key).await {
        if let Ok(url) = String::from_utf8(cached) {
            return Ok(url);
        }
    }

    // 4. Fetch stream info from DB (announce list, file_index, filename hint)
    let stream_info = db::fetch_stream_playback_info(&state.pool_ro, info_hash, season, episode)
        .await
        .ok_or_else(|| providers::ProviderError::api("Stream not found", "stream_not_found.mp4"))?;

    let resolved_filename = filename.or(stream_info.filename.as_deref());
    let no_file_metadata = stream_info.has_no_files;

    // Build a MediaFlow forward transport when the user has a non-local proxy configured.
    // When the proxy URL is loopback/private the addon and MediaFlow share an IP, so
    // routing debrid API calls through it would not help — use direct calls instead.
    let forward = user_data.mediaflow_config.as_ref().and_then(|cfg| {
        let proxy_url = cfg.proxy_url.as_deref()?;
        let api_password = cfg.api_password.as_deref()?;
        if providers::torrents::transport::MediaFlowForward::is_local(proxy_url) {
            None
        } else {
            Some(providers::torrents::transport::MediaFlowForward::new(
                proxy_url,
                api_password,
            ))
        }
    });
    let fwd = forward.as_ref();

    // 5. Dispatch to provider — realdebrid returns (url, files); others just url.
    //
    // Only providers where forward IS fully wired (api calls routed through MediaFlow)
    // AND whose API accepts an ip= hint receive "{mediaflow_ip}" as user_ip.
    // MediaFlow substitutes that placeholder with its actual public IP before forwarding.
    //
    // Providers with _forward (ignored) make direct API calls, so passing a placeholder
    // would send the literal string "{mediaflow_ip}" to the debrid API — always None there.
    macro_rules! call_provider_simple {
        ($module:path) => {{
            use $module as p;
            let url = p::get_video_url(
                &state.http,
                token,
                info_hash,
                &stream_info.announce_list,
                resolved_filename,
                stream_info.file_index,
                season,
                episode,
                None,
                fwd,
            )
            .await?;
            (url, Vec::<ProviderFile>::new())
        }};
    }

    // ip= hint: only for providers with fully wired forward transport.
    let ip_hint = |has_ip_hint: bool| -> Option<&str> {
        if has_ip_hint && fwd.is_some() {
            Some("{mediaflow_ip}")
        } else {
            None
        }
    };

    let (video_url, provider_files): (String, Vec<ProviderFile>) = match provider.service.as_str() {
        "realdebrid" => {
            // forward wired + ip= form field supported
            providers::torrents::realdebrid::get_video_url(
                &state.http,
                token,
                info_hash,
                &stream_info.announce_list,
                resolved_filename,
                stream_info.file_index,
                season,
                episode,
                ip_hint(true),
                fwd,
            )
            .await?
        }
        "alldebrid" => {
            // forward wired + ip= query/body supported
            use providers::torrents::alldebrid as p;
            let url = p::get_video_url(
                &state.http,
                token,
                info_hash,
                &stream_info.announce_list,
                resolved_filename,
                stream_info.file_index,
                season,
                episode,
                ip_hint(true),
                fwd,
            )
            .await?;
            (url, Vec::<ProviderFile>::new())
        }
        // Providers below make API calls through forward when wired.
        "premiumize" => call_provider_simple!(providers::torrents::premiumize),
        // debridlink: forward wired for API calls; CDN ip= fetched from /proxy/ip internally
        "debridlink" => call_provider_simple!(providers::torrents::debridlink),
        "torbox" => {
            // forward wired + user_ip= query param supported — pass placeholder
            use providers::torrents::torbox as p;
            let url = p::get_video_url(
                &state.http,
                token,
                info_hash,
                &stream_info.announce_list,
                resolved_filename,
                stream_info.file_index,
                season,
                episode,
                ip_hint(true),
                fwd,
            )
            .await?;
            (url, Vec::<ProviderFile>::new())
        }
        "stremthru" => call_provider_simple!(providers::torrents::stremthru),
        "offcloud" => call_provider_simple!(providers::torrents::offcloud),
        // easydebrid: forward wired; X-Forwarded-For is stripped by /proxy/forward — None for user_ip
        "easydebrid" => call_provider_simple!(providers::torrents::easydebrid),
        "seedr" => {
            use providers::torrents::seedr as p;
            let url = p::get_video_url(
                &state.http,
                token,
                info_hash,
                &stream_info.announce_list,
                resolved_filename,
                stream_info.file_index,
                season,
                episode,
                stream_info.size_bytes,
                None,
                fwd,
            )
            .await?;
            (url, Vec::<ProviderFile>::new())
        }
        "pikpak" => call_provider_simple!(providers::torrents::pikpak),
        other => {
            return Err(providers::ProviderError::api(
                format!("Provider '{other}' is not yet supported in the Rust service"),
                "provider_error.mp4",
            ));
        }
    };

    // 6. If no file metadata in DB, store it in the background (future users benefit).
    if no_file_metadata && !provider_files.is_empty() {
        let pool = state.pool.clone();
        let hash = info_hash.to_string();
        let files = provider_files;
        let s = season;
        tokio::spawn(async move {
            providers::torrents::metadata_update::update_metadata(&pool, &hash, &files, s).await;
        });
    }

    // 7. Cache result
    let _ = state
        .redis
        .set::<(), _, _>(
            &cache_key,
            video_url.as_bytes(),
            Some(Expiration::EX(URL_CACHE_TTL)),
            None,
            false,
        )
        .await;

    Ok(video_url)
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

fn playback_cache_key(
    secret_str: &str,
    info_hash: &str,
    season: Option<i32>,
    episode: Option<i32>,
) -> String {
    let raw = format!("{secret_str}_{info_hash}_{season:?}_{episode:?}");
    let mut hasher = Sha256::new();
    hasher.update(raw.as_bytes());
    let hash = hex_encode(&hasher.finalize());
    format!("playback_url:{hash}")
}

fn hex_encode(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn error_video_url(state: &AppState, video_file: &str) -> String {
    format!("{}/static/exceptions/{video_file}", state.config.host_url)
}

fn redirect(url: String) -> Response {
    Response::builder()
        .status(StatusCode::FOUND)
        .header(header::LOCATION, url)
        .header(header::CACHE_CONTROL, "no-store, no-cache, must-revalidate")
        .body(Body::empty())
        .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
}
