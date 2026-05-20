/// IPTV source management endpoints.
///
/// Routes (prefix /api/v1/import):
///   GET    /sources              → list_iptv_sources
///   GET    /sources/{source_id}  → get_iptv_source
///   PATCH  /sources/{source_id}  → update_iptv_source
///   DELETE /sources/{source_id}  → delete_iptv_source
///   POST   /sources/{source_id}/sync → sync_iptv_source
use std::sync::Arc;

use axum::{
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::{DateTime, Utc};
use hmac::{Hmac, KeyInit, Mac};
use serde::{Deserialize, Serialize};
use serde_json::json;
use sha2::Sha256;

use crate::state::AppState;

// ─── Auth ─────────────────────────────────────────────────────────────────────

fn validate_token(headers: &HeaderMap, secret_key: &str) -> Option<i32> {
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
    let data: serde_json::Value = serde_json::from_slice(&decoded).ok()?;
    let exp = data["exp"].as_f64()?;
    if exp < Utc::now().timestamp() as f64 {
        return None;
    }
    if data["type"].as_str() != Some("access") {
        return None;
    }
    data["sub"].as_str()?.parse().ok()
}

// ─── Shapes ───────────────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct IPTVSourceUpdateRequest {
    pub name: Option<String>,
    pub is_active: Option<bool>,
    pub import_live: Option<bool>,
    pub import_vod: Option<bool>,
    pub import_series: Option<bool>,
}

#[derive(Serialize)]
struct SourceResponse {
    id: i32,
    source_type: String,
    name: String,
    is_public: bool,
    import_live: bool,
    import_vod: bool,
    import_series: bool,
    last_synced_at: Option<DateTime<Utc>>,
    last_sync_stats: Option<serde_json::Value>,
    is_active: bool,
    created_at: DateTime<Utc>,
    has_url: bool,
    has_credentials: bool,
}

// ─── DB row helper ────────────────────────────────────────────────────────────

type SourceRow = (
    i32,
    String,
    String,
    bool,
    bool,
    bool,
    bool,
    Option<DateTime<Utc>>,
    Option<serde_json::Value>,
    bool,
    DateTime<Utc>,
    Option<String>,
    Option<String>,
);

fn row_to_response(r: SourceRow) -> SourceResponse {
    SourceResponse {
        id: r.0,
        source_type: r.1,
        name: r.2,
        is_public: r.3,
        import_live: r.4,
        import_vod: r.5,
        import_series: r.6,
        last_synced_at: r.7,
        last_sync_stats: r.8,
        is_active: r.9,
        created_at: r.10,
        has_url: r.11.is_some(),
        has_credentials: r.12.is_some(),
    }
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// GET /api/v1/import/sources
pub async fn list_iptv_sources(headers: HeaderMap, State(state): State<Arc<AppState>>) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let rows: Vec<SourceRow> = sqlx::query_as(
        r#"SELECT id, source_type::text, name, is_public, import_live, import_vod, import_series,
                  last_synced_at, last_sync_stats, is_active, created_at, m3u_url, encrypted_credentials::text
           FROM iptv_source
           WHERE user_id = $1
           ORDER BY created_at DESC"#,
    )
    .bind(user_id)
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    let sources: Vec<SourceResponse> = rows.into_iter().map(row_to_response).collect();
    let total = sources.len();

    Json(json!({
        "sources": sources,
        "total": total,
    }))
    .into_response()
}

/// GET /api/v1/import/sources/{source_id}
pub async fn get_iptv_source(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(source_id): Path<i32>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let row: Option<SourceRow> = sqlx::query_as(
        r#"SELECT id, source_type::text, name, is_public, import_live, import_vod, import_series,
                  last_synced_at, last_sync_stats, is_active, created_at, m3u_url, encrypted_credentials::text
           FROM iptv_source
           WHERE id = $1 AND user_id = $2"#,
    )
    .bind(source_id)
    .bind(user_id)
    .fetch_optional(&state.pool_ro)
    .await
    .unwrap_or(None);

    match row {
        Some(r) => Json(row_to_response(r)).into_response(),
        None => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Source not found"})),
        )
            .into_response(),
    }
}

/// PATCH /api/v1/import/sources/{source_id}
pub async fn update_iptv_source(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(source_id): Path<i32>,
    Json(body): Json<IPTVSourceUpdateRequest>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    // Verify ownership
    let exists: bool = sqlx::query_scalar(
        "SELECT EXISTS(SELECT 1 FROM iptv_source WHERE id = $1 AND user_id = $2)",
    )
    .bind(source_id)
    .bind(user_id)
    .fetch_one(&state.pool)
    .await
    .unwrap_or(false);

    if !exists {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Source not found"})),
        )
            .into_response();
    }

    if let Some(ref name) = body.name {
        sqlx::query("UPDATE iptv_source SET name = $1 WHERE id = $2")
            .bind(name)
            .bind(source_id)
            .execute(&state.pool)
            .await
            .ok();
    }
    if let Some(v) = body.is_active {
        sqlx::query("UPDATE iptv_source SET is_active = $1 WHERE id = $2")
            .bind(v)
            .bind(source_id)
            .execute(&state.pool)
            .await
            .ok();
    }
    if let Some(v) = body.import_live {
        sqlx::query("UPDATE iptv_source SET import_live = $1 WHERE id = $2")
            .bind(v)
            .bind(source_id)
            .execute(&state.pool)
            .await
            .ok();
    }
    if let Some(v) = body.import_vod {
        sqlx::query("UPDATE iptv_source SET import_vod = $1 WHERE id = $2")
            .bind(v)
            .bind(source_id)
            .execute(&state.pool)
            .await
            .ok();
    }
    if let Some(v) = body.import_series {
        sqlx::query("UPDATE iptv_source SET import_series = $1 WHERE id = $2")
            .bind(v)
            .bind(source_id)
            .execute(&state.pool)
            .await
            .ok();
    }

    let row: Option<SourceRow> = sqlx::query_as(
        r#"SELECT id, source_type::text, name, is_public, import_live, import_vod, import_series,
                  last_synced_at, last_sync_stats, is_active, created_at, m3u_url, encrypted_credentials::text
           FROM iptv_source
           WHERE id = $1"#,
    )
    .bind(source_id)
    .fetch_optional(&state.pool)
    .await
    .unwrap_or(None);

    match row {
        Some(r) => Json(row_to_response(r)).into_response(),
        None => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    }
}

/// DELETE /api/v1/import/sources/{source_id}
pub async fn delete_iptv_source(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(source_id): Path<i32>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let result = sqlx::query("DELETE FROM iptv_source WHERE id = $1 AND user_id = $2")
        .bind(source_id)
        .bind(user_id)
        .execute(&state.pool)
        .await;

    match result {
        Ok(r) if r.rows_affected() == 0 => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Source not found"})),
        )
            .into_response(),
        Ok(_) => Json(json!({"status": "success", "message": "Source deleted"})).into_response(),
        Err(e) => {
            tracing::error!("delete_iptv_source db error: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

/// POST /api/v1/import/sources/{source_id}/sync
pub async fn sync_iptv_source(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(source_id): Path<i32>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    // Verify ownership
    let exists: bool = sqlx::query_scalar(
        "SELECT EXISTS(SELECT 1 FROM iptv_source WHERE id = $1 AND user_id = $2)",
    )
    .bind(source_id)
    .bind(user_id)
    .fetch_one(&state.pool)
    .await
    .unwrap_or(false);

    if !exists {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Source not found"})),
        )
            .into_response();
    }

    // Load source details
    type SourceDetail = (String, Option<String>, Option<String>, bool);
    // (source_type, m3u_url, server_url, is_active)
    let detail: Option<SourceDetail> = sqlx::query_as(
        "SELECT source_type::text, m3u_url, server_url, is_active FROM iptv_source WHERE id = $1",
    )
    .bind(source_id)
    .fetch_optional(&state.pool_ro)
    .await
    .unwrap_or(None);

    let (source_type, m3u_url, _server_url, is_active) = match detail {
        Some(d) => d,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Source not found"})),
            )
                .into_response();
        }
    };

    if !is_active {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Source is not active"})),
        )
            .into_response();
    }

    if source_type.to_uppercase() == "XTREAM" {
        // Xtream sync is complex — return 202 indicating background processing needed
        sqlx::query("UPDATE iptv_source SET last_synced_at = NOW() WHERE id = $1")
            .bind(source_id)
            .execute(&state.pool)
            .await
            .ok();

        return (
            StatusCode::ACCEPTED,
            Json(json!({
                "status": "accepted",
                "message": "Xtream source sync requires background processing. Use import/xtream endpoints directly.",
                "source_id": source_id,
            })),
        )
            .into_response();
    }

    // M3U sync
    let url = match m3u_url {
        Some(u) if !u.is_empty() => u,
        _ => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "M3U source has no URL configured"})),
            )
                .into_response();
        }
    };

    // Fetch M3U content
    let content = match state
        .http
        .get(&url)
        .timeout(std::time::Duration::from_secs(120))
        .send()
        .await
    {
        Ok(r) if r.status().is_success() => r.text().await.unwrap_or_default(),
        Ok(r) => {
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({"detail": format!("Failed to fetch M3U: HTTP {}", r.status())})),
            )
                .into_response();
        }
        Err(e) => {
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({"detail": format!("Failed to fetch M3U: {e}")})),
            )
                .into_response();
        }
    };

    // Parse M3U
    let entries = crate::routes::content::m3u_import::parse_m3u(&content);
    let source_label = format!("IPTV Source #{source_id}");
    let mut imported = 0usize;
    let mut skipped = 0usize;

    for entry in entries.iter().filter(|e| e.entry_type == "tv") {
        if crate::routes::content::m3u_import::import_tv_channel(
            &state.pool,
            &entry.name,
            &entry.url,
            entry.logo.as_deref(),
            &source_label,
            entry.behavior_hints.as_ref(),
        )
        .await
        {
            imported += 1;
        } else {
            skipped += 1;
        }
    }

    // Update last_synced_at
    let sync_stats = json!({"imported": imported, "skipped": skipped});
    sqlx::query(
        "UPDATE iptv_source SET last_synced_at = NOW(), last_sync_stats = $1::jsonb WHERE id = $2",
    )
    .bind(sync_stats)
    .bind(source_id)
    .execute(&state.pool)
    .await
    .ok();

    Json(json!({
        "status": "success",
        "imported": imported,
        "skipped": skipped,
        "source_id": source_id,
    }))
    .into_response()
}
