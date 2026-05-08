/// Delete-all-watchlist endpoint.
///
/// Route: GET /streaming_provider/{secret_str}/delete_all_watchlist
///
/// Called by Stremio when the user clicks the "🗑️💩🚨 Delete all watchlist" stream item.
/// Resolves the user's primary streaming provider and removes all torrents/items from their
/// active watchlist on that provider.  Implemented for Real-Debrid, AllDebrid, Premiumize,
/// DebridLink, TorBox, StremThru, Offcloud, and EasyDebrid.
use std::sync::Arc;

use axum::{
    body::Body,
    extract::{Path, State},
    http::{header, StatusCode},
    response::{IntoResponse, Response},
};

use crate::{crypto, models::user_data::UserData, providers, state::AppState};

pub async fn delete_all_handler(
    Path(secret_str): Path<String>,
    State(state): State<Arc<AppState>>,
) -> Response {
    match dispatch(&state, &secret_str).await {
        Ok(()) => redirect(format!(
            "{}/static/exceptions/watchlist_deleted.mp4",
            state.config.host_url
        )),
        Err(e) => {
            tracing::warn!("delete_all_watchlist: {e}");
            let video = e.video_file();
            redirect(error_video_url(&state, video))
        }
    }
}

async fn dispatch(state: &AppState, secret_str: &str) -> Result<(), providers::ProviderError> {
    let raw = crypto::resolve_user_data(
        secret_str,
        &state.config.secret_key,
        &state.pool,
        &state.redis,
    )
    .await;
    let user_data: UserData = serde_json::from_value(raw).unwrap_or_default();

    let provider = user_data.get_primary_provider().ok_or_else(|| {
        providers::ProviderError::api("No streaming provider configured", "api_error.mp4")
    })?;

    let token = provider.token.as_deref().ok_or_else(|| {
        providers::ProviderError::api("Provider token is missing", "invalid_token.mp4")
    })?;

    match provider.service.as_str() {
        "realdebrid" => providers::realdebrid::delete_all_torrents(&state.http, token).await,
        "alldebrid" => providers::alldebrid::delete_all_torrents(&state.http, token).await,
        "premiumize" => providers::premiumize::delete_all_torrents(&state.http, token).await,
        "debridlink" => providers::debridlink::delete_all_torrents(&state.http, token).await,
        "torbox" => providers::torbox::delete_all_torrents(&state.http, token).await,
        "stremthru" => providers::stremthru::delete_all_torrents(&state.http, token).await,
        "offcloud" => providers::offcloud::delete_all_torrents(&state.http, token).await,
        "easydebrid" => providers::easydebrid::delete_all_torrents(&state.http, token).await,
        other => Err(providers::ProviderError::api(
            format!("Provider '{other}' does not support delete-all-watchlist"),
            "provider_error.mp4",
        )),
    }
}

fn redirect(url: String) -> Response {
    Response::builder()
        .status(StatusCode::FOUND)
        .header(header::LOCATION, url)
        .header(header::CACHE_CONTROL, "no-store, no-cache, must-revalidate")
        .body(Body::empty())
        .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
}

fn error_video_url(state: &AppState, video_file: &str) -> String {
    if let Some(python_url) = &state.config.python_proxy_url {
        format!("{python_url}/static/exceptions/{video_file}")
    } else {
        format!("{}/static/exceptions/{video_file}", state.config.host_url)
    }
}
