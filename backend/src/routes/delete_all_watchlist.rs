use std::sync::Arc;

use axum::{
    http::StatusCode,
    response::{IntoResponse, Json},
};
use serde_json::{json, Value};

use crate::{
    models::{
        stremio::{Meta, MetaItem, MetaPreview},
        user_data::UserData,
    },
    providers,
    state::AppState,
};

const DELETE_ALL_NAME: &str = "🗑️💩 Delete all files";
const DELETE_ALL_DESCRIPTION: &str = "🚨💀⚠ Delete all files in streaming provider";

/// Parse `dl{service}` watchlist pseudo-IDs (e.g. `dlseedr` → `seedr`).
pub fn parse_service(id: &str) -> Option<&str> {
    id.strip_prefix("dl").filter(|s| !s.is_empty())
}

/// Providers that expose a delete-all watchlist action (Python `DELETE_ALL_WATCHLIST_FUNCTIONS`).
pub fn supports_delete_all(service: &str) -> bool {
    service != "easydebrid"
}

pub fn delete_all_meta_preview(host_url: &str, service: &str) -> MetaPreview {
    MetaPreview {
        id: format!("dl{service}"),
        media_type: "movie".into(),
        name: DELETE_ALL_NAME.into(),
        release_info: None,
        poster: Some(format!("{host_url}/static/images/delete_all_poster.jpg")),
        background: Some(format!(
            "{host_url}/static/images/delete_all_background.png"
        )),
        description: Some(DELETE_ALL_DESCRIPTION.into()),
    }
}

pub fn delete_all_meta(host_url: &str, service: &str) -> Meta {
    Meta {
        id: format!("dl{service}"),
        media_type: "movie".into(),
        name: DELETE_ALL_NAME.into(),
        release_info: None,
        description: Some(DELETE_ALL_DESCRIPTION.into()),
        poster: Some(format!("{host_url}/static/images/delete_all_poster.jpg")),
        background: Some(format!(
            "{host_url}/static/images/delete_all_background.png"
        )),
        runtime: None,
        website: None,
        language: None,
        country: None,
        genres: vec![],
        cast: vec![],
        imdb_rating: None,
        videos: vec![],
        links: None,
    }
}

pub fn delete_all_stream_json(
    host_url: &str,
    secret_str: &str,
    service: &str,
    addon_name: &str,
) -> Value {
    json!({
        "name": format!("{addon_name} {} 🗑️💩🚨", service),
        "description": format!("🚨💀⚠ Delete all files in {service} watchlist."),
        "url": format!(
            "{host_url}/streaming_provider/{secret_str}/delete_all_watchlist?provider={service}"
        ),
    })
}

/// Whether this user may access the delete-all item for `service`.
pub fn user_has_delete_all_provider(user_data: &UserData, service: &str) -> bool {
    if !supports_delete_all(service) {
        return false;
    }
    user_data
        .get_provider_by_name(service)
        .is_some_and(|p| p.enable_watchlist_catalogs)
}

pub async fn delete_all_for_service(
    state: &AppState,
    user_data: &UserData,
    service: &str,
) -> Result<(), providers::ProviderError> {
    let provider = user_data.get_provider_by_name(service).ok_or_else(|| {
        providers::ProviderError::api(
            format!("Provider '{service}' not configured"),
            "api_error.mp4",
        )
    })?;

    let token = provider.token.as_deref().ok_or_else(|| {
        providers::ProviderError::api("Provider token is missing", "invalid_token.mp4")
    })?;

    match service {
        "realdebrid" => {
            providers::torrents::realdebrid::delete_all_torrents(&state.http, token).await
        }
        "alldebrid" => {
            providers::torrents::alldebrid::delete_all_torrents(&state.http, token).await
        }
        "premiumize" => {
            providers::torrents::premiumize::delete_all_torrents(&state.http, token).await
        }
        "debridlink" => {
            providers::torrents::debridlink::delete_all_torrents(&state.http, token).await
        }
        "torbox" => providers::torrents::torbox::delete_all_torrents(&state.http, token).await,
        "stremthru" => {
            providers::torrents::stremthru::delete_all_torrents(&state.http, token).await
        }
        "offcloud" => providers::torrents::offcloud::delete_all_torrents(&state.http, token).await,
        "easydebrid" => {
            providers::torrents::easydebrid::delete_all_torrents(&state.http, token).await
        }
        "seedr" => providers::torrents::seedr::delete_all_torrents(&state.http, token).await,
        "pikpak" => providers::torrents::pikpak::delete_all_torrents(&state.http, token).await,
        other => Err(providers::ProviderError::api(
            format!("Provider '{other}' does not support delete-all-watchlist"),
            "provider_error.mp4",
        )),
    }
}

pub fn delete_all_meta_response(
    state: &AppState,
    user_data: &UserData,
    service: &str,
) -> axum::response::Response {
    if !user_has_delete_all_provider(user_data, service) {
        return StatusCode::NOT_FOUND.into_response();
    }
    let item = MetaItem {
        meta: delete_all_meta(&state.config.host_url, service),
    };
    Json(item).into_response()
}

pub fn delete_all_streams_response(
    state: Arc<AppState>,
    user_data: &UserData,
    secret_str: &str,
    service: &str,
) -> axum::response::Response {
    if !user_has_delete_all_provider(user_data, service) {
        return StatusCode::NOT_FOUND.into_response();
    }
    let stream = delete_all_stream_json(
        &state.config.host_url,
        secret_str,
        service,
        &state.config.addon_name,
    );
    Json(json!({"streams": [stream]})).into_response()
}
