/// Stream Suggestions endpoints — users suggest stream corrections/reports.
///
/// Routes:
///   POST   /api/v1/streams/{stream_id}/suggest              → create_stream_suggestion
///   GET    /api/v1/stream-suggestions                        → list_my_stream_suggestions
///   GET    /api/v1/stream-suggestions/stats                  → get_stream_suggestion_stats
///   GET    /api/v1/stream-suggestions/pending                → list_pending_stream_suggestions  (moderator)
///   POST   /api/v1/stream-suggestions/bulk-review            → bulk_review_stream_suggestions   (moderator)
///   GET    /api/v1/stream-suggestions/{suggestion_id}        → get_stream_suggestion
///   DELETE /api/v1/stream-suggestions/{suggestion_id}        → delete_stream_suggestion
///   PUT    /api/v1/stream-suggestions/{suggestion_id}/review → review_stream_suggestion         (moderator)
///   PATCH  /api/v1/stream-suggestions/{suggestion_id}/triage → triage_stream_suggestion         (moderator)
///   POST   /api/v1/streams/{stream_id}/signals               → get_stream_signals
///   POST   /api/v1/streams/signals/bulk                      → bulk_stream_signals
///   GET    /api/v1/streams/{stream_id}/editable-fields        → get_stream_editable_fields
use std::sync::Arc;

use axum::{
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::{DateTime, Utc};
use hmac::{Hmac, KeyInit, Mac};
use serde::Deserialize;
use serde_json::json;
use sha2::Sha256;
use uuid::Uuid;

use crate::state::AppState;

// ─── Auth helpers ─────────────────────────────────────────────────────────────

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

fn validate_token_optional(headers: &HeaderMap, secret_key: &str) -> Option<i32> {
    validate_token(headers, secret_key)
}

async fn get_user_role(pool: &sqlx::PgPool, user_id: i32) -> Option<String> {
    sqlx::query_scalar::<_, String>("SELECT LOWER(role::text) FROM users WHERE id = $1")
        .bind(user_id)
        .fetch_optional(pool)
        .await
        .unwrap_or(None)
}

fn is_mod_or_admin(role: &str) -> bool {
    matches!(role, "moderator" | "admin")
}

// ─── Request / Response structs ───────────────────────────────────────────────

#[derive(Deserialize)]
pub struct StreamSuggestionCreateRequest {
    pub suggestion_type: String,
    pub field_name: Option<String>,
    pub current_value: Option<String>,
    pub suggested_value: Option<String>,
    pub reason: Option<String>,
    pub related_stream_id: Option<String>,
    pub target_media_id: Option<i32>,
    pub target_external_id: Option<String>,
    pub target_media_type: Option<String>,
    pub target_title: Option<String>,
    pub file_index: Option<i32>,
    pub season_number: Option<i32>,
    pub episode_number: Option<i32>,
    pub episode_end: Option<i32>,
}

#[derive(Deserialize)]
pub struct StreamSuggestionReviewRequest {
    pub action: String,
    pub review_notes: Option<String>,
}

#[derive(Deserialize)]
pub struct TriageRequest {
    pub issue_triage_status: String,
    pub issue_triage_note: Option<String>,
}

#[derive(Deserialize)]
pub struct ListSuggestionsQuery {
    pub status: Option<String>,
    pub suggestion_type: Option<String>,
    #[serde(default = "default_page")]
    pub page: i64,
    #[serde(default = "default_page_size")]
    pub page_size: i64,
}

fn default_page() -> i64 {
    1
}
fn default_page_size() -> i64 {
    20
}

#[derive(Deserialize)]
pub struct PendingQuery {
    pub status: Option<String>,
    pub suggestion_type: Option<String>,
    #[serde(default = "default_page")]
    pub page: i64,
    #[serde(default = "default_page_size")]
    pub page_size: i64,
}

#[derive(Deserialize)]
pub struct BulkReviewBody {
    pub suggestion_ids: Vec<String>,
    pub action: String,
    pub review_notes: Option<String>,
}

// ─── DB helpers ───────────────────────────────────────────────────────────────

struct SuggestionRow {
    id: String,
    user_id: i32,
    stream_id: i32,
    suggestion_type: String,
    field_name: Option<String>,
    current_value: Option<String>,
    suggested_value: Option<String>,
    reason: Option<String>,
    status: String,
    reviewed_by: Option<String>,
    reviewed_at: Option<DateTime<Utc>>,
    review_notes: Option<String>,
    issue_triage_status: Option<String>,
    issue_triage_note: Option<String>,
    created_at: DateTime<Utc>,
}

async fn fetch_suggestion(pool: &sqlx::PgPool, id: &str) -> Option<SuggestionRow> {
    type R = (
        String,
        i32,
        i32,
        String,
        Option<String>,
        Option<String>,
        Option<String>,
        Option<String>,
        String,
        Option<String>,
        Option<DateTime<Utc>>,
        Option<String>,
        Option<String>,
        Option<String>,
        DateTime<Utc>,
    );
    let row: R = sqlx::query_as::<_, R>(
        r#"SELECT id, user_id, stream_id, suggestion_type, field_name,
                      current_value, suggested_value, reason, status,
                      reviewed_by, reviewed_at, review_notes,
                      issue_triage_status, issue_triage_note, created_at
               FROM stream_suggestions WHERE id = $1"#,
    )
    .bind(id)
    .fetch_optional(pool)
    .await
    .unwrap_or(None)?;

    Some(SuggestionRow {
        id: row.0,
        user_id: row.1,
        stream_id: row.2,
        suggestion_type: row.3,
        field_name: row.4,
        current_value: row.5,
        suggested_value: row.6,
        reason: row.7,
        status: row.8,
        reviewed_by: row.9,
        reviewed_at: row.10,
        review_notes: row.11,
        issue_triage_status: row.12,
        issue_triage_note: row.13,
        created_at: row.14,
    })
}

async fn suggestion_to_json(pool: &sqlx::PgPool, row: &SuggestionRow) -> serde_json::Value {
    let username: Option<String> = sqlx::query_scalar("SELECT username FROM users WHERE id = $1")
        .bind(row.user_id)
        .fetch_optional(pool)
        .await
        .unwrap_or(None);

    let reviewer_name: Option<String> = if let Some(ref rb) = row.reviewed_by {
        if let Ok(rid) = rb.parse::<i32>() {
            sqlx::query_scalar::<_, String>("SELECT username FROM users WHERE id = $1")
                .bind(rid)
                .fetch_optional(pool)
                .await
                .unwrap_or(None)
        } else {
            Some(rb.clone())
        }
    } else {
        None
    };

    let stream_name: Option<String> = sqlx::query_scalar("SELECT name FROM stream WHERE id = $1")
        .bind(row.stream_id)
        .fetch_optional(pool)
        .await
        .unwrap_or(None);

    // Source media via stream_media_link
    let source_media: Option<(i32, String, String, Option<i32>)> = sqlx::query_as(
        r#"SELECT m.id, m.title, m.type::text, m.year
           FROM media m
           JOIN stream_media_link sml ON sml.media_id = m.id
           WHERE sml.stream_id = $1
           ORDER BY sml.is_primary DESC, sml.id ASC
           LIMIT 1"#,
    )
    .bind(row.stream_id)
    .fetch_optional(pool)
    .await
    .unwrap_or(None);

    let (src_media_id, src_media_title, src_media_type, src_media_year) = source_media
        .map(|(id, t, mt, y)| (Some(id), Some(t), Some(mt.to_lowercase()), y))
        .unwrap_or((None, None, None, None));

    let user_info: Option<(String, i32)> =
        sqlx::query_as("SELECT contribution_level, contribution_points FROM users WHERE id = $1")
            .bind(row.user_id)
            .fetch_optional(pool)
            .await
            .unwrap_or(None);
    let (user_contribution_level, user_contribution_points) = user_info
        .map(|(l, p)| (Some(l), Some(p)))
        .unwrap_or((None, None));

    json!({
        "id": row.id,
        "user_id": row.user_id,
        "username": username,
        "stream_id": row.stream_id,
        "stream_name": stream_name,
        "media_id": src_media_id,
        "source_media_id": src_media_id,
        "source_media_type": src_media_type,
        "source_media_title": src_media_title,
        "source_media_year": src_media_year,
        "source_media_poster_url": null,
        "target_media_id": null,
        "target_media_type": null,
        "target_media_title": null,
        "target_media_year": null,
        "target_media_poster_url": null,
        "suggestion_type": row.suggestion_type,
        "field_name": row.field_name,
        "current_value": row.current_value,
        "suggested_value": row.suggested_value,
        "reason": row.reason,
        "status": row.status,
        "was_auto_approved": row.status == "auto_approved",
        "created_at": row.created_at.to_rfc3339(),
        "reviewed_by": reviewer_name,
        "reviewer_name": reviewer_name,
        "reviewed_at": row.reviewed_at.map(|d| d.to_rfc3339()),
        "review_notes": row.review_notes,
        "user_contribution_level": user_contribution_level,
        "user_contribution_points": user_contribution_points,
        "issue_triage_status": row.issue_triage_status,
        "issue_triage_note": row.issue_triage_note,
    })
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// POST /api/v1/streams/{stream_id}/suggest
pub async fn create_stream_suggestion(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(stream_id): Path<i32>,
    Json(body): Json<StreamSuggestionCreateRequest>,
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

    let stream_exists: bool =
        sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM stream WHERE id = $1)")
            .bind(stream_id)
            .fetch_one(&state.pool)
            .await
            .unwrap_or(false);

    if !stream_exists {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Stream not found"})),
        )
            .into_response();
    }

    // Check auto-approval eligibility
    let role = get_user_role(&state.pool, user_id)
        .await
        .unwrap_or_default();
    let user_points: i32 =
        sqlx::query_scalar("SELECT COALESCE(contribution_points, 0) FROM users WHERE id = $1")
            .bind(user_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None)
            .unwrap_or(0);

    let auto_threshold: i32 = sqlx::query_scalar(
        "SELECT COALESCE(auto_approval_threshold, 100) FROM contribution_settings WHERE id = 'default'",
    )
    .fetch_optional(&state.pool)
    .await
    .unwrap_or(None)
    .unwrap_or(100);

    let allow_auto: bool = sqlx::query_scalar(
        "SELECT COALESCE(allow_auto_approval, true) FROM contribution_settings WHERE id = 'default'",
    )
    .fetch_optional(&state.pool)
    .await
    .unwrap_or(None)
    .unwrap_or(true);

    let can_auto_approve = is_mod_or_admin(&role) || (allow_auto && user_points >= auto_threshold);

    let initial_status = if can_auto_approve {
        "auto_approved"
    } else {
        "pending"
    };
    let suggestion_id = Uuid::new_v4().to_string();

    // Build suggested_value for relink/add_media_link types
    let suggested_value = if matches!(
        body.suggestion_type.as_str(),
        "relink_media" | "add_media_link"
    ) {
        let link_data = json!({
            "target_media_id": body.target_media_id,
            "target_external_id": body.target_external_id,
            "target_media_type": body.target_media_type,
            "target_title": body.target_title,
            "file_index": body.file_index,
            "season_number": body.season_number,
            "episode_number": body.episode_number,
            "episode_end": body.episode_end,
        });
        Some(link_data.to_string())
    } else {
        body.suggested_value.clone()
    };

    let reviewed_by = if can_auto_approve {
        Some(user_id.to_string())
    } else {
        None
    };
    let review_notes = if can_auto_approve {
        Some("Auto-approved based on user reputation".to_string())
    } else {
        None
    };

    if let Err(e) = sqlx::query(
        r#"INSERT INTO stream_suggestions
               (id, user_id, stream_id, suggestion_type, field_name,
                current_value, suggested_value, reason, status,
                reviewed_by, reviewed_at, review_notes, created_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                   CASE WHEN $10 IS NOT NULL THEN NOW() ELSE NULL END,
                   $11, NOW())"#,
    )
    .bind(&suggestion_id)
    .bind(user_id)
    .bind(stream_id)
    .bind(&body.suggestion_type)
    .bind(&body.field_name)
    .bind(&body.current_value)
    .bind(&suggested_value)
    .bind(&body.reason)
    .bind(initial_status)
    .bind(&reviewed_by)
    .bind(&review_notes)
    .execute(&state.pool)
    .await
    {
        tracing::error!("create_stream_suggestion: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    // If auto-approved, apply changes
    if can_auto_approve {
        apply_stream_field_change(
            &state.pool,
            stream_id,
            &body.suggestion_type,
            body.field_name.as_deref(),
            suggested_value.as_deref(),
        )
        .await;
    }

    let row = match fetch_suggestion(&state.pool, &suggestion_id).await {
        None => return StatusCode::INTERNAL_SERVER_ERROR.into_response(),
        Some(r) => r,
    };

    (
        StatusCode::CREATED,
        Json(suggestion_to_json(&state.pool, &row).await),
    )
        .into_response()
}

/// Apply a stream field change
async fn apply_stream_field_change(
    pool: &sqlx::PgPool,
    stream_id: i32,
    suggestion_type: &str,
    field_name: Option<&str>,
    value: Option<&str>,
) {
    if suggestion_type == "report_broken" {
        // Increment broken report counter or block stream based on threshold
        let threshold: i32 = sqlx::query_scalar(
            "SELECT COALESCE(auto_block_broken_report_threshold, 10) FROM contribution_settings WHERE id = 'default'",
        )
        .fetch_optional(pool)
        .await
        .unwrap_or(None)
        .unwrap_or(10);

        let report_count: i64 = sqlx::query_scalar(
            "SELECT COUNT(*) FROM stream_suggestions WHERE stream_id = $1 AND suggestion_type = 'report_broken' AND status IN ('approved', 'auto_approved')",
        )
        .bind(stream_id)
        .fetch_one(pool)
        .await
        .unwrap_or(0);

        if report_count >= threshold as i64 {
            let _ = sqlx::query("UPDATE stream SET is_blocked = true WHERE id = $1")
                .bind(stream_id)
                .execute(pool)
                .await;
        }
        return;
    }

    if suggestion_type != "field_correction" {
        return;
    }

    let field = match field_name {
        Some(f) => f,
        None => return,
    };
    let val = match value {
        Some(v) => v,
        None => return,
    };

    let result = match field {
        "name" => {
            sqlx::query("UPDATE stream SET name = $1 WHERE id = $2")
                .bind(val)
                .bind(stream_id)
                .execute(pool)
                .await
        }
        "resolution" => {
            sqlx::query("UPDATE stream SET resolution = $1 WHERE id = $2")
                .bind(val)
                .bind(stream_id)
                .execute(pool)
                .await
        }
        "codec" => {
            sqlx::query("UPDATE stream SET codec = $1 WHERE id = $2")
                .bind(val)
                .bind(stream_id)
                .execute(pool)
                .await
        }
        "quality" => {
            sqlx::query("UPDATE stream SET quality = $1 WHERE id = $2")
                .bind(val)
                .bind(stream_id)
                .execute(pool)
                .await
        }
        "bit_depth" => {
            sqlx::query("UPDATE stream SET bit_depth = $1 WHERE id = $2")
                .bind(val)
                .bind(stream_id)
                .execute(pool)
                .await
        }
        _ => return,
    };

    if let Err(e) = result {
        tracing::warn!("apply_stream_field_change failed for field {field}: {e}");
    }
}

/// GET /api/v1/stream-suggestions
pub async fn list_my_stream_suggestions(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<ListSuggestionsQuery>,
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

    let page = params.page.max(1);
    let page_size = params.page_size.clamp(1, 100);
    let offset = (page - 1) * page_size;

    let mut count_sql = String::from("SELECT COUNT(*) FROM stream_suggestions WHERE user_id = $1");
    let mut fetch_sql = String::from("SELECT id FROM stream_suggestions WHERE user_id = $1");
    let mut extra_binds: Vec<String> = Vec::new();
    let mut next_idx = 2i32;

    if let Some(ref s) = params.status {
        count_sql.push_str(&format!(" AND status = ${next_idx}"));
        fetch_sql.push_str(&format!(" AND status = ${next_idx}"));
        extra_binds.push(s.clone());
        next_idx += 1;
    }
    if let Some(ref st) = params.suggestion_type {
        count_sql.push_str(&format!(" AND suggestion_type = ${next_idx}"));
        fetch_sql.push_str(&format!(" AND suggestion_type = ${next_idx}"));
        extra_binds.push(st.clone());
        next_idx += 1;
    }

    fetch_sql.push_str(&format!(
        " ORDER BY created_at DESC LIMIT ${next_idx} OFFSET ${}",
        next_idx + 1
    ));

    let mut cq = sqlx::query_scalar::<_, i64>(&count_sql).bind(user_id);
    for v in &extra_binds {
        cq = cq.bind(v.clone());
    }
    let total: i64 = cq.fetch_one(&state.pool_ro).await.unwrap_or(0);

    let mut fq = sqlx::query_as::<_, (String,)>(&fetch_sql).bind(user_id);
    for v in &extra_binds {
        fq = fq.bind(v.clone());
    }
    fq = fq.bind(page_size).bind(offset);
    let ids: Vec<(String,)> = fq.fetch_all(&state.pool_ro).await.unwrap_or_default();

    let mut suggestions = Vec::new();
    for (id,) in &ids {
        if let Some(row) = fetch_suggestion(&state.pool_ro, id).await {
            suggestions.push(suggestion_to_json(&state.pool_ro, &row).await);
        }
    }

    Json(json!({
        "suggestions": suggestions,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": offset + page_size < total,
    }))
    .into_response()
}

/// GET /api/v1/stream-suggestions/stats
pub async fn get_stream_suggestion_stats(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
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

    let role = get_user_role(&state.pool_ro, user_id)
        .await
        .unwrap_or_default();
    let is_moderator = is_mod_or_admin(&role);

    let (total, pending, approved, auto_approved, rejected, approved_today, rejected_today) =
        if is_moderator {
            let total: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM stream_suggestions")
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0);
            let pending: i64 = sqlx::query_scalar(
                "SELECT COUNT(*) FROM stream_suggestions WHERE status = 'pending'",
            )
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);
            let approved: i64 = sqlx::query_scalar(
                "SELECT COUNT(*) FROM stream_suggestions WHERE status = 'approved'",
            )
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);
            let auto_approved: i64 = sqlx::query_scalar(
                "SELECT COUNT(*) FROM stream_suggestions WHERE status = 'auto_approved'",
            )
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);
            let rejected: i64 = sqlx::query_scalar(
                "SELECT COUNT(*) FROM stream_suggestions WHERE status = 'rejected'",
            )
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);
            let at: i64 = sqlx::query_scalar(
                "SELECT COUNT(*) FROM stream_suggestions WHERE status IN ('approved', 'auto_approved') AND reviewed_at >= CURRENT_DATE",
            ).fetch_one(&state.pool_ro).await.unwrap_or(0);
            let rt: i64 = sqlx::query_scalar(
                "SELECT COUNT(*) FROM stream_suggestions WHERE status = 'rejected' AND reviewed_at >= CURRENT_DATE",
            ).fetch_one(&state.pool_ro).await.unwrap_or(0);
            (total, pending, approved, auto_approved, rejected, at, rt)
        } else {
            (0, 0, 0, 0, 0, 0, 0)
        };

    let user_pending: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM stream_suggestions WHERE user_id = $1 AND status = 'pending'",
    )
    .bind(user_id)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);
    let user_approved: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM stream_suggestions WHERE user_id = $1 AND status = 'approved'",
    )
    .bind(user_id)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);
    let user_auto_approved: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM stream_suggestions WHERE user_id = $1 AND status = 'auto_approved'",
    )
    .bind(user_id)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);
    let user_rejected: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM stream_suggestions WHERE user_id = $1 AND status = 'rejected'",
    )
    .bind(user_id)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);

    Json(json!({
        "total": total,
        "pending": pending,
        "approved": approved,
        "auto_approved": auto_approved,
        "rejected": rejected,
        "approved_today": approved_today,
        "rejected_today": rejected_today,
        "user_pending": user_pending,
        "user_approved": user_approved,
        "user_auto_approved": user_auto_approved,
        "user_rejected": user_rejected,
    }))
    .into_response()
}

/// GET /api/v1/stream-suggestions/pending  (moderator)
pub async fn list_pending_stream_suggestions(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<PendingQuery>,
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

    let role = get_user_role(&state.pool_ro, user_id)
        .await
        .unwrap_or_default();
    if !is_mod_or_admin(&role) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "Moderator role required"})),
        )
            .into_response();
    }

    let page = params.page.max(1);
    let page_size = params.page_size.clamp(1, 100);
    let offset = (page - 1) * page_size;

    let status_filter = params.status.as_deref().unwrap_or("pending");

    let mut count_sql = String::from("SELECT COUNT(*) FROM stream_suggestions WHERE 1=1");
    let mut fetch_sql = String::from("SELECT id FROM stream_suggestions WHERE 1=1");
    let mut extra_binds: Vec<String> = Vec::new();
    let mut next_idx = 1i32;

    if status_filter != "all" {
        count_sql.push_str(&format!(" AND status = ${next_idx}"));
        fetch_sql.push_str(&format!(" AND status = ${next_idx}"));
        extra_binds.push(status_filter.to_string());
        next_idx += 1;
    }

    if let Some(ref st) = params.suggestion_type {
        count_sql.push_str(&format!(" AND suggestion_type = ${next_idx}"));
        fetch_sql.push_str(&format!(" AND suggestion_type = ${next_idx}"));
        extra_binds.push(st.clone());
        next_idx += 1;
    }

    fetch_sql.push_str(&format!(
        " ORDER BY created_at ASC LIMIT ${next_idx} OFFSET ${}",
        next_idx + 1
    ));

    let mut cq = sqlx::query_scalar::<_, i64>(&count_sql);
    for v in &extra_binds {
        cq = cq.bind(v.clone());
    }
    let total: i64 = cq.fetch_one(&state.pool_ro).await.unwrap_or(0);

    let mut fq = sqlx::query_as::<_, (String,)>(&fetch_sql);
    for v in &extra_binds {
        fq = fq.bind(v.clone());
    }
    fq = fq.bind(page_size).bind(offset);
    let ids: Vec<(String,)> = fq.fetch_all(&state.pool_ro).await.unwrap_or_default();

    let mut suggestions = Vec::new();
    for (id,) in &ids {
        if let Some(row) = fetch_suggestion(&state.pool_ro, id).await {
            suggestions.push(suggestion_to_json(&state.pool_ro, &row).await);
        }
    }

    Json(json!({
        "suggestions": suggestions,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": offset + page_size < total,
    }))
    .into_response()
}

/// POST /api/v1/stream-suggestions/bulk-review  (moderator)
pub async fn bulk_review_stream_suggestions(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<BulkReviewBody>,
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

    let role = get_user_role(&state.pool, user_id)
        .await
        .unwrap_or_default();
    if !is_mod_or_admin(&role) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "Moderator role required"})),
        )
            .into_response();
    }

    let new_status = match body.action.as_str() {
        "approve" => "approved",
        "reject" => "rejected",
        _ => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "action must be approve or reject"})),
            )
                .into_response()
        }
    };

    let points_per_edit: i32 = sqlx::query_scalar(
        "SELECT COALESCE(points_per_stream_edit, 5) FROM contribution_settings WHERE id = 'default'",
    )
    .fetch_optional(&state.pool)
    .await
    .unwrap_or(None)
    .unwrap_or(5);

    let mut approved = 0i64;
    let mut rejected = 0i64;
    let mut skipped = 0i64;

    for id in &body.suggestion_ids {
        let row = match fetch_suggestion(&state.pool, id).await {
            None => {
                skipped += 1;
                continue;
            }
            Some(r) => r,
        };

        if row.status != "pending" {
            skipped += 1;
            continue;
        }

        if let Err(e) = sqlx::query(
            "UPDATE stream_suggestions SET status = $1, reviewed_by = $2, reviewed_at = NOW(), review_notes = $3 WHERE id = $4",
        )
        .bind(new_status)
        .bind(user_id.to_string())
        .bind(&body.review_notes)
        .bind(id)
        .execute(&state.pool)
        .await
        {
            tracing::error!("bulk_review_stream_suggestions: {e}");
            skipped += 1;
            continue;
        }

        if new_status == "approved" {
            apply_stream_field_change(
                &state.pool,
                row.stream_id,
                &row.suggestion_type,
                row.field_name.as_deref(),
                row.suggested_value.as_deref(),
            )
            .await;
            if points_per_edit > 0 {
                let _ = sqlx::query(
                    "UPDATE users SET contribution_points = GREATEST(0, COALESCE(contribution_points, 0) + $1), stream_edits_approved = COALESCE(stream_edits_approved, 0) + 1 WHERE id = $2",
                )
                .bind(points_per_edit)
                .bind(row.user_id)
                .execute(&state.pool)
                .await;
            }
            approved += 1;
        } else {
            rejected += 1;
        }
    }

    Json(json!({"approved": approved, "rejected": rejected, "skipped": skipped})).into_response()
}

/// GET /api/v1/stream-suggestions/{suggestion_id}
pub async fn get_stream_suggestion(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(suggestion_id): Path<String>,
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

    let row = match fetch_suggestion(&state.pool_ro, &suggestion_id).await {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Suggestion not found"})),
            )
                .into_response()
        }
        Some(r) => r,
    };

    let role = get_user_role(&state.pool_ro, user_id)
        .await
        .unwrap_or_default();
    if row.user_id != user_id && !is_mod_or_admin(&role) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "Access denied"})),
        )
            .into_response();
    }

    Json(suggestion_to_json(&state.pool_ro, &row).await).into_response()
}

/// DELETE /api/v1/stream-suggestions/{suggestion_id}
pub async fn delete_stream_suggestion(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(suggestion_id): Path<String>,
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

    let row = match fetch_suggestion(&state.pool, &suggestion_id).await {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Suggestion not found"})),
            )
                .into_response()
        }
        Some(r) => r,
    };

    if row.user_id != user_id {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Suggestion not found"})),
        )
            .into_response();
    }

    if row.status != "pending" {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Can only delete pending suggestions"})),
        )
            .into_response();
    }

    if let Err(e) = sqlx::query("DELETE FROM stream_suggestions WHERE id = $1")
        .bind(&suggestion_id)
        .execute(&state.pool)
        .await
    {
        tracing::error!("delete_stream_suggestion: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    StatusCode::NO_CONTENT.into_response()
}

/// PUT /api/v1/stream-suggestions/{suggestion_id}/review  (moderator)
pub async fn review_stream_suggestion(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(suggestion_id): Path<String>,
    Json(body): Json<StreamSuggestionReviewRequest>,
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

    let role = get_user_role(&state.pool, user_id)
        .await
        .unwrap_or_default();
    if !is_mod_or_admin(&role) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "Moderator role required"})),
        )
            .into_response();
    }

    let row = match fetch_suggestion(&state.pool, &suggestion_id).await {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Suggestion not found"})),
            )
                .into_response()
        }
        Some(r) => r,
    };

    if row.status != "pending" {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Suggestion has already been reviewed"})),
        )
            .into_response();
    }

    let new_status = match body.action.as_str() {
        "approve" => "approved",
        "reject" => "rejected",
        _ => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "action must be approve or reject"})),
            )
                .into_response()
        }
    };

    if let Err(e) = sqlx::query(
        "UPDATE stream_suggestions SET status = $1, reviewed_by = $2, reviewed_at = NOW(), review_notes = $3 WHERE id = $4",
    )
    .bind(new_status)
    .bind(user_id.to_string())
    .bind(&body.review_notes)
    .bind(&suggestion_id)
    .execute(&state.pool)
    .await
    {
        tracing::error!("review_stream_suggestion: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    let points_per_edit: i32 = sqlx::query_scalar(
        "SELECT COALESCE(points_per_stream_edit, 5) FROM contribution_settings WHERE id = 'default'",
    )
    .fetch_optional(&state.pool)
    .await
    .unwrap_or(None)
    .unwrap_or(5);

    if new_status == "approved" {
        apply_stream_field_change(
            &state.pool,
            row.stream_id,
            &row.suggestion_type,
            row.field_name.as_deref(),
            row.suggested_value.as_deref(),
        )
        .await;
        if points_per_edit > 0 {
            let _ = sqlx::query(
                "UPDATE users SET contribution_points = GREATEST(0, COALESCE(contribution_points, 0) + $1), stream_edits_approved = COALESCE(stream_edits_approved, 0) + 1 WHERE id = $2",
            )
            .bind(points_per_edit)
            .bind(row.user_id)
            .execute(&state.pool)
            .await;
        }
    }

    let updated = match fetch_suggestion(&state.pool, &suggestion_id).await {
        None => return StatusCode::INTERNAL_SERVER_ERROR.into_response(),
        Some(r) => r,
    };
    Json(suggestion_to_json(&state.pool, &updated).await).into_response()
}

/// PATCH /api/v1/stream-suggestions/{suggestion_id}/triage  (moderator)
pub async fn triage_stream_suggestion(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(suggestion_id): Path<String>,
    Json(body): Json<TriageRequest>,
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

    let role = get_user_role(&state.pool, user_id)
        .await
        .unwrap_or_default();
    if !is_mod_or_admin(&role) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "Moderator role required"})),
        )
            .into_response();
    }

    let exists: bool =
        sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM stream_suggestions WHERE id = $1)")
            .bind(&suggestion_id)
            .fetch_one(&state.pool)
            .await
            .unwrap_or(false);

    if !exists {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Suggestion not found"})),
        )
            .into_response();
    }

    if let Err(e) = sqlx::query(
        "UPDATE stream_suggestions SET issue_triage_status = $1, issue_triage_note = $2 WHERE id = $3",
    )
    .bind(&body.issue_triage_status)
    .bind(&body.issue_triage_note)
    .bind(&suggestion_id)
    .execute(&state.pool)
    .await
    {
        tracing::error!("triage_stream_suggestion: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    let updated = match fetch_suggestion(&state.pool, &suggestion_id).await {
        None => return StatusCode::INTERNAL_SERVER_ERROR.into_response(),
        Some(r) => r,
    };
    Json(suggestion_to_json(&state.pool, &updated).await).into_response()
}

/// GET /api/v1/streams/{stream_id}/editable-fields
#[allow(clippy::type_complexity)]
pub async fn get_stream_editable_fields(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(stream_id): Path<i32>,
) -> Response {
    let _user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response()
        }
    };

    let row: Option<(
        Option<String>,
        Option<String>,
        Option<String>,
        Option<String>,
        Option<String>,
    )> = sqlx::query_as(
        "SELECT name, resolution, codec, quality, bit_depth FROM stream WHERE id = $1",
    )
    .bind(stream_id)
    .fetch_optional(&state.pool_ro)
    .await
    .unwrap_or(None);

    let (name, resolution, codec, quality, bit_depth) = match row {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Stream not found"})),
            )
                .into_response()
        }
        Some(r) => r,
    };

    let fields = vec![
        json!({"field_name": "name", "display_name": "Name", "current_value": name, "field_type": "text", "options": null}),
        json!({"field_name": "resolution", "display_name": "Resolution", "current_value": resolution, "field_type": "select", "options": ["4K","2160p","1080p","720p","480p","SD"]}),
        json!({"field_name": "codec", "display_name": "Codec", "current_value": codec, "field_type": "select", "options": ["HEVC","H.265","AVC","H.264","VP9","AV1","XviD","DivX"]}),
        json!({"field_name": "quality", "display_name": "Quality", "current_value": quality, "field_type": "select", "options": ["WEB-DL","WEBRip","BluRay","BDRip","HDRip","DVDRip","HDTV","CAM","TC","TS"]}),
        json!({"field_name": "bit_depth", "display_name": "Bit Depth", "current_value": bit_depth, "field_type": "select", "options": ["8-bit","10-bit","12-bit"]}),
    ];

    Json(json!({
        "stream_id": stream_id,
        "stream_name": fields[0]["current_value"],
        "fields": fields,
    }))
    .into_response()
}

/// GET /api/v1/streams/{stream_id}/signals  (optional auth)
pub async fn get_stream_signals(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(stream_id): Path<i32>,
) -> Response {
    let user_id = validate_token_optional(&headers, &state.config.secret_key_raw);

    let stream_row: Option<(bool,)> = sqlx::query_as("SELECT is_blocked FROM stream WHERE id = $1")
        .bind(stream_id)
        .fetch_optional(&state.pool_ro)
        .await
        .unwrap_or(None);

    if stream_row.is_none() {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Stream not found"})),
        )
            .into_response();
    }

    let is_blocked = stream_row.map(|(b,)| b).unwrap_or(false);

    let issue_count: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM stream_suggestions WHERE stream_id = $1 AND suggestion_type = 'report_broken' AND status IN ('approved', 'auto_approved', 'pending')",
    )
    .bind(stream_id)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);

    let vote_counts: (Option<i64>, Option<i64>) = sqlx::query_as(
        "SELECT COUNT(*) FILTER (WHERE vote_type = 'up'), COUNT(*) FILTER (WHERE vote_type = 'down') FROM stream_votes WHERE stream_id = $1",
    )
    .bind(stream_id)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or((None, None));

    let upvotes = vote_counts.0.unwrap_or(0);
    let downvotes = vote_counts.1.unwrap_or(0);
    let total_votes = upvotes + downvotes;
    let score = upvotes - downvotes;

    let threshold: i32 = sqlx::query_scalar(
        "SELECT COALESCE(auto_block_broken_report_threshold, 10) FROM contribution_settings WHERE id = 'default'",
    )
    .fetch_optional(&state.pool_ro)
    .await
    .unwrap_or(None)
    .unwrap_or(10);

    let auto_block: bool = sqlx::query_scalar(
        "SELECT COALESCE(auto_block_on_broken_reports, true) FROM contribution_settings WHERE id = 'default'",
    )
    .fetch_optional(&state.pool_ro)
    .await
    .unwrap_or(None)
    .unwrap_or(true);

    let user_has_issue_report = if let Some(uid) = user_id {
        let has: bool = sqlx::query_scalar(
            "SELECT EXISTS(SELECT 1 FROM stream_suggestions WHERE stream_id = $1 AND user_id = $2 AND suggestion_type = 'report_broken')",
        )
        .bind(stream_id)
        .bind(uid)
        .fetch_one(&state.pool_ro)
        .await
        .unwrap_or(false);
        Some(has)
    } else {
        None
    };

    let user_vote = if let Some(uid) = user_id {
        let vt: Option<String> = sqlx::query_scalar(
            "SELECT vote_type FROM stream_votes WHERE user_id = $1 AND stream_id = $2 LIMIT 1",
        )
        .bind(uid)
        .bind(stream_id)
        .fetch_optional(&state.pool_ro)
        .await
        .unwrap_or(None);
        vt.map(|v| if v == "up" { 1 } else { -1 })
    } else {
        None
    };

    let reports_needed = if is_blocked || issue_count >= threshold as i64 {
        0i64
    } else {
        threshold as i64 - issue_count
    };

    Json(json!({
        "stream_id": stream_id,
        "is_blocked": is_blocked,
        "issue_report_count": issue_count,
        "auto_block_on_broken_reports": auto_block,
        "broken_report_threshold": threshold,
        "rating_up": upvotes,
        "rating_down": downvotes,
        "rating_score": score,
        "rating_total": total_votes,
        "user_has_issue_report": user_has_issue_report,
        "user_has_report_broken": user_has_issue_report,
        "user_vote": user_vote,
        "recent_reasons": [],
        "legacy_approved_broken_reporters": 0,
        "reports_needed_for_auto_block": reports_needed,
    }))
    .into_response()
}

/// GET /api/v1/streams/{stream_id}/suggestions
pub async fn list_stream_suggestions(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(stream_id): Path<i64>,
    Query(params): Query<ListSuggestionsQuery>,
) -> Response {
    let _user_id = validate_token_optional(&headers, &state.config.secret_key_raw);

    let page = params.page.max(1);
    let page_size = params.page_size.clamp(1, 100);
    let offset = (page - 1) * page_size;

    let count: i64 =
        sqlx::query_scalar("SELECT COUNT(*) FROM stream_suggestions WHERE stream_id = $1")
            .bind(stream_id)
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);

    let ids: Vec<(String,)> = sqlx::query_as(
        "SELECT id FROM stream_suggestions WHERE stream_id = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3",
    )
    .bind(stream_id)
    .bind(page_size)
    .bind(offset)
    .fetch_all(&state.pool_ro)
    .await
    .unwrap_or_default();

    let mut suggestions = Vec::new();
    for (id,) in &ids {
        if let Some(row) = fetch_suggestion(&state.pool_ro, id).await {
            suggestions.push(suggestion_to_json(&state.pool_ro, &row).await);
        }
    }

    Json(json!({
        "suggestions": suggestions,
        "total": count,
        "page": page,
        "page_size": page_size,
        "has_more": offset + page_size < count,
    }))
    .into_response()
}

/// GET /api/v1/streams/{stream_id}/broken-status
pub async fn get_stream_broken_status(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(stream_id): Path<i32>,
) -> Response {
    let _user_id = validate_token_optional(&headers, &state.config.secret_key_raw);

    let row: Option<(bool,)> = sqlx::query_as("SELECT is_blocked FROM stream WHERE id = $1")
        .bind(stream_id)
        .fetch_optional(&state.pool_ro)
        .await
        .unwrap_or(None);

    if row.is_none() {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Stream not found"})),
        )
            .into_response();
    }

    let is_blocked = row.map(|(b,)| b).unwrap_or(false);
    let broken_count: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM stream_suggestions WHERE stream_id = $1 AND suggestion_type = 'report_broken' AND status IN ('approved', 'auto_approved', 'pending')",
    )
    .bind(stream_id)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);

    Json(json!({
        "stream_id": stream_id,
        "is_blocked": is_blocked,
        "broken_report_count": broken_count,
    }))
    .into_response()
}

/// PATCH /api/v1/streams/{stream_id}/broken-status
pub async fn update_stream_broken_status(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(stream_id): Path<i32>,
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

    let role = get_user_role(&state.pool, user_id)
        .await
        .unwrap_or_default();
    if !is_mod_or_admin(&role) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "Moderator role required"})),
        )
            .into_response();
    }

    // Check stream exists
    let existing: Option<(bool,)> = sqlx::query_as("SELECT is_blocked FROM streams WHERE id = $1")
        .bind(stream_id)
        .fetch_optional(&state.pool_ro)
        .await
        .unwrap_or(None);

    if existing.is_none() {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Stream not found"})),
        )
            .into_response();
    }

    // Toggle is_blocked
    let row: Option<(bool,)> = sqlx::query_as(
        "UPDATE streams SET is_blocked = NOT is_blocked WHERE id = $1 RETURNING is_blocked",
    )
    .bind(stream_id)
    .fetch_optional(&state.pool)
    .await
    .unwrap_or(None);

    match row {
        Some((new_blocked,)) => {
            let message = if new_blocked {
                "Stream blocked"
            } else {
                "Stream unblocked"
            };
            Json(json!({
                "stream_id": stream_id,
                "is_blocked": new_blocked,
                "message": message,
            }))
            .into_response()
        }
        None => (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Stream not found"})),
        )
            .into_response(),
    }
}

/// POST /api/v1/streams/signals/bulk  (optional auth)
pub async fn bulk_stream_signals(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(stream_ids): Json<Vec<i64>>,
) -> Response {
    if stream_ids.len() > 100 {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Maximum 100 stream_ids allowed"})),
        )
            .into_response();
    }

    let user_id = validate_token_optional(&headers, &state.config.secret_key_raw);

    let mut signals = serde_json::Map::new();

    for stream_id in &stream_ids {
        let issue_count: i64 = sqlx::query_scalar(
            "SELECT COUNT(*) FROM stream_suggestions WHERE stream_id = $1 AND suggestion_type = 'report_broken' AND status IN ('approved', 'auto_approved', 'pending')",
        )
        .bind(stream_id)
        .fetch_one(&state.pool_ro)
        .await
        .unwrap_or(0);

        let vote_counts: (Option<i64>, Option<i64>) = sqlx::query_as(
            "SELECT COUNT(*) FILTER (WHERE vote_type = 'up'), COUNT(*) FILTER (WHERE vote_type = 'down') FROM stream_votes WHERE stream_id = $1",
        )
        .bind(*stream_id as i32)
        .fetch_one(&state.pool_ro)
        .await
        .unwrap_or((None, None));

        let upvotes = vote_counts.0.unwrap_or(0);
        let downvotes = vote_counts.1.unwrap_or(0);
        let score = upvotes - downvotes;
        let total_votes = upvotes + downvotes;

        let user_vote = if let Some(uid) = user_id {
            let vt: Option<String> = sqlx::query_scalar(
                "SELECT vote_type FROM stream_votes WHERE user_id = $1 AND stream_id = $2 LIMIT 1",
            )
            .bind(uid)
            .bind(*stream_id as i32)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None);
            vt.map(|v| if v == "up" { 1i32 } else { -1 })
        } else {
            None
        };

        let user_has_issue_report = if let Some(uid) = user_id {
            let has: bool = sqlx::query_scalar(
                "SELECT EXISTS(SELECT 1 FROM stream_suggestions WHERE stream_id = $1 AND user_id = $2 AND suggestion_type = 'report_broken')",
            )
            .bind(stream_id)
            .bind(uid)
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(false);
            Some(has)
        } else {
            None
        };

        signals.insert(
            stream_id.to_string(),
            json!({
                "issue_report_count": issue_count,
                "rating_up": upvotes,
                "rating_down": downvotes,
                "rating_score": score,
                "rating_total": total_votes,
                "user_vote": user_vote,
                "user_has_issue_report": user_has_issue_report,
            }),
        );
    }

    Json(json!({"signals": serde_json::Value::Object(signals)})).into_response()
}
