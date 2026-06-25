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
    Json,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use chrono::Utc;
use fred::prelude::*;
use hmac::{Hmac, KeyInit, Mac};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::Sha256;

use crate::{
    db::{MediaId, StreamId, contribution_defaults},
    state::AppState,
};

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
    /// "blocked" (default) | "nsfw_flagged" | "nsfw_reviewed"
    pub filter: Option<String>,
}

/// DELETE /api/v1/admin/metadata/{media_id}
pub async fn delete_metadata(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<MediaId>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    // Delete streams that are ONLY linked to this media (no other media links)
    let _ = sqlx::query(
        "DELETE FROM stream WHERE id IN (
            SELECT sml.stream_id FROM stream_media_link sml
            WHERE sml.media_id = $1
            AND NOT EXISTS (
                SELECT 1 FROM stream_media_link sml2
                WHERE sml2.stream_id = sml.stream_id AND sml2.media_id <> $1
            )
        )",
    )
    .bind(media_id.0)
    .execute(&state.pool)
    .await;

    // Now delete the media (cascades to stream_media_link and media_catalog_link)
    match sqlx::query("DELETE FROM media WHERE id = $1")
        .bind(media_id.0)
        .execute(&state.pool)
        .await
    {
        Ok(r) if r.rows_affected() == 0 => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Media not found"})),
        )
            .into_response(),
        Ok(_) => Json(json!({"message": "Metadata and associated streams deleted successfully"}))
            .into_response(),
        Err(e) => {
            tracing::error!("delete_metadata: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

/// POST /api/v1/admin/metadata/{media_id}/block
pub async fn block_media(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<MediaId>,
    Json(body): Json<BlockMediaRequest>,
) -> impl IntoResponse {
    let user_id = match validate_moderator_or_admin(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => return forbidden(),
    };
    match sqlx::query(
        r#"UPDATE media
           SET is_blocked = true,
               blocked_at = NOW(),
               blocked_by_user_id = $1,
               block_reason = $2
           WHERE id = $3"#,
    )
    .bind(user_id as i32)
    .bind(&body.reason)
    .bind(media_id.0)
    .execute(&state.pool)
    .await
    {
        Ok(r) if r.rows_affected() == 0 => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Media not found"})),
        )
            .into_response(),
        Ok(_) => {
            let blocked_at = Utc::now();
            Json(json!({
                "media_id": media_id,
                "is_blocked": true,
                "blocked_at": blocked_at,
                "message": "Media blocked successfully",
            }))
            .into_response()
        }
        Err(e) => {
            tracing::error!("block_media: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

/// POST /api/v1/admin/metadata/{media_id}/unblock
pub async fn unblock_media(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<MediaId>,
) -> impl IntoResponse {
    if validate_moderator_or_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    match sqlx::query(
        r#"UPDATE media
           SET is_blocked = false,
               blocked_at = NULL,
               blocked_by_user_id = NULL,
               block_reason = NULL
           WHERE id = $1"#,
    )
    .bind(media_id.0)
    .execute(&state.pool)
    .await
    {
        Ok(r) if r.rows_affected() == 0 => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Media not found"})),
        )
            .into_response(),
        Ok(_) => Json(json!({
            "media_id": media_id,
            "is_blocked": false,
            "message": "Media unblocked successfully",
        }))
        .into_response(),
        Err(e) => {
            tracing::error!("unblock_media: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

#[derive(Deserialize)]
pub struct BulkMediaIdsRequest {
    pub ids: Vec<i32>,
    pub reason: Option<String>,
}

/// POST /api/v1/admin/metadata/bulk-block
pub async fn bulk_block_media(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<BulkMediaIdsRequest>,
) -> impl IntoResponse {
    let user_id = match validate_moderator_or_admin(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => return forbidden(),
    };
    if body.ids.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "ids must not be empty"})),
        )
            .into_response();
    }
    match sqlx::query(
        "UPDATE media SET is_blocked = true, blocked_at = NOW(), blocked_by_user_id = $1, block_reason = $2 WHERE id = ANY($3)"
    )
    .bind(user_id as i32)
    .bind(&body.reason)
    .bind(&body.ids)
    .execute(&state.pool)
    .await
    {
        Ok(r) => Json(json!({"message": format!("{} item(s) blocked", r.rows_affected()), "count": r.rows_affected()})).into_response(),
        Err(e) => {
            tracing::error!("bulk_block_media: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

/// POST /api/v1/admin/metadata/bulk-delete
pub async fn bulk_delete_media(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<BulkMediaIdsRequest>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    if body.ids.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "ids must not be empty"})),
        )
            .into_response();
    }
    // Delete orphaned streams first
    let _ = sqlx::query(
        "DELETE FROM stream WHERE id IN (
            SELECT sml.stream_id FROM stream_media_link sml
            WHERE sml.media_id = ANY($1)
            AND NOT EXISTS (
                SELECT 1 FROM stream_media_link sml2
                WHERE sml2.stream_id = sml.stream_id AND sml2.media_id <> ALL($1)
            )
        )",
    )
    .bind(&body.ids)
    .execute(&state.pool)
    .await;

    match sqlx::query("DELETE FROM media WHERE id = ANY($1)")
        .bind(&body.ids)
        .execute(&state.pool)
        .await
    {
        Ok(r) => Json(json!({"message": format!("{} item(s) deleted", r.rows_affected()), "count": r.rows_affected()})).into_response(),
        Err(e) => {
            tracing::error!("bulk_delete_media: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

/// GET /api/v1/admin/media/blocked
pub async fn list_blocked_media(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<BlockedMediaQuery>,
) -> impl IntoResponse {
    // nsfw_flagged filter is accessible to any authenticated user;
    // the blocked filter requires admin/moderator.
    let filter = params.filter.as_deref().unwrap_or("blocked");
    let viewer_is_admin;

    if filter == "nsfw_flagged" || filter == "nsfw_reviewed" {
        // Any valid token can view NSFW flagged/reviewed items.
        let data = match crate::routes::admin_nsfw::extract_token_data(
            &headers,
            &state.config.secret_key_raw,
        ) {
            Some(d) => d,
            None => return forbidden(),
        };
        viewer_is_admin = data["role"].as_str() == Some("admin");
    } else {
        // Blocked media requires at least moderator.
        match validate_moderator_or_admin(&headers, &state.config.secret_key_raw) {
            Some(_) => {
                viewer_is_admin = validate_admin(&headers, &state.config.secret_key_raw).is_some();
            }
            None => return forbidden(),
        }
    }

    let page = params.page.unwrap_or(1).max(1);
    let page_size = params.page_size.unwrap_or(20).clamp(1, 100);
    let offset = (page - 1) * page_size;

    let base_condition = match filter {
        "nsfw_flagged" => "m.poster_nsfw_flagged = true AND m.poster_nsfw_reviewed = false".to_string(),
        "nsfw_reviewed" => "m.poster_nsfw_reviewed = true".to_string(),
        "all_restricted" => "(m.is_blocked OR (m.is_keyword_blocked AND NOT m.keyword_block_override) OR m.poster_nsfw_flagged)".to_string(),
        "manual" => "m.is_blocked = true".to_string(),
        "keyword_blocked" => "m.is_keyword_blocked = true AND m.keyword_block_override = false".to_string(),
        _ => "(m.is_blocked = true OR (m.is_keyword_blocked = true AND m.keyword_block_override = false))".to_string(),
    };

    let is_nsfw_filter = matches!(filter, "nsfw_flagged" | "nsfw_reviewed");

    let media_type_filter = params
        .media_type
        .as_ref()
        .and_then(|t| crate::db::MediaType::from_wire(&t.to_ascii_lowercase()));

    let type_clause = if media_type_filter.is_some() {
        " AND m.type = $1".to_string()
    } else {
        String::new()
    };

    let search_clause = params
        .search
        .as_ref()
        .map(|s| {
            let escaped = s.replace('\'', "''");
            format!(" AND m.title ILIKE '%{escaped}%'")
        })
        .unwrap_or_default();

    let where_clause = format!("WHERE {base_condition}{type_clause}{search_clause}");

    // Total count
    let count_sql = format!("SELECT COUNT(*) FROM media m {where_clause}");
    let mut count_q = sqlx::query_scalar::<_, i64>(sqlx::AssertSqlSafe(count_sql.as_str()));
    if let Some(mt) = media_type_filter {
        count_q = count_q.bind(mt);
    }
    let total: i64 = count_q.fetch_one(&state.pool_ro).await.unwrap_or(0);

    // Paged results — include nsfw columns + poster via RPDB/media_image
    let order = if is_nsfw_filter {
        "m.poster_nsfw_score DESC NULLS LAST"
    } else {
        "m.blocked_at DESC NULLS LAST"
    };

    let list_sql = format!(
        r#"
        SELECT m.id, m.title, m.type::text AS media_type, m.year,
               m.is_blocked, m.is_keyword_blocked, m.keyword_block_override,
               m.blocked_at, m.blocked_by_user_id, m.block_reason,
               m.poster_nsfw_score, m.poster_nsfw_flagged, m.poster_nsfw_reviewed,
               MAX(CASE WHEN mei.provider = 'imdb' THEN mei.external_id END) AS imdb_id,
               mi.url AS poster_url
        FROM media m
        LEFT JOIN media_external_id mei ON mei.media_id = m.id AND mei.provider = 'imdb'
        LEFT JOIN LATERAL (
            SELECT url FROM media_image
            WHERE media_id = m.id AND image_type = 'poster' AND is_primary = true
            LIMIT 1
        ) mi ON true
        {where_clause}
        GROUP BY m.id, mi.url
        ORDER BY {order}
        LIMIT {page_size} OFFSET {offset}
        "#
    );

    let mut list_q = sqlx::query(sqlx::AssertSqlSafe(list_sql.as_str()));
    if let Some(mt) = media_type_filter {
        list_q = list_q.bind(mt);
    }

    match list_q.fetch_all(&state.pool_ro).await {
        Ok(rows) => {
            use sqlx::Row;
            let items: Vec<Value> = rows
                .into_iter()
                .map(|r| {
                    let imdb_id: Option<String> = r.try_get("imdb_id").unwrap_or(None);
                    let stored_url: Option<String> = r.try_get("poster_url").unwrap_or(None);
                    json!({
                        "id": r.try_get::<i32, _>("id").unwrap_or(0),
                        "title": r.try_get::<String, _>("title").unwrap_or_default(),
                        "type": r.try_get::<String, _>("media_type").unwrap_or_default().to_lowercase(),
                        "year": r.try_get::<Option<i32>, _>("year").unwrap_or(None),
                        "poster": stored_url,
                        "imdb_id": imdb_id,
                        "is_blocked": r.try_get::<bool, _>("is_blocked").unwrap_or(false),
                        "is_keyword_blocked": r.try_get::<bool, _>("is_keyword_blocked").unwrap_or(false),
                        "keyword_block_override": r.try_get::<bool, _>("keyword_block_override").unwrap_or(false),
                        "blocked_at": r.try_get::<Option<chrono::DateTime<chrono::Utc>>, _>("blocked_at").unwrap_or(None),
                        "blocked_by_user_id": r.try_get::<Option<i32>, _>("blocked_by_user_id").unwrap_or(None),
                        "block_reason": r.try_get::<Option<String>, _>("block_reason").unwrap_or(None),
                        "nsfw_score": r.try_get::<Option<f32>, _>("poster_nsfw_score").unwrap_or(None),
                        "nsfw_flagged": r.try_get::<bool, _>("poster_nsfw_flagged").unwrap_or(false),
                        "nsfw_reviewed": r.try_get::<bool, _>("poster_nsfw_reviewed").unwrap_or(false),
                    })
                })
                .collect();

            Json(json!({
                "items": items,
                "total": total,
                "page": page,
                "page_size": page_size,
                "has_more": offset + page_size < total,
                "viewer_is_admin": viewer_is_admin,
            }))
            .into_response()
        }
        Err(e) => {
            tracing::error!("list_blocked_media filter={filter}: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

/// POST /api/v1/admin/media/{id}/keyword-override
/// Toggle keyword_block_override for a media row.
pub async fn toggle_keyword_block_override(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i32>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    match sqlx::query_scalar::<_, bool>(
        "UPDATE media SET keyword_block_override = NOT keyword_block_override WHERE id = $1 RETURNING keyword_block_override",
    )
    .bind(media_id)
    .fetch_optional(&state.pool)
    .await
    {
        Ok(Some(new_val)) => Json(json!({
            "id": media_id,
            "keyword_block_override": new_val,
            "message": if new_val { "Keyword block overridden — media is now visible" } else { "Keyword block override removed" },
        })).into_response(),
        Ok(None) => (StatusCode::NOT_FOUND, Json(json!({"detail": "Media not found"}))).into_response(),
        Err(e) => {
            tracing::error!("toggle_keyword_block_override id={media_id}: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}

/// POST /api/v1/admin/torrent-streams/{stream_id}/block
pub async fn block_torrent_stream(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(stream_id): Path<StreamId>,
) -> impl IntoResponse {
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    match sqlx::query("UPDATE stream SET is_blocked = true WHERE id = $1")
        .bind(stream_id.0)
        .execute(&state.pool)
        .await
    {
        Ok(r) if r.rows_affected() == 0 => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Stream not found"})),
        )
            .into_response(),
        Ok(_) => Json(json!({
            "stream_id": stream_id,
            "is_blocked": true,
            "message": "Stream blocked",
        }))
        .into_response(),
        Err(e) => {
            tracing::error!("block_torrent_stream: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
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
            Json(json!({
                "id": "default",
                "auto_approval_threshold": contribution_defaults::AUTO_APPROVAL_THRESHOLD,
                "points_per_metadata_edit": contribution_defaults::POINTS_PER_METADATA_EDIT,
                "points_per_stream_edit": contribution_defaults::POINTS_PER_STREAM_EDIT,
                "points_for_rejection_penalty": contribution_defaults::POINTS_FOR_REJECTION_PENALTY,
                "contributor_threshold": contribution_defaults::CONTRIBUTOR_THRESHOLD,
                "trusted_threshold": contribution_defaults::TRUSTED_THRESHOLD,
                "expert_threshold": contribution_defaults::EXPERT_THRESHOLD,
                "allow_auto_approval": true,
                "require_reason_for_edits": false,
                "max_pending_suggestions_per_user": contribution_defaults::MAX_PENDING_SUGGESTIONS_PER_USER,
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
    if let (Some(c), Some(t)) = (ct, tt)
        && c >= t
    {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Contributor threshold must be less than trusted threshold"})),
        )
            .into_response();
    }
    if let (Some(t), Some(e)) = (tt, et)
        && t >= e
    {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Trusted threshold must be less than expert threshold"})),
        )
            .into_response();
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

    let (ct, tt, et, aaa, aat) = row.unwrap_or((
        contribution_defaults::CONTRIBUTOR_THRESHOLD as i32,
        contribution_defaults::TRUSTED_THRESHOLD as i32,
        contribution_defaults::EXPERT_THRESHOLD as i32,
        true,
        contribution_defaults::AUTO_APPROVAL_THRESHOLD,
    ));

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

// ─── Exception tracking endpoints ────────────────────────────────────────────

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
    use fred::prelude::SortedSetsInterface;
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }
    let total: i64 = state
        .redis
        .zcard(crate::exception_tracker::INDEX_KEY)
        .await
        .unwrap_or(0);
    Json(json!({
        "enabled": state.config.enable_exception_tracking,
        "ttl_seconds": state.config.exception_tracking_ttl,
        "max_entries": state.config.exception_tracking_max_entries,
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
    let result = crate::exception_tracker::query_list(
        &state.redis,
        page,
        per_page,
        params.exception_type.as_deref(),
    )
    .await;
    Json(result).into_response()
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
    match crate::exception_tracker::query_detail(&state.redis, &fingerprint).await {
        Some(v) => Json(v).into_response(),
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
    let count = crate::exception_tracker::clear_all(&state.redis).await;
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
    if crate::exception_tracker::clear_one(&state.redis, &fingerprint).await {
        Json(json!({"cleared": 1, "message": "Exception cleared successfully."})).into_response()
    } else {
        (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Exception not found. It may have already expired."})),
        )
            .into_response()
    }
}

// ─── request_metrics.py endpoints ────────────────────────────────────────────

use crate::metrics_middleware::{
    AGG_PREFIX, ENDPOINTS_KEY, LATENCY_PREFIX, RECENT_KEY, TS_COUNT_KEY, TS_ERR_KEY,
    TS_STATUS_PREFIX, TS_TIME_KEY, UV_GLOBAL_KEY, UV_PREFIX,
};

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

fn agg_hash_to_item(ep_key: &str, data: std::collections::HashMap<String, String>) -> Value {
    let total_req: i64 = data
        .get("total_requests")
        .and_then(|v| v.parse().ok())
        .unwrap_or(0);
    let total_time: f64 = data
        .get("total_time")
        .and_then(|v| v.parse().ok())
        .unwrap_or(0.0);
    let avg_time = if total_req > 0 {
        (total_time / total_req as f64 * 1_000_000.0).round() / 1_000_000.0
    } else {
        0.0
    };
    let method = data
        .get("method")
        .cloned()
        .unwrap_or_else(|| ep_key.split(':').next().unwrap_or("").to_string());
    let route = data.get("route").cloned().unwrap_or_else(|| {
        ep_key
            .split_once(':')
            .map(|(_, rest)| rest.to_string())
            .unwrap_or_else(|| ep_key.to_string())
    });

    json!({
        "endpoint_key": ep_key,
        "method": method,
        "route": route,
        "total_requests": total_req,
        "avg_time": avg_time,
        "min_time": data.get("min_time").and_then(|v| v.parse::<f64>().ok()).unwrap_or(0.0),
        "max_time": data.get("max_time").and_then(|v| v.parse::<f64>().ok()).unwrap_or(0.0),
        "error_count": data.get("error_count").and_then(|v| v.parse::<i64>().ok()).unwrap_or(0),
        "status_2xx": data.get("status_2xx").and_then(|v| v.parse::<i64>().ok()).unwrap_or(0),
        "status_3xx": data.get("status_3xx").and_then(|v| v.parse::<i64>().ok()).unwrap_or(0),
        "status_4xx": data.get("status_4xx").and_then(|v| v.parse::<i64>().ok()).unwrap_or(0),
        "status_5xx": data.get("status_5xx").and_then(|v| v.parse::<i64>().ok()).unwrap_or(0),
        "last_seen": data.get("last_seen").cloned().unwrap_or_default(),
    })
}

/// GET /api/v1/admin/request-metrics/status
pub async fn get_request_metrics_status(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    use fred::prelude::{HyperloglogInterface, SortedSetsInterface};
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let total_endpoints: i64 = state.redis.zcard(ENDPOINTS_KEY).await.unwrap_or(0);
    let total_recent: i64 = state.redis.llen::<i64, _>(RECENT_KEY).await.unwrap_or(0);

    // Sum total_requests across all endpoints (mirrors Python get_status)
    let all_ep_keys: Vec<String> = state
        .redis
        .zrange(ENDPOINTS_KEY, 0i64, -1i64, None, false, None, false)
        .await
        .unwrap_or_default();
    let mut total_requests: i64 = 0;
    for ep_key in &all_ep_keys {
        let agg_key = format!("{AGG_PREFIX}{ep_key}");
        let count: Option<String> = state
            .redis
            .hget(&agg_key, "total_requests")
            .await
            .unwrap_or(None);
        if let Some(c) = count.and_then(|s| s.parse::<i64>().ok()) {
            total_requests += c;
        }
    }

    // Approximate unique visitors via HyperLogLog
    let unique_visitors: i64 = state
        .redis
        .pfcount::<i64, _>(UV_GLOBAL_KEY)
        .await
        .unwrap_or(0);

    Json(json!({
        "enabled": true,
        "ttl_seconds": 86400,
        "recent_ttl_seconds": 3600,
        "max_recent": 10000,
        "total_endpoints": total_endpoints,
        "total_requests": total_requests,
        "total_recent": total_recent,
        "unique_visitors": unique_visitors,
    }))
    .into_response()
}

/// GET /api/v1/admin/request-metrics/endpoints
pub async fn list_endpoint_stats(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<MetricsEndpointListQuery>,
) -> impl IntoResponse {
    use fred::prelude::{HyperloglogInterface, SortedSetsInterface};
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let page = params.page.unwrap_or(1).max(1);
    let per_page = params.per_page.unwrap_or(20).clamp(1, 100);
    let sort_by = params.sort_by.as_deref().unwrap_or("total_requests");
    let sort_desc = params.sort_order.as_deref().unwrap_or("desc") != "asc";

    // Get all endpoints (most-recently-seen first)
    let all_ep_keys: Vec<String> = state
        .redis
        .zrange(ENDPOINTS_KEY, 0i64, -1i64, None, true, None, false)
        .await
        .unwrap_or_default();

    let mut items: Vec<Value> = Vec::new();
    for ep_key in &all_ep_keys {
        let agg_key = format!("{AGG_PREFIX}{ep_key}");
        let data: std::collections::HashMap<String, String> =
            state.redis.hgetall(&agg_key).await.unwrap_or_default();
        if !data.is_empty() {
            let uv_key = format!("{UV_PREFIX}{ep_key}");
            let unique_visitors: i64 = state.redis.pfcount::<i64, _>(&uv_key).await.unwrap_or(0);
            let mut item = agg_hash_to_item(ep_key, data);
            item["unique_visitors"] = serde_json::json!(unique_visitors);
            items.push(item);
        }
    }

    // Sort
    items.sort_by(|a, b| {
        let va = match sort_by {
            "avg_time" | "min_time" | "max_time" => {
                a.get(sort_by).and_then(|v| v.as_f64()).unwrap_or(0.0)
            }
            _ => a.get(sort_by).and_then(|v| v.as_i64()).unwrap_or(0) as f64,
        };
        let vb = match sort_by {
            "avg_time" | "min_time" | "max_time" => {
                b.get(sort_by).and_then(|v| v.as_f64()).unwrap_or(0.0)
            }
            _ => b.get(sort_by).and_then(|v| v.as_i64()).unwrap_or(0) as f64,
        };
        if sort_desc {
            vb.partial_cmp(&va).unwrap_or(std::cmp::Ordering::Equal)
        } else {
            va.partial_cmp(&vb).unwrap_or(std::cmp::Ordering::Equal)
        }
    });

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

/// Compute p50, p95, p99 percentiles from a latency sorted set.
/// Mirrors Python `_compute_percentiles` in `python-deprecated/utils/request_tracker.py`.
async fn compute_percentiles(redis: &fred::clients::Client, latency_key: &str) -> (f64, f64, f64) {
    use fred::prelude::SortedSetsInterface;
    let members: Vec<String> = redis
        .zrange(latency_key, 0i64, -1i64, None, false, None, false)
        .await
        .unwrap_or_default();
    if members.is_empty() {
        return (0.0, 0.0, 0.0);
    }

    // Members are stored as "{duration_s:.6}:{uuid_suffix}"; extract the leading float.
    let mut latencies: Vec<f64> = members
        .iter()
        .filter_map(|m| m.split(':').next()?.parse::<f64>().ok())
        .collect();
    if latencies.is_empty() {
        return (0.0, 0.0, 0.0);
    }

    latencies.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let n = latencies.len();

    let percentile = |p: f64| -> f64 {
        let idx = (p / 100.0) * (n as f64 - 1.0);
        let lower = idx.floor() as usize;
        let upper = (lower + 1).min(n - 1);
        let w = idx - lower as f64;
        let v = latencies[lower] * (1.0 - w) + latencies[upper] * w;
        // Round to 6 decimal places
        (v * 1_000_000.0).round() / 1_000_000.0
    };

    (percentile(50.0), percentile(95.0), percentile(99.0))
}

/// GET /api/v1/admin/request-metrics/endpoints/{method}/{route}
pub async fn get_endpoint_detail(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path((method, route)): Path<(String, String)>,
) -> impl IntoResponse {
    use fred::prelude::HyperloglogInterface;
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let route = if route.starts_with('/') {
        route
    } else {
        format!("/{route}")
    };

    let ep_key = format!("{}:{route}", method.to_uppercase());
    let agg_key = format!("{AGG_PREFIX}{ep_key}");
    let data: std::collections::HashMap<String, String> =
        state.redis.hgetall(&agg_key).await.unwrap_or_default();

    if data.is_empty() {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Endpoint metrics not found. It may have expired."})),
        )
            .into_response();
    }

    let uv_key = format!("{UV_PREFIX}{ep_key}");
    let unique_visitors: i64 = state.redis.pfcount::<i64, _>(&uv_key).await.unwrap_or(0);

    let latency_key = format!("{LATENCY_PREFIX}{ep_key}");
    let (p50, p95, p99) = compute_percentiles(&state.redis, &latency_key).await;

    let mut item = agg_hash_to_item(&ep_key, data);
    item["unique_visitors"] = serde_json::json!(unique_visitors);
    item["p50"] = serde_json::json!(p50);
    item["p95"] = serde_json::json!(p95);
    item["p99"] = serde_json::json!(p99);

    Json(item).into_response()
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
        .lrange::<Vec<String>, _>(RECENT_KEY, 0, -1)
        .await
        .unwrap_or_default();

    let mut items: Vec<Value> = Vec::new();
    for raw in &raw_entries {
        if let Ok(v) = serde_json::from_str::<Value>(raw) {
            if let Some(ref method) = params.method
                && v.get("method").and_then(|m| m.as_str()) != Some(method.as_str())
            {
                continue;
            }
            if let Some(sc) = params.status_code
                && v.get("status_code").and_then(|s| s.as_i64()) != Some(sc)
            {
                continue;
            }
            if let Some(ref route) = params.route {
                let path_val = v
                    .get("route_template")
                    .or_else(|| v.get("path"))
                    .and_then(|p| p.as_str())
                    .unwrap_or("");
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
    use fred::prelude::SortedSetsInterface;
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let all_ep_keys: Vec<String> = state
        .redis
        .zrange(ENDPOINTS_KEY, 0i64, -1i64, None, false, None, false)
        .await
        .unwrap_or_default();

    let mut cleared: i64 = 0;
    for ep_key in &all_ep_keys {
        cleared += state
            .redis
            .del::<i64, _>(format!("{AGG_PREFIX}{ep_key}"))
            .await
            .unwrap_or(0);
        cleared += state
            .redis
            .del::<i64, _>(format!("{UV_PREFIX}{ep_key}"))
            .await
            .unwrap_or(0);
        cleared += state
            .redis
            .del::<i64, _>(format!("{LATENCY_PREFIX}{ep_key}"))
            .await
            .unwrap_or(0);
    }

    // Global index + recent log
    let _: Result<i64, _> = state.redis.del(ENDPOINTS_KEY).await;
    let _: Result<i64, _> = state.redis.del(RECENT_KEY).await;
    let _: Result<i64, _> = state.redis.del(UV_GLOBAL_KEY).await;
    cleared += 3;

    // Time-series hashes
    let _: Result<i64, _> = state.redis.del(TS_COUNT_KEY).await;
    let _: Result<i64, _> = state.redis.del(TS_TIME_KEY).await;
    let _: Result<i64, _> = state.redis.del(TS_ERR_KEY).await;
    for digit in ["2", "3", "4", "5"] {
        let key = format!("{TS_STATUS_PREFIX}{digit}");
        let _: Result<i64, _> = state.redis.del(&key).await;
    }
    cleared += 7;

    Json(json!({
        "cleared": cleared,
        "message": format!("Cleared {cleared} request metrics key(s)."),
    }))
    .into_response()
}

// ─── Timeseries query ─────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct TimeseriesQuery {
    pub window: Option<i64>,
}

/// GET /api/v1/admin/request-metrics/timeseries?window=<seconds>
///
/// Returns per-minute aggregated time-series data for the given look-back window.
/// Default window = 3600 s (1 h); max = 86400 s (1 d).
pub async fn get_request_metrics_timeseries(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<TimeseriesQuery>,
) -> impl IntoResponse {
    use fred::prelude::HashesInterface;
    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let window_secs = params.window.unwrap_or(3600).clamp(60, 86_400);
    let now_ts = chrono::Utc::now().timestamp();
    let cutoff = ((now_ts - window_secs) / 60) * 60;

    // Read all time-series hashes in one pass
    let counts: std::collections::HashMap<String, String> =
        state.redis.hgetall(TS_COUNT_KEY).await.unwrap_or_default();
    let times: std::collections::HashMap<String, String> =
        state.redis.hgetall(TS_TIME_KEY).await.unwrap_or_default();
    let errs: std::collections::HashMap<String, String> =
        state.redis.hgetall(TS_ERR_KEY).await.unwrap_or_default();

    let mut s2: std::collections::HashMap<String, String> = Default::default();
    let mut s3: std::collections::HashMap<String, String> = Default::default();
    let mut s4: std::collections::HashMap<String, String> = Default::default();
    let mut s5: std::collections::HashMap<String, String> = Default::default();
    for (digit, map) in [
        ("2", &mut s2),
        ("3", &mut s3),
        ("4", &mut s4),
        ("5", &mut s5),
    ] {
        *map = state
            .redis
            .hgetall(format!("{TS_STATUS_PREFIX}{digit}"))
            .await
            .unwrap_or_default();
    }

    // Collect buckets within the window, fill count/time/err/status
    let mut bucket_set: std::collections::BTreeMap<i64, serde_json::Value> =
        std::collections::BTreeMap::new();

    for (bucket_str, count_str) in &counts {
        let bucket: i64 = match bucket_str.parse() {
            Ok(v) => v,
            Err(_) => continue,
        };
        if bucket < cutoff {
            continue;
        }
        let count: i64 = count_str.parse().unwrap_or(0);
        let total_time: f64 = times
            .get(bucket_str)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0.0);
        let errors: i64 = errs
            .get(bucket_str)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0);
        let status_2xx: i64 = s2.get(bucket_str).and_then(|s| s.parse().ok()).unwrap_or(0);
        let status_3xx: i64 = s3.get(bucket_str).and_then(|s| s.parse().ok()).unwrap_or(0);
        let status_4xx: i64 = s4.get(bucket_str).and_then(|s| s.parse().ok()).unwrap_or(0);
        let status_5xx: i64 = s5.get(bucket_str).and_then(|s| s.parse().ok()).unwrap_or(0);
        let avg_time = if count > 0 {
            (total_time / count as f64 * 1_000_000.0).round() / 1_000_000.0
        } else {
            0.0
        };

        bucket_set.insert(
            bucket,
            serde_json::json!({
                "ts": bucket,
                "count": count,
                "errors": errors,
                "avg_time": avg_time,
                "status_2xx": status_2xx,
                "status_3xx": status_3xx,
                "status_4xx": status_4xx,
                "status_5xx": status_5xx,
            }),
        );
    }

    let points: Vec<serde_json::Value> = bucket_set.into_values().collect();

    Json(json!({
        "bucket_seconds": 60,
        "window_seconds": window_secs,
        "points": points,
    }))
    .into_response()
}

// ─── source_health.py endpoint ────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct SourceHealthQuery {
    pub anime_only: Option<bool>,
    pub bucket: Option<String>,
}

/// GET /api/v1/admin/public-indexers/source-health
pub async fn get_source_health(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<SourceHealthQuery>,
) -> impl IntoResponse {
    use crate::scrapers::{public_indexer_registry::ALL_INDEXERS, source_health};

    if validate_admin(&headers, &state.config.secret_key_raw).is_none() {
        return forbidden();
    }

    let cfg = &state.config;
    let scope_mode = &cfg.public_indexers_source_health_scope_mode;
    let scope_override = &cfg.public_indexers_source_health_scope;
    let min_samples = cfg.public_indexers_source_health_min_samples;
    let min_success_rate = cfg.public_indexers_source_min_success_rate;
    let max_timeout_rate = cfg.public_indexers_source_max_timeout_rate;

    let anime_only = params.anime_only.unwrap_or(false);

    // Resolve the scope key the same way source_health.rs does
    let scope_key_display =
        source_health::metrics_key("__scope_probe__", "general", scope_mode, scope_override)
            .strip_prefix("public_indexer_source_health:")
            .and_then(|s| s.strip_suffix(":general:__scope_probe__"))
            .map(|s| s.to_string());

    let gate = json!({
        "enabled": cfg.public_indexers_source_health_gates_enabled,
        "scope_mode": scope_mode,
        "scope_key": scope_key_display,
        "min_samples": min_samples,
        "min_success_rate": min_success_rate,
        "max_timeout_rate": max_timeout_rate,
    });

    // Determine the health bucket(s) to query
    let buckets_to_query: Vec<&str> = if let Some(b) = params.bucket.as_deref() {
        vec![b]
    } else if anime_only {
        vec!["anime"]
    } else {
        vec!["movie", "series"]
    };

    let mut sources: Vec<Value> = Vec::new();
    let mut allowed = 0usize;
    let mut blocked = 0usize;
    let mut warming = 0usize;

    for def in ALL_INDEXERS.iter() {
        if anime_only && !def.supports_anime {
            continue;
        }

        // Pick the most relevant bucket for this indexer's capabilities
        let bucket = if buckets_to_query.contains(&"anime") && def.supports_anime {
            "anime"
        } else if buckets_to_query.contains(&"series") && def.supports_series {
            "series"
        } else if buckets_to_query.contains(&"movie") && def.supports_movie {
            "movie"
        } else {
            "general"
        };

        let snapshot = source_health::get_source_health(
            &state.redis,
            def.key,
            bucket,
            scope_mode,
            scope_override,
        )
        .await;

        let status = snapshot.gate_status(min_samples, min_success_rate, max_timeout_rate);
        match status {
            "allowed" => allowed += 1,
            "blocked" => blocked += 1,
            _ => warming += 1,
        }

        sources.push(json!({
            "source_key": def.key,
            "source_name": def.source_name,
            "supports_movie": def.supports_movie,
            "supports_series": def.supports_series,
            "supports_anime": def.supports_anime,
            "health_bucket": bucket,
            "samples": snapshot.total,
            "success": snapshot.success,
            "timeout": snapshot.timeout,
            "challenge_solved": snapshot.challenge_solved,
            "consecutive_success": snapshot.consecutive_success,
            "success_rate": snapshot.success_rate(),
            "timeout_rate": snapshot.timeout_rate(),
            "challenge_solve_rate": snapshot.challenge_solve_rate(),
            "gate_status": status,
            "gate_enforced_now": cfg.public_indexers_source_health_gates_enabled && status == "blocked",
        }));
    }

    Json(json!({
        "gate": gate,
        "total_sources": sources.len(),
        "allowed": allowed,
        "blocked": blocked,
        "warming": warming,
        "sources": sources,
    }))
    .into_response()
}
