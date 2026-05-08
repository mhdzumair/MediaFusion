/// Usenet stream playback route.
///
/// Looks up the stored `nzb_url` for a given `nzb_guid` and returns a 302
/// redirect to the direct download/streaming URL.  For Easynews, the URL
/// already contains embedded credentials.
use std::sync::Arc;

use axum::{
    body::Body,
    extract::{Path, State},
    http::{header, StatusCode},
    response::{IntoResponse, Response},
};

use crate::state::AppState;

/// `GET /usenet/{nzb_guid}` — public (credentials embedded in URL)
pub async fn handler(
    Path(nzb_guid): Path<String>,
    State(state): State<Arc<AppState>>,
) -> Response {
    redirect(&state, &nzb_guid).await
}

/// `GET /streaming_provider/{secret_str}/usenet/{provider_name}/{nzb_guid}` — authenticated debrid usenet
pub async fn provider_handler(
    Path((_secret_str, _provider_name, nzb_guid)): Path<(String, String, String)>,
    State(state): State<Arc<AppState>>,
) -> Response {
    // Phase 3 will add provider-specific NZB fetching via debrid.
    // For now, fall back to the direct NZB URL stored in the DB.
    redirect(&state, &nzb_guid).await
}

/// `GET /streaming_provider/{secret_str}/usenet/{provider_name}/{nzb_guid}/{season}/{episode}`
pub async fn provider_seep_handler(
    Path((_secret_str, _provider_name, nzb_guid, _season, _episode)): Path<(String, String, String, i32, i32)>,
    State(state): State<Arc<AppState>>,
) -> Response {
    redirect(&state, &nzb_guid).await
}

async fn redirect(state: &AppState, nzb_guid: &str) -> Response {
    let row: Option<(Option<String>,)> =
        sqlx::query_as("SELECT nzb_url FROM usenet_stream WHERE nzb_guid = $1")
            .bind(nzb_guid)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None);

    match row {
        Some((Some(url),)) if !url.is_empty() => Response::builder()
            .status(StatusCode::FOUND)
            .header(header::LOCATION, url)
            .body(Body::empty())
            .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response()),
        Some(_) => StatusCode::NOT_FOUND.into_response(),
        None => StatusCode::NOT_FOUND.into_response(),
    }
}
