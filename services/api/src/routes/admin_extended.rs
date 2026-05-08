/// Extended admin endpoints.
///
/// Combines:
///   admin.py         — metadata CRUD, block/unblock, torrent block, TV streams (5 endpoints)
///   contribution_settings.py — contribution settings (4 endpoints)
///   exceptions.py    — exception tracking via Redis (5 endpoints)
///   request_metrics.py — request metrics via Redis (5 endpoints)
///   source_health.py — public indexer source health (1 endpoint)
///
/// All endpoints require admin JWT role unless noted.
///
/// Routes (prefix /api/v1/admin):
///   DELETE /metadata/{media_id}              → delete_metadata
///   POST   /metadata/{media_id}/block        → block_media
///   POST   /metadata/{media_id}/unblock      → unblock_media
///   GET    /media/blocked                    → list_blocked_media
///   POST   /torrent-streams/{stream_id}/block→ block_torrent_stream
///
///   GET    /contribution-settings            → get_contribution_settings
///   PUT    /contribution-settings            → update_contribution_settings
///   GET    /contribution-levels              → get_contribution_levels
///   POST   /contribution-settings/reset      → reset_contribution_settings
///
///   GET    /exceptions/status                → get_exception_status
///   GET    /exceptions                       → list_exceptions
///   GET    /exceptions/{fingerprint}         → get_exception
///   DELETE /exceptions                       → clear_exceptions
///   DELETE /exceptions/{fingerprint}         → clear_single_exception
///
///   GET    /request-metrics/status           → get_request_metrics_status
///   GET    /request-metrics/endpoints        → list_endpoint_stats
///   GET    /request-metrics/endpoints/{method}/{route} → get_endpoint_detail
///   GET    /request-metrics/recent           → list_recent_requests
///   DELETE /request-metrics                  → clear_request_metrics
///
///   GET    /public-indexers/source-health    → get_source_health
use std::sync::Arc;

use axum::{
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::Utc;
use fred::prelude::*;
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::Sha256;

use crate::state::AppState;

// ─── Auth helper ──────────────────────────────────────────────────────────────

fn validate_admin(headers: &HeaderMap, secret_key: &str) -> Option<i64> {
    let token = headers
        .get("authorization")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "))
        .map(str::to_string)?;
    let dot = token.rfind('.')?;
    let (payload_str, sig) = token.split_at(dot);
    let sig = &sig[1..];
    let mut mac = Hmac::<Sha256>::new_from_slice(secret_key.as_bytes()).ok()?;
    mac.update(payload_str.as_bytes());
    let expected: String = mac
        .finalize()
        .into_bytes()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    if expected != sig {
        return None;
    }
    let decoded = URL_SAFE_NO_PAD.decode(payload_str).ok()?;
    let data: Value = serde_json::from_slice(&decoded).ok()?;
    let exp = data["exp"].as_f64()?;
    if exp < Utc::now().timestamp() as f64 {
        return None;
    }
    if data["type"].as_str() != Some("access") {
        return None;
    }
    if data["role"].as_str() != Some("admin") {
        return None;
    }
    data["sub"].as_str()?.parse().ok()
}

fn validate_moderator_or_admin(headers: &HeaderMap, secret_key: &str) -> Option<i64> {
    let token = headers
        .get("authorization")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "))
        .map(str::to_string)?;
    let dot = token.rfind('.')?;
    let (payload_str, sig) = token.split_at(dot);
    let sig = &sig[1..];
    let mut mac = Hmac::<Sha256>::new_from_slice(secret_key.as_bytes()).ok()?;
    mac.update(payload_str.as_bytes());
    let expected: String = mac
        .finalize()
        .into_bytes()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    if expected != sig {
        return None;
    }
    let decoded = URL_SAFE_NO_PAD.decode(payload_str).ok()?;
    let data: Value = serde_json::from_slice(&decoded).ok()?;
    let exp = data["exp"].as_f64()?;
    if exp < Utc::now().timestamp() as f64 {
        return None;
    }
    if data["type"].as_str() != Some("access") {
        return None;
    }
    let role = data["role"].as_str().unwrap_or("user");
    if role != "admin" && role != "moderator" {
        return None;
    }
    data["sub"].as_str()?.parse().ok()
}

fn forbidden() -> axum::response::Response {
    (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response()
}

// ─── Proxy helper ─────────────────────────────────────────────────────────────

async fn proxy_to_python(
    state: &AppState,
    method: reqwest::Method,
    path: &str,
    headers: &HeaderMap,
    body: Option<Value>,
) -> axum::response::Response {
    let py_url = match &state.config.python_proxy_url {
        Some(u) => u.clone(),
        None => {
            return (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(json!({"detail": "Background service unavailable"})),
            )
                .into_response();
        }
    };
    let url = format!("{py_url}{path}");
    let mut req = state.http.request(method, &url);
    if let Some(auth) = headers.get("authorization") {
        req = req.header("authorization", auth);
    }
    if let Some(b) = body {
        req = req.json(&b);
    }
    match req.send().await {
        Ok(r) => {
            let status = StatusCode::from_u16(r.status().as_u16())
                .unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
            let body: Value = r.json().await.unwrap_or(json!({}));
            (status, Json(body)).into_response()
        }
        Err(e) => (
            StatusCode::BAD_GATEWAY,
            Json(json!({"detail": e.to_string()})),
        )
            .into_response(),
    }
}

// ─── admin.py endpoints ───────────────────────────────────────────────────────

#[derive(Deserialize, Serialize)]
pub struct BlockMediaRequest {
    pub reason: Option<String>,
}

#[derive(Deserialize)]
pub struct BlockedMediaQuery {
    pub page: Option<i64>,
    pub page_size: Option<i64>,
    #[serde(rename = "type")]
    pub media_type: Option<String>,
    pub search: Option<String>,
}

/// DELETE /api/v1/admin/metadata/{media_id}
pub async fn delete_metadata(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i64>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let path = format!("/api/v1/admin/metadata/{media_id}");
    proxy_to_python(&state, reqwest::Method::DELETE, &path, &headers, None).await
}

/// POST /api/v1/admin/metadata/{media_id}/block
pub async fn block_media(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i64>,
    Json(body): Json<BlockMediaRequest>,
) -> impl IntoResponse {
    if validate_moderator_or_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let path = format!("/api/v1/admin/metadata/{media_id}/block");
    proxy_to_python(
        &state,
        reqwest::Method::POST,
        &path,
        &headers,
        Some(serde_json::to_value(body).unwrap_or(json!({}))),
    )
    .await
}

/// POST /api/v1/admin/metadata/{media_id}/unblock
pub async fn unblock_media(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i64>,
) -> impl IntoResponse {
    if validate_moderator_or_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let path = format!("/api/v1/admin/metadata/{media_id}/unblock");
    proxy_to_python(&state, reqwest::Method::POST, &path, &headers, None).await
}

/// GET /api/v1/admin/media/blocked
pub async fn list_blocked_media(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<BlockedMediaQuery>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let mut path = "/api/v1/admin/media/blocked?".to_string();
    if let Some(p) = params.page {
        path.push_str(&format!("page={p}&"));
    }
    if let Some(ps) = params.page_size {
        path.push_str(&format!("page_size={ps}&"));
    }
    if let Some(ref mt) = params.media_type {
        path.push_str(&format!("type={mt}&"));
    }
    if let Some(ref s) = params.search {
        path.push_str(&format!("search={}&", urlencoding::encode(s)));
    }
    proxy_to_python(
        &state,
        reqwest::Method::GET,
        path.trim_end_matches('&'),
        &headers,
        None,
    )
    .await
}

/// POST /api/v1/admin/torrent-streams/{stream_id}/block
pub async fn block_torrent_stream(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(stream_id): Path<i64>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let path = format!("/api/v1/admin/torrent-streams/{stream_id}/block");
    proxy_to_python(&state, reqwest::Method::POST, &path, &headers, None).await
}

// ─── contribution_settings.py endpoints ──────────────────────────────────────

#[derive(Serialize, Deserialize)]
pub struct ContributionSettingsUpdate {
    pub auto_approval_threshold: Option<i32>,
    pub points_per_metadata_edit: Option<i32>,
    pub points_per_stream_edit: Option<i32>,
    pub points_for_rejection_penalty: Option<i32>,
    pub contributor_threshold: Option<i32>,
    pub trusted_threshold: Option<i32>,
    pub expert_threshold: Option<i32>,
    pub allow_auto_approval: Option<bool>,
    pub require_reason_for_edits: Option<bool>,
    pub max_pending_suggestions_per_user: Option<i32>,
}

/// GET /api/v1/admin/contribution-settings
pub async fn get_contribution_settings(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let row = sqlx::query_as::<_, (i32, i32, i32, i32, i32, i32, i32, bool, bool, i32)>(
        r#"SELECT auto_approval_threshold, points_per_metadata_edit, points_per_stream_edit,
                  points_for_rejection_penalty, contributor_threshold, trusted_threshold,
                  expert_threshold, allow_auto_approval, require_reason_for_edits,
                  max_pending_suggestions_per_user
           FROM contribution_settings WHERE id = 'default'"#,
    )
    .fetch_optional(&state.pool_ro)
    .await;

    match row {
        Ok(Some((aat, pme, pse, prp, ct, tt, et, aaa, rre, mpsu))) => Json(json!({
            "id": "default",
            "auto_approval_threshold": aat,
            "points_per_metadata_edit": pme,
            "points_per_stream_edit": pse,
            "points_for_rejection_penalty": prp,
            "contributor_threshold": ct,
            "trusted_threshold": tt,
            "expert_threshold": et,
            "allow_auto_approval": aaa,
            "require_reason_for_edits": rre,
            "max_pending_suggestions_per_user": mpsu,
        }))
        .into_response(),
        Ok(None) => {
            // Return defaults
            Json(json!({
                "id": "default",
                "auto_approval_threshold": 100,
                "points_per_metadata_edit": 10,
                "points_per_stream_edit": 5,
                "points_for_rejection_penalty": -5,
                "contributor_threshold": 50,
                "trusted_threshold": 200,
                "expert_threshold": 1000,
                "allow_auto_approval": true,
                "require_reason_for_edits": false,
                "max_pending_suggestions_per_user": 10,
            }))
            .into_response()
        }
        Err(e) => {
            tracing::error!("get_contribution_settings: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

/// PUT /api/v1/admin/contribution-settings
pub async fn update_contribution_settings(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<ContributionSettingsUpdate>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    // Upsert settings row
    let result = sqlx::query(
        r#"INSERT INTO contribution_settings (id) VALUES ('default')
           ON CONFLICT (id) DO NOTHING"#,
    )
    .execute(&state.pool)
    .await;
    if let Err(e) = result {
        tracing::error!("contribution_settings upsert: {e}");
    }

    // Apply individual field updates
    if let Some(v) = body.auto_approval_threshold {
        let _ = sqlx::query(
            "UPDATE contribution_settings SET auto_approval_threshold = $1 WHERE id = 'default'",
        )
        .bind(v)
        .execute(&state.pool)
        .await;
    }
    if let Some(v) = body.points_per_metadata_edit {
        let _ = sqlx::query(
            "UPDATE contribution_settings SET points_per_metadata_edit = $1 WHERE id = 'default'",
        )
        .bind(v)
        .execute(&state.pool)
        .await;
    }
    if let Some(v) = body.points_per_stream_edit {
        let _ = sqlx::query(
            "UPDATE contribution_settings SET points_per_stream_edit = $1 WHERE id = 'default'",
        )
        .bind(v)
        .execute(&state.pool)
        .await;
    }
    if let Some(v) = body.allow_auto_approval {
        let _ = sqlx::query(
            "UPDATE contribution_settings SET allow_auto_approval = $1 WHERE id = 'default'",
        )
        .bind(v)
        .execute(&state.pool)
        .await;
    }
    if let Some(v) = body.require_reason_for_edits {
        let _ = sqlx::query(
            "UPDATE contribution_settings SET require_reason_for_edits = $1 WHERE id = 'default'",
        )
        .bind(v)
        .execute(&state.pool)
        .await;
    }
    if let Some(v) = body.max_pending_suggestions_per_user {
        let _ = sqlx::query("UPDATE contribution_settings SET max_pending_suggestions_per_user = $1 WHERE id = 'default'").bind(v).execute(&state.pool).await;
    }

    // Threshold ordering validation & update
    let ct = body.contributor_threshold;
    let tt = body.trusted_threshold;
    let et = body.expert_threshold;
    if let (Some(c), Some(t)) = (ct, tt) {
        if c >= t {
            return (
                StatusCode::BAD_REQUEST,
                Json(
                    json!({"detail": "Contributor threshold must be less than trusted threshold"}),
                ),
            )
                .into_response();
        }
    }
    if let (Some(t), Some(e)) = (tt, et) {
        if t >= e {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Trusted threshold must be less than expert threshold"})),
            )
                .into_response();
        }
    }
    if let Some(v) = ct {
        let _ = sqlx::query(
            "UPDATE contribution_settings SET contributor_threshold = $1 WHERE id = 'default'",
        )
        .bind(v)
        .execute(&state.pool)
        .await;
    }
    if let Some(v) = tt {
        let _ = sqlx::query(
            "UPDATE contribution_settings SET trusted_threshold = $1 WHERE id = 'default'",
        )
        .bind(v)
        .execute(&state.pool)
        .await;
    }
    if let Some(v) = et {
        let _ = sqlx::query(
            "UPDATE contribution_settings SET expert_threshold = $1 WHERE id = 'default'",
        )
        .bind(v)
        .execute(&state.pool)
        .await;
    }

    Json(json!({"detail": "Contribution settings updated"})).into_response()
}

/// GET /api/v1/admin/contribution-levels
pub async fn get_contribution_levels(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let row = sqlx::query_as::<_, (i32, i32, i32, bool, i32)>(
        r#"SELECT contributor_threshold, trusted_threshold, expert_threshold,
                  allow_auto_approval, auto_approval_threshold
           FROM contribution_settings WHERE id = 'default'"#,
    )
    .fetch_optional(&state.pool_ro)
    .await
    .unwrap_or(None);

    let (ct, tt, et, aaa, aat) = row.unwrap_or((50, 200, 1000, true, 100));

    let levels = json!([
        {"name": "new", "display_name": "New Contributor", "min_points": 0, "max_points": ct - 1, "can_auto_approve": false},
        {"name": "contributor", "display_name": "Contributor", "min_points": ct, "max_points": tt - 1, "can_auto_approve": false},
        {"name": "trusted", "display_name": "Trusted Contributor", "min_points": tt, "max_points": et - 1, "can_auto_approve": aaa && aat <= tt},
        {"name": "expert", "display_name": "Expert Contributor", "min_points": et, "max_points": null, "can_auto_approve": aaa},
    ]);

    Json(json!({
        "levels": levels,
        "current_settings": {
            "contributor_threshold": ct,
            "trusted_threshold": tt,
            "expert_threshold": et,
            "allow_auto_approval": aaa,
            "auto_approval_threshold": aat,
        },
    }))
    .into_response()
}

/// POST /api/v1/admin/contribution-settings/reset
pub async fn reset_contribution_settings(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let _ = sqlx::query("DELETE FROM contribution_settings WHERE id = 'default'")
        .execute(&state.pool)
        .await;
    let _ = sqlx::query("INSERT INTO contribution_settings (id) VALUES ('default')")
        .execute(&state.pool)
        .await;

    Json(json!({"detail": "Contribution settings reset to defaults"})).into_response()
}

// ─── exceptions.py endpoints ─────────────────────────────────────────────────

const EXCEPTION_KEY_PREFIX: &str = "exception:";
const EXCEPTION_INDEX_KEY: &str = "exceptions:index";

#[derive(Deserialize)]
pub struct ExceptionListQuery {
    pub page: Option<i64>,
    pub per_page: Option<i64>,
    pub exception_type: Option<String>,
}

/// GET /api/v1/admin/exceptions/status
pub async fn get_exception_status(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let total: i64 = state
        .redis
        .llen::<i64, _>(EXCEPTION_INDEX_KEY)
        .await
        .unwrap_or(0);

    Json(json!({
        "enabled": true,
        "ttl_seconds": 86400,
        "max_entries": 1000,
        "total_tracked": total,
    }))
    .into_response()
}

/// GET /api/v1/admin/exceptions
pub async fn list_exceptions(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<ExceptionListQuery>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let page = params.page.unwrap_or(1).max(1);
    let per_page = params.per_page.unwrap_or(20).clamp(1, 100);

    let all_keys: Vec<String> = state
        .redis
        .lrange::<Vec<String>, _>(EXCEPTION_INDEX_KEY, 0, -1)
        .await
        .unwrap_or_default();

    let mut items: Vec<Value> = Vec::new();
    for key in &all_keys {
        let data: Option<String> = state
            .redis
            .get::<Option<String>, _>(format!("{EXCEPTION_KEY_PREFIX}{key}"))
            .await
            .unwrap_or(None);
        if let Some(raw) = data {
            if let Ok(v) = serde_json::from_str::<Value>(&raw) {
                if let Some(ref et) = params.exception_type {
                    if v.get("type").and_then(|t| t.as_str()) != Some(et.as_str()) {
                        continue;
                    }
                }
                items.push(v);
            }
        }
    }

    let total = items.len() as i64;
    let pages = (total + per_page - 1) / per_page;
    let offset = ((page - 1) * per_page) as usize;
    let page_items: Vec<Value> = items
        .into_iter()
        .skip(offset)
        .take(per_page as usize)
        .collect();

    Json(json!({
        "items": page_items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }))
    .into_response()
}

/// GET /api/v1/admin/exceptions/{fingerprint}
pub async fn get_exception(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(fingerprint): Path<String>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let data: Option<String> = state
        .redis
        .get::<Option<String>, _>(format!("{EXCEPTION_KEY_PREFIX}{fingerprint}"))
        .await
        .unwrap_or(None);

    match data {
        Some(raw) => match serde_json::from_str::<Value>(&raw) {
            Ok(v) => Json(v).into_response(),
            Err(_) => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
        },
        None => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Exception not found. It may have expired."})),
        )
            .into_response(),
    }
}

/// DELETE /api/v1/admin/exceptions
pub async fn clear_all_exceptions(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let all_keys: Vec<String> = state
        .redis
        .lrange::<Vec<String>, _>(EXCEPTION_INDEX_KEY, 0, -1)
        .await
        .unwrap_or_default();

    let count = all_keys.len() as i64;
    for key in &all_keys {
        let _ = state
            .redis
            .del::<i64, _>(format!("{EXCEPTION_KEY_PREFIX}{key}"))
            .await;
    }
    let _ = state.redis.del::<i64, _>(EXCEPTION_INDEX_KEY).await;

    Json(json!({
        "cleared": count,
        "message": format!("Cleared {count} tracked exception(s)."),
    }))
    .into_response()
}

/// DELETE /api/v1/admin/exceptions/{fingerprint}
pub async fn clear_single_exception(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(fingerprint): Path<String>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let deleted: i64 = state
        .redis
        .del::<i64, _>(format!("{EXCEPTION_KEY_PREFIX}{fingerprint}"))
        .await
        .unwrap_or(0);

    if deleted == 0 {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Exception not found. It may have already expired."})),
        )
            .into_response();
    }

    // Remove from index
    let _ = state
        .redis
        .lrem::<i64, _, _>(EXCEPTION_INDEX_KEY, 0, fingerprint.as_str())
        .await;

    Json(json!({
        "cleared": 1,
        "message": "Exception cleared successfully.",
    }))
    .into_response()
}

// ─── request_metrics.py endpoints ────────────────────────────────────────────

const METRICS_KEY_PREFIX: &str = "req_metric:";
const METRICS_INDEX_KEY: &str = "req_metrics:index";
const RECENT_REQUESTS_KEY: &str = "req_metrics:recent";

#[derive(Deserialize)]
pub struct MetricsEndpointListQuery {
    pub page: Option<i64>,
    pub per_page: Option<i64>,
    pub sort_by: Option<String>,
    pub sort_order: Option<String>,
}

#[derive(Deserialize)]
pub struct RecentRequestsQuery {
    pub page: Option<i64>,
    pub per_page: Option<i64>,
    pub method: Option<String>,
    pub status_code: Option<i64>,
    pub route: Option<String>,
}

/// GET /api/v1/admin/request-metrics/status
pub async fn get_request_metrics_status(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let total_endpoints: i64 = state
        .redis
        .llen::<i64, _>(METRICS_INDEX_KEY)
        .await
        .unwrap_or(0);

    let total_recent: i64 = state
        .redis
        .llen::<i64, _>(RECENT_REQUESTS_KEY)
        .await
        .unwrap_or(0);

    Json(json!({
        "enabled": true,
        "ttl_seconds": 86400,
        "recent_ttl_seconds": 3600,
        "max_recent": 10000,
        "total_endpoints": total_endpoints,
        "total_requests": 0,
        "total_recent": total_recent,
        "unique_visitors": 0,
    }))
    .into_response()
}

/// GET /api/v1/admin/request-metrics/endpoints
pub async fn list_endpoint_stats(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<MetricsEndpointListQuery>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let page = params.page.unwrap_or(1).max(1);
    let per_page = params.per_page.unwrap_or(20).clamp(1, 100);

    let all_keys: Vec<String> = state
        .redis
        .lrange::<Vec<String>, _>(METRICS_INDEX_KEY, 0, -1)
        .await
        .unwrap_or_default();

    let mut items: Vec<Value> = Vec::new();
    for key in &all_keys {
        let data: Option<String> = state
            .redis
            .get::<Option<String>, _>(format!("{METRICS_KEY_PREFIX}{key}"))
            .await
            .unwrap_or(None);
        if let Some(raw) = data {
            if let Ok(v) = serde_json::from_str::<Value>(&raw) {
                items.push(v);
            }
        }
    }

    let total = items.len() as i64;
    let pages = (total + per_page - 1) / per_page;
    let offset = ((page - 1) * per_page) as usize;
    let page_items: Vec<Value> = items
        .into_iter()
        .skip(offset)
        .take(per_page as usize)
        .collect();

    Json(json!({
        "items": page_items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }))
    .into_response()
}

/// GET /api/v1/admin/request-metrics/endpoints/{method}/{route}
pub async fn get_endpoint_detail(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path((method, route)): Path<(String, String)>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let route = if route.starts_with('/') {
        route
    } else {
        format!("/{route}")
    };

    let key = format!("{}{}-{}", METRICS_KEY_PREFIX, method.to_uppercase(), route);
    let data: Option<String> = state
        .redis
        .get::<Option<String>, _>(&key)
        .await
        .unwrap_or(None);

    match data {
        Some(raw) => match serde_json::from_str::<Value>(&raw) {
            Ok(v) => Json(v).into_response(),
            Err(_) => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
        },
        None => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Endpoint metrics not found. It may have expired."})),
        )
            .into_response(),
    }
}

/// GET /api/v1/admin/request-metrics/recent
pub async fn list_recent_requests(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<RecentRequestsQuery>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let page = params.page.unwrap_or(1).max(1);
    let per_page = params.per_page.unwrap_or(20).clamp(1, 100);

    let raw_entries: Vec<String> = state
        .redis
        .lrange::<Vec<String>, _>(RECENT_REQUESTS_KEY, 0, -1)
        .await
        .unwrap_or_default();

    let mut items: Vec<Value> = Vec::new();
    for raw in &raw_entries {
        if let Ok(v) = serde_json::from_str::<Value>(raw) {
            // Apply filters
            if let Some(ref method) = params.method {
                if v.get("method").and_then(|m| m.as_str()) != Some(method.as_str()) {
                    continue;
                }
            }
            if let Some(sc) = params.status_code {
                if v.get("status_code").and_then(|s| s.as_i64()) != Some(sc) {
                    continue;
                }
            }
            if let Some(ref route) = params.route {
                let path_val = v.get("path").and_then(|p| p.as_str()).unwrap_or("");
                if !path_val.contains(route.as_str()) {
                    continue;
                }
            }
            items.push(v);
        }
    }

    let total = items.len() as i64;
    let pages = (total + per_page - 1) / per_page;
    let offset = ((page - 1) * per_page) as usize;
    let page_items: Vec<Value> = items
        .into_iter()
        .skip(offset)
        .take(per_page as usize)
        .collect();

    Json(json!({
        "items": page_items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }))
    .into_response()
}

/// DELETE /api/v1/admin/request-metrics
pub async fn clear_request_metrics(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let all_keys: Vec<String> = state
        .redis
        .lrange::<Vec<String>, _>(METRICS_INDEX_KEY, 0, -1)
        .await
        .unwrap_or_default();

    let mut cleared: i64 = 0;
    for key in &all_keys {
        let n: i64 = state
            .redis
            .del::<i64, _>(format!("{METRICS_KEY_PREFIX}{key}"))
            .await
            .unwrap_or(0);
        cleared += n;
    }
    let _ = state.redis.del::<i64, _>(METRICS_INDEX_KEY).await;
    let n: i64 = state
        .redis
        .del::<i64, _>(RECENT_REQUESTS_KEY)
        .await
        .unwrap_or(0);
    cleared += n;

    Json(json!({
        "cleared": cleared,
        "message": format!("Cleared {cleared} request metrics key(s)."),
    }))
    .into_response()
}

// ─── source_health.py endpoint ────────────────────────────────────────────────

/// GET /api/v1/admin/public-indexers/source-health
pub async fn get_source_health(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    // Proxy to Python which has the PUBLIC_INDEXER_DEFINITIONS and health logic
    proxy_to_python(
        &state,
        reqwest::Method::GET,
        "/api/v1/admin/public-indexers/source-health",
        &headers,
        None,
    )
    .await
}
