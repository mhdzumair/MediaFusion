/// Usenet stream playback routes.
///
/// Route handlers decrypt the user profile, fetch stream info from the DB,
/// inject Newznab credentials via `nzb_url::build_user_scoped_nzb_url`, check
/// the Redis cache, then delegate to the per-provider module under
/// `crate::providers::usenet::*`.
///
/// NZB URL privacy model
/// ─────────────────────
/// Raw NZB URLs (which carry per-user Newznab API keys) are NEVER exposed to
/// third parties.  Instead, we build a MediaFusion-local proxy URL:
///
///   {host_url}/streaming_provider/{secret}/usenet/nzb/{nzb_guid}
///
/// Providers submit this proxy URL.  When the provider fetches it, the handler
/// decrypts the user secret, re-injects their API key, and returns the NZB bytes.
///
/// Exception: when `host_url` is localhost the proxy URL is useless to an
/// external provider.  In that case we skip the proxy and upload the NZB file
/// bytes directly (file-upload path in each provider).
use std::sync::Arc;

use axum::{
    body::Body,
    extract::{Path, State},
    http::{header, StatusCode},
    response::{IntoResponse, Response},
};
use tracing::warn;

use crate::{
    config::AppConfig,
    crypto,
    models::user_data::{StreamingProvider, UserData},
    providers::{
        self,
        usenet::{cache, debrider, easynews, nzb_url, nzbget, sabnzbd, torbox},
    },
    state::AppState,
};

// ─── Public handler ────────────────────────────────────────────────────────────

/// `GET /usenet/{nzb_guid}` — no auth, redirects to stored NZB URL
pub async fn handler(Path(nzb_guid): Path<String>, State(state): State<Arc<AppState>>) -> Response {
    public_redirect(&state, &nzb_guid).await
}

// ─── NZB proxy handler ─────────────────────────────────────────────────────────

/// `GET /streaming_provider/{secret}/usenet/nzb/{nzb_guid}`
///
/// Returns the raw NZB file bytes with the user's Newznab API key injected.
/// This is the endpoint providers hit when MediaFusion submits a proxy URL
/// instead of the raw indexer URL.
pub async fn nzb_proxy_handler(
    Path((secret_str, nzb_guid)): Path<(String, String)>,
    State(state): State<Arc<AppState>>,
) -> Response {
    // Decrypt user profile
    let user_json = crypto::resolve_user_data(
        &secret_str,
        &state.config.secret_key,
        &state.pool_ro,
        &state.redis,
    )
    .await;
    let user_data: UserData = match serde_json::from_value(user_json) {
        Ok(u) => u,
        Err(e) => {
            warn!("nzb_proxy: parse user_data: {e}");
            return StatusCode::UNAUTHORIZED.into_response();
        }
    };

    // Fetch stream from DB
    let Some(stream) = fetch_stream_info(&state, &nzb_guid).await else {
        return StatusCode::NOT_FOUND.into_response();
    };

    // Inject user's API key
    let resolved_url = nzb_url::build_user_scoped_nzb_url(
        &stream.nzb_url,
        stream.source.as_deref(),
        stream.indexer.as_deref(),
        &user_data,
    );
    if resolved_url.is_empty() {
        return StatusCode::NOT_FOUND.into_response();
    }

    // Fetch NZB bytes and stream them back
    match crate::providers::usenet::fetch_nzb_bytes(&state.http, &resolved_url).await {
        Ok(bytes) => Response::builder()
            .status(StatusCode::OK)
            .header(header::CONTENT_TYPE, "application/x-nzb")
            .header(
                header::CONTENT_DISPOSITION,
                format!("attachment; filename=\"{}.nzb\"", stream.name),
            )
            .body(Body::from(bytes))
            .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response()),
        Err(e) => {
            warn!("nzb_proxy {nzb_guid}: {e}");
            StatusCode::BAD_GATEWAY.into_response()
        }
    }
}

// ─── Authenticated provider handlers ──────────────────────────────────────────

/// `GET /streaming_provider/{secret}/usenet/{provider}/{nzb_guid}`
pub async fn provider_handler(
    Path((secret_str, provider_name, nzb_guid)): Path<(String, String, String)>,
    State(state): State<Arc<AppState>>,
) -> Response {
    handle_provider(&state, &secret_str, &provider_name, &nzb_guid, 0, 0).await
}

/// `GET /streaming_provider/{secret}/usenet/{provider}/{nzb_guid}/{season}/{episode}`
pub async fn provider_seep_handler(
    Path((secret_str, provider_name, nzb_guid, season, episode)): Path<(
        String,
        String,
        String,
        i32,
        i32,
    )>,
    State(state): State<Arc<AppState>>,
) -> Response {
    handle_provider(
        &state,
        &secret_str,
        &provider_name,
        &nzb_guid,
        season,
        episode,
    )
    .await
}

// ─── Core dispatch ─────────────────────────────────────────────────────────────

async fn handle_provider(
    state: &AppState,
    secret_str: &str,
    provider_name: &str,
    nzb_guid: &str,
    season: i32,
    episode: i32,
) -> Response {
    // 1. Decrypt user profile
    let user_json = crypto::resolve_user_data(
        secret_str,
        &state.config.secret_key,
        &state.pool_ro,
        &state.redis,
    )
    .await;
    let user_data: UserData = match serde_json::from_value(user_json) {
        Ok(u) => u,
        Err(e) => {
            warn!("usenet playback: parse user_data: {e}");
            return make_redirect(&error_video_url(state, "invalid_token.mp4"));
        }
    };

    // 2. Fetch stream from DB
    let Some(stream) = fetch_stream_info(state, nzb_guid).await else {
        return make_redirect(&error_video_url(state, "stream_not_found.mp4"));
    };

    // 3. Redis cache check
    let ck = cache::cache_key(secret_str, nzb_guid, season, episode);
    if let Some(cached) = cache::get(&state.redis, &ck).await {
        return make_redirect(&cached);
    }

    // 4. Locate provider config in user's profile
    let all_providers = collect_providers(&user_data);
    let provider_cfg = all_providers
        .iter()
        .find(|p| p.service.eq_ignore_ascii_case(provider_name) && p.enabled)
        .copied();

    // 5. Inject user's Newznab API key into the stored NZB URL
    let fallback_url = nzb_url::build_user_scoped_nzb_url(
        &stream.nzb_url,
        stream.source.as_deref(),
        stream.indexer.as_deref(),
        &user_data,
    );

    // Build a MediaFusion-proxied submission URL so raw indexer credentials
    // are never sent to third-party providers.  Falls back to direct file-upload
    // when host_url is localhost (the proxy URL would be unreachable externally).
    let submission_url = if is_localhost_url(&state.config.host_url) {
        String::new()
    } else {
        format!(
            "{}/streaming_provider/{}/usenet/nzb/{}",
            state.config.host_url.trim_end_matches('/'),
            secret_str,
            nzb_guid
        )
    };
    tracing::debug!(
        provider = provider_name,
        nzb_guid,
        raw_url = stream.nzb_url.as_str(),
        has_apikey = fallback_url.contains("apikey="),
        proxy_url = submission_url.as_str(),
        source = ?stream.source,
        indexer = ?stream.indexer,
        "usenet playback: NZB URL resolved"
    );

    // Build a MediaFlow forward transport when the user has a non-local proxy configured.
    let forward = user_data.mediaflow_config.as_ref().and_then(|cfg| {
        let proxy_url = cfg.proxy_url.as_deref()?;
        let api_password = cfg.api_password.as_deref()?;
        if crate::providers::torrents::transport::MediaFlowForward::is_local(proxy_url) {
            None
        } else {
            Some(
                crate::providers::torrents::transport::MediaFlowForward::new(
                    proxy_url,
                    api_password,
                ),
            )
        }
    });
    let fwd = forward.as_ref();

    // 6. Dispatch to provider
    let result = dispatch(
        &state.http,
        &state.config,
        provider_name,
        provider_cfg,
        &submission_url,
        &fallback_url,
        &stream.nzb_guid,
        &stream.name,
        season,
        episode,
        fwd,
    )
    .await;

    // 7. Cache successful result and redirect; on error redirect to exception video
    match result {
        Ok(url) => {
            cache::set(&state.redis, &ck, &url).await;
            make_redirect(&url)
        }
        Err(e) => {
            warn!("usenet playback {provider_name}/{nzb_guid}: {e}");
            make_redirect(&error_video_url(state, e.video_file()))
        }
    }
}

/// Dispatch to the per-provider module.
///
/// `submission_url`: MediaFusion proxy URL for the NZB file (empty when localhost).
/// `fallback_url`:   Resolved indexer URL with user's API key injected (used for
///                   direct file-upload when submission_url is empty, or when the
///                   provider needs to fetch the bytes itself as a fallback).
#[allow(clippy::too_many_arguments)]
async fn dispatch(
    http: &reqwest::Client,
    config: &AppConfig,
    provider_name: &str,
    provider_cfg: Option<&StreamingProvider>,
    submission_url: &str,
    fallback_url: &str,
    nzb_guid: &str,
    name: &str,
    season: i32,
    episode: i32,
    fwd: Option<&crate::providers::torrents::transport::MediaFlowForward>,
) -> Result<String, providers::ProviderError> {
    match provider_name.to_lowercase().as_str() {
        "torbox" => {
            let token = provider_cfg
                .and_then(|p| p.token.as_deref())
                .unwrap_or_default();
            torbox::get_url(
                http,
                token,
                submission_url,
                fallback_url,
                nzb_guid,
                name,
                season,
                episode,
                fwd,
            )
            .await
        }
        "easynews" => {
            let (username, password) = easynews_credentials(provider_cfg, config);
            easynews::get_url(http, &username, &password, name, season, episode, fwd).await
        }
        "debrider" => {
            let token = provider_cfg
                .and_then(|p| p.token.as_deref())
                .unwrap_or_default();
            let nzb_url = if !submission_url.is_empty() {
                submission_url
            } else {
                fallback_url
            };
            debrider::get_url(http, token, nzb_url, name, season, episode, fwd).await
        }
        "stremio_nntp" => {
            let url = if !submission_url.is_empty() {
                submission_url
            } else {
                fallback_url
            };
            if url.is_empty() {
                Err(providers::ProviderError::api(
                    "stremio_nntp: no NZB URL (Newznab indexer not configured?)",
                    "stream_not_found.mp4",
                ))
            } else {
                Ok(url.to_string())
            }
        }
        "sabnzbd" | "nzbdav" => {
            let raw =
                provider_cfg.and_then(|p| p.sabnzbd_config.as_ref().or(p.nzbdav_config.as_ref()));
            match raw {
                Some(cfg) => {
                    sabnzbd::get_url(
                        http,
                        cfg,
                        submission_url,
                        fallback_url,
                        name,
                        season,
                        episode,
                    )
                    .await
                }
                None => Err(providers::ProviderError::api(
                    format!("{provider_name}: no config found in user profile"),
                    "invalid_config.mp4",
                )),
            }
        }
        "nzbget" => {
            let raw = provider_cfg.and_then(|p| p.nzbget_config.as_ref());
            match raw {
                Some(cfg) => {
                    nzbget::get_url(
                        http,
                        cfg,
                        submission_url,
                        fallback_url,
                        name,
                        season,
                        episode,
                    )
                    .await
                }
                None => Err(providers::ProviderError::api(
                    "nzbget: no config found in user profile",
                    "invalid_config.mp4",
                )),
            }
        }
        _ => {
            let url = if !submission_url.is_empty() {
                submission_url
            } else {
                fallback_url
            };
            if !url.is_empty() {
                Ok(url.to_string())
            } else {
                Err(providers::ProviderError::api(
                    format!("unknown provider '{provider_name}' with no NZB URL"),
                    "stream_not_found.mp4",
                ))
            }
        }
    }
}

// ─── DB fetch ─────────────────────────────────────────────────────────────────

struct UsenetStreamInfo {
    nzb_url: String,
    nzb_guid: String,
    indexer: Option<String>,
    source: Option<String>,
    name: String,
}

async fn fetch_stream_info(state: &AppState, nzb_guid: &str) -> Option<UsenetStreamInfo> {
    type Row = (
        Option<String>,
        String,
        Option<String>,
        Option<String>,
        String,
    );
    let (nzb_url, nzb_guid, indexer, source, name): Row = sqlx::query_as::<_, Row>(
        r#"
            SELECT us.nzb_url, us.nzb_guid, us.indexer, st.source, st.name
            FROM usenet_stream us
            JOIN stream st ON us.stream_id = st.id
            WHERE us.nzb_guid = $1
            LIMIT 1
            "#,
    )
    .bind(nzb_guid)
    .fetch_optional(&state.pool_ro)
    .await
    .ok()
    .flatten()?;

    Some(UsenetStreamInfo {
        nzb_url: nzb_url.unwrap_or_default(),
        nzb_guid,
        indexer,
        source,
        name,
    })
}

// ─── Helpers ───────────────────────────────────────────────────────────────────

fn collect_providers(user_data: &UserData) -> Vec<&StreamingProvider> {
    let mut v: Vec<&StreamingProvider> = user_data.streaming_providers.iter().collect();
    if let Some(sp) = &user_data.streaming_provider {
        v.push(sp);
    }
    v
}

fn error_video_url(state: &AppState, video_file: &str) -> String {
    format!(
        "{}/static/exceptions/{video_file}",
        state.config.host_url.trim_end_matches('/')
    )
}

fn make_redirect(url: &str) -> Response {
    Response::builder()
        .status(StatusCode::FOUND)
        .header(header::LOCATION, url)
        .header(header::CACHE_CONTROL, "no-store, no-cache, must-revalidate")
        .body(Body::empty())
        .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
}

async fn public_redirect(state: &AppState, nzb_guid: &str) -> Response {
    let row: Option<(Option<String>,)> =
        sqlx::query_as("SELECT nzb_url FROM usenet_stream WHERE nzb_guid = $1")
            .bind(nzb_guid)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None);

    match row {
        Some((Some(url),)) if !url.is_empty() => make_redirect(&url),
        _ => StatusCode::NOT_FOUND.into_response(),
    }
}

/// Returns true when `url` points at a loopback address or `localhost`,
/// meaning any proxy URL built from it would be unreachable by external providers.
fn is_localhost_url(url: &str) -> bool {
    let lower = url.to_lowercase();
    let rest = lower
        .strip_prefix("https://")
        .or_else(|| lower.strip_prefix("http://"))
        .unwrap_or(&lower);
    // Strip optional userinfo
    let rest = rest.find('@').map(|i| &rest[i + 1..]).unwrap_or(rest);
    let host = rest
        .split('/')
        .next()
        .unwrap_or("")
        .split(':')
        .next()
        .unwrap_or("");
    matches!(host, "localhost" | "127.0.0.1" | "::1" | "0.0.0.0")
        || host.starts_with("192.168.")
        || host.starts_with("10.")
        || host.starts_with("172.")
}

fn easynews_credentials(
    provider: Option<&StreamingProvider>,
    _config: &AppConfig,
) -> (String, String) {
    if let Some(p) = provider {
        // `email` field holds the EasyNews username
        let u = p.email.as_deref().unwrap_or_default();
        let pw = p.password.as_deref().unwrap_or_default();
        if !u.is_empty() && !pw.is_empty() {
            return (u.to_string(), pw.to_string());
        }
        if let Some(cfg) = &p.easynews_config {
            let u = cfg
                .get("username")
                .or_else(|| cfg.get("email"))
                .and_then(|v| v.as_str())
                .unwrap_or_default();
            let pw = cfg
                .get("password")
                .and_then(|v| v.as_str())
                .unwrap_or_default();
            if !u.is_empty() && !pw.is_empty() {
                return (u.to_string(), pw.to_string());
            }
        }
    }
    (String::new(), String::new())
}
