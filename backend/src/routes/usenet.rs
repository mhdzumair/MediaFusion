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
    http::{header, Method, StatusCode},
    response::{IntoResponse, Response},
};
use tracing::warn;

use crate::{
    config::AppConfig,
    crypto,
    models::user_data::{StreamingProvider, UserData},
    providers::{
        self,
        usenet::{cache, debrider, easynews, nzb_url, nzbdav, nzbget, sabnzbd, torbox},
    },
    routes::{playback::playback_redirect, playback_dedup},
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
    let user_json = match crypto::resolve_user_data(
        &secret_str,
        &state.config.secret_key,
        &state.pool_ro,
        &state.redis,
    )
    .await
    {
        Ok(v) => v,
        Err(e) => {
            tracing::debug!("nzb_proxy: {e}");
            return StatusCode::UNPROCESSABLE_ENTITY.into_response();
        }
    };
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

/// `GET|HEAD /streaming_provider/{secret}/usenet/{provider}/{nzb_guid}`
pub async fn provider_handler(
    method: Method,
    Path((secret_str, provider_name, nzb_guid)): Path<(String, String, String)>,
    State(state): State<Arc<AppState>>,
) -> Response {
    handle_provider(
        method,
        &state,
        &secret_str,
        &provider_name,
        &nzb_guid,
        0,
        0,
        None,
    )
    .await
}

/// `GET|HEAD /streaming_provider/{secret}/usenet/{provider}/{nzb_guid}/{season}/{episode}`
pub async fn provider_seep_handler(
    method: Method,
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
        method,
        &state,
        &secret_str,
        &provider_name,
        &nzb_guid,
        season,
        episode,
        None,
    )
    .await
}

/// `GET|HEAD /streaming_provider/{secret}/usenet/{provider}/{nzb_guid}/{filename}`
pub async fn provider_filename_handler(
    method: Method,
    Path((secret_str, provider_name, nzb_guid, filename)): Path<(String, String, String, String)>,
    State(state): State<Arc<AppState>>,
) -> Response {
    handle_provider(
        method,
        &state,
        &secret_str,
        &provider_name,
        &nzb_guid,
        0,
        0,
        Some(filename.as_str()),
    )
    .await
}

/// `GET|HEAD /streaming_provider/{secret}/usenet/{provider}/{nzb_guid}/{season}/{episode}/{filename}`
pub async fn provider_seep_filename_handler(
    method: Method,
    Path((secret_str, provider_name, nzb_guid, season, episode, filename)): Path<(
        String,
        String,
        String,
        i32,
        i32,
        String,
    )>,
    State(state): State<Arc<AppState>>,
) -> Response {
    handle_provider(
        method,
        &state,
        &secret_str,
        &provider_name,
        &nzb_guid,
        season,
        episode,
        Some(filename.as_str()),
    )
    .await
}

// ─── Core dispatch ─────────────────────────────────────────────────────────────

async fn handle_provider(
    method: Method,
    state: &AppState,
    secret_str: &str,
    provider_name: &str,
    nzb_guid: &str,
    season: i32,
    episode: i32,
    filename: Option<&str>,
) -> Response {
    // 1. Decrypt user profile
    let user_json = match crypto::resolve_user_data(
        secret_str,
        &state.config.secret_key,
        &state.pool_ro,
        &state.redis,
    )
    .await
    {
        Ok(v) => v,
        Err(e) => {
            tracing::debug!("usenet playback: {e}");
            return playback_redirect(method, error_video_url(state, "invalid_config.mp4"));
        }
    };
    let user_data: UserData = match serde_json::from_value(user_json) {
        Ok(u) => u,
        Err(e) => {
            warn!("usenet playback: parse user_data: {e}");
            return playback_redirect(method, error_video_url(state, "invalid_token.mp4"));
        }
    };

    // 2. Fetch stream from DB
    let Some(stream) = fetch_stream_info(state, nzb_guid).await else {
        return playback_redirect(method, error_video_url(state, "stream_not_found.mp4"));
    };

    // 3. Redis cache check, then deduplicate concurrent HEAD/GET resolution.
    let ck = cache::cache_key(secret_str, nzb_guid, season, episode);
    if let Some(cached) = cache::get(&state.redis, &ck).await {
        return playback_redirect(method, cached);
    }

    // 4. Locate provider config in user's profile
    let all_providers = collect_providers(&user_data);
    let provider_cfg = all_providers
        .iter()
        .find(|p| p.service.eq_ignore_ascii_case(provider_name) && p.enabled)
        .copied();

    // 5. Resolve NZB URL — file-imported NZBs use signed download URLs (Python parity).
    let fallback_url = if stream.nzb_url.is_empty() {
        crate::util::nzb_storage::generate_signed_nzb_url(&state.config, nzb_guid)
    } else {
        nzb_url::build_user_scoped_nzb_url(
            &stream.nzb_url,
            stream.source.as_deref(),
            stream.indexer.as_deref(),
            &user_data,
        )
    };

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

    match playback_dedup::prepare_resolve(&state.redis, &ck).await {
        playback_dedup::DedupWaitResult::Cached(url) => return playback_redirect(method, url),
        playback_dedup::DedupWaitResult::TimedOut => {
            tracing::debug!(
                provider = %provider_name,
                nzb_guid = %nzb_guid,
                "usenet playback timed out waiting for peer; retrying lock"
            );
            match playback_dedup::try_ready_after_wait(&state.redis, &ck).await {
                playback_dedup::DedupWaitResult::Cached(url) => {
                    return playback_redirect(method, url)
                }
                playback_dedup::DedupWaitResult::ReadyToResolve => {}
                playback_dedup::DedupWaitResult::TimedOut => {
                    return playback_redirect(
                        method,
                        error_video_url(
                            state,
                            playback_dedup::playback_resolve_timed_out().video_file(),
                        ),
                    );
                }
            }
        }
        playback_dedup::DedupWaitResult::ReadyToResolve => {}
    }

    let resolve_work = async {
        let lock_guard = playback_dedup::acquire_resolve_lock(&state.redis, &ck)
            .await
            .ok_or_else(playback_dedup::playback_resolve_timed_out)?;

        if let Some(cached) = cache::get(&state.redis, &ck).await {
            lock_guard.release().await;
            return Ok(cached);
        }

        let result = match tokio::time::timeout(
            playback_dedup::holder_resolve_timeout(),
            dispatch(
                &state.debrid_http,
                &state.config,
                &state.pool_ro,
                provider_name,
                provider_cfg,
                &submission_url,
                &fallback_url,
                &stream.nzb_guid,
                &stream.name,
                filename,
                season,
                episode,
                fwd,
            ),
        )
        .await
        {
            Ok(r) => r,
            Err(_) => {
                warn!("usenet playback {provider_name}/{nzb_guid}: provider dispatch timed out");
                Err(playback_dedup::playback_resolve_timed_out())
            }
        };

        if let Ok(ref url) = result {
            cache::set(&state.redis, &ck, url).await;
        }
        lock_guard.release().await;
        result
    };

    let result = if method == Method::HEAD {
        match tokio::time::timeout(playback_dedup::HEAD_RESOLVE_BUDGET, resolve_work).await {
            Ok(r) => r,
            Err(_) => {
                tracing::debug!(
                    provider = %provider_name,
                    nzb_guid = %nzb_guid,
                    "usenet playback HEAD budget exceeded; releasing lock for follow-up GET"
                );
                Err(playback_dedup::playback_resolve_timed_out())
            }
        }
    } else {
        resolve_work.await
    };

    match result {
        Ok(url) => playback_redirect(method, url),
        Err(e) => {
            e.log(&format!("usenet playback {provider_name}/{nzb_guid}"));
            playback_redirect(method, error_video_url(state, e.video_file()))
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
    pool_ro: &sqlx::PgPool,
    provider_name: &str,
    provider_cfg: Option<&StreamingProvider>,
    submission_url: &str,
    fallback_url: &str,
    nzb_guid: &str,
    name: &str,
    filename: Option<&str>,
    season: i32,
    episode: i32,
    fwd: Option<&crate::providers::torrents::transport::MediaFlowForward>,
) -> Result<String, providers::ProviderError> {
    let episode_air_date =
        resolve_usenet_episode_air_date_iso(pool_ro, nzb_guid, season, episode).await;
    let air_date_ref = episode_air_date.as_deref();

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
            let raw = provider_cfg.and_then(|p| {
                if provider_name == "nzbdav" {
                    p.nzbdav_config.as_ref()
                } else {
                    p.sabnzbd_config.as_ref()
                }
            });
            match raw {
                Some(cfg) if provider_name == "nzbdav" => {
                    nzbdav::get_url(
                        http,
                        cfg,
                        submission_url,
                        fallback_url,
                        name,
                        filename,
                        season,
                        episode,
                        air_date_ref,
                    )
                    .await
                }
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

/// Return catalog episode air date as YYYY-MM-DD for dated Usenet release names.
async fn resolve_usenet_episode_air_date_iso(
    pool: &sqlx::PgPool,
    nzb_guid: &str,
    season: i32,
    episode: i32,
) -> Option<String> {
    if season <= 0 || episode <= 0 {
        return None;
    }
    sqlx::query_scalar::<_, String>(
        r#"
        SELECT e.air_date::text
        FROM usenet_stream us
        JOIN stream_media_link sml ON sml.stream_id = us.stream_id
        JOIN series_metadata sm ON sm.media_id = sml.media_id
        JOIN season s ON s.series_id = sm.id AND s.season_number = $2
        JOIN episode e ON e.season_id = s.id AND e.episode_number = $3
        WHERE us.nzb_guid = $1
        LIMIT 1
        "#,
    )
    .bind(nzb_guid)
    .bind(season)
    .bind(episode)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten()
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

async fn public_redirect(state: &AppState, nzb_guid: &str) -> Response {
    let row: Option<(Option<String>,)> =
        sqlx::query_as("SELECT nzb_url FROM usenet_stream WHERE nzb_guid = $1")
            .bind(nzb_guid)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None);

    match row {
        Some((Some(url),)) if !url.is_empty() => playback_redirect(Method::GET, url),
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
