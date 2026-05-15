/// Episode Suggestions endpoints — users suggest corrections to episode metadata.
///
/// Routes:
///   POST   /api/v1/episode/{episode_id}/suggest              → create_episode_suggestion
///   GET    /api/v1/episode-suggestions                        → list_my_episode_suggestions
///   GET    /api/v1/episode-suggestions/stats                  → get_episode_suggestion_stats
///   GET    /api/v1/episode-suggestions/pending                → list_pending_episode_suggestions  (moderator)
///   POST   /api/v1/episode-suggestions/bulk-review            → bulk_review_episode_suggestions   (moderator)
///   GET    /api/v1/episode-suggestions/{suggestion_id}        → get_episode_suggestion
///   DELETE /api/v1/episode-suggestions/{suggestion_id}        → delete_episode_suggestion
///   PUT    /api/v1/episode-suggestions/{suggestion_id}/review → review_episode_suggestion         (moderator)
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

fn validate_token(headers: &HeaderMap, secret_key: &str) -> Option<i64> {
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

async fn get_user_role(pool: &sqlx::PgPool, user_id: i64) -> Option<String> {
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
pub struct EpisodeSuggestionCreateRequest {
    pub field_name: String,
    pub current_value: Option<String>,
    pub suggested_value: String,
    pub reason: Option<String>,
}

#[derive(Deserialize)]
pub struct EpisodeSuggestionReviewRequest {
    pub action: String,
    pub review_notes: Option<String>,
}

#[derive(Deserialize)]
pub struct ListSuggestionsQuery {
    pub status: Option<String>,
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
    pub field_name: Option<String>,
    #[serde(default = "default_page")]
    pub page: i64,
    #[serde(default = "default_page_size")]
    pub page_size: i64,
}

#[derive(Deserialize)]
pub struct BulkReviewQuery {
    pub action: String,
    pub review_notes: Option<String>,
}

// ─── DB helpers ───────────────────────────────────────────────────────────────

struct SuggestionRow {
    id: String,
    user_id: i64,
    episode_id: i64,
    field_name: String,
    current_value: Option<String>,
    suggested_value: String,
    reason: Option<String>,
    status: String,
    reviewed_by: Option<String>,
    reviewed_at: Option<DateTime<Utc>>,
    review_notes: Option<String>,
    created_at: DateTime<Utc>,
    updated_at: Option<DateTime<Utc>>,
}

async fn fetch_suggestion(pool: &sqlx::PgPool, id: &str) -> Option<SuggestionRow> {
    type R = (
        String,
        i64,
        i64,
        String,
        Option<String>,
        String,
        Option<String>,
        String,
        Option<String>,
        Option<DateTime<Utc>>,
        Option<String>,
        DateTime<Utc>,
        Option<DateTime<Utc>>,
    );
    let row: R = sqlx::query_as::<_, R>(
        r#"SELECT id, user_id, episode_id, field_name, current_value, suggested_value,
                      reason, status, reviewed_by, reviewed_at, review_notes, created_at, updated_at
               FROM episode_suggestion WHERE id = $1"#,
    )
    .bind(id)
    .fetch_optional(pool)
    .await
    .unwrap_or(None)?;

    Some(SuggestionRow {
        id: row.0,
        user_id: row.1,
        episode_id: row.2,
        field_name: row.3,
        current_value: row.4,
        suggested_value: row.5,
        reason: row.6,
        status: row.7,
        reviewed_by: row.8,
        reviewed_at: row.9,
        review_notes: row.10,
        created_at: row.11,
        updated_at: row.12,
    })
}

async fn suggestion_to_json(pool: &sqlx::PgPool, row: &SuggestionRow) -> serde_json::Value {
    let username: Option<String> = sqlx::query_scalar("SELECT username FROM users WHERE id = $1")
        .bind(row.user_id)
        .fetch_optional(pool)
        .await
        .unwrap_or(None);

    let reviewer_name: Option<String> = if let Some(ref rb) = row.reviewed_by {
        if let Ok(rid) = rb.parse::<i64>() {
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

    // Episode info
    let ep_info: Option<(String, i32, i32, String)> = sqlx::query_as(
        r#"SELECT e.title, e.episode_number, s.season_number, m.title
           FROM episode e
           JOIN season s ON s.id = e.season_id
           JOIN series_metadata sm ON sm.id = s.series_id
           JOIN media m ON m.id = sm.media_id
           WHERE e.id = $1"#,
    )
    .bind(row.episode_id)
    .fetch_optional(pool)
    .await
    .unwrap_or(None);

    let (ep_title, ep_num, season_num, series_title) = ep_info
        .map(|(t, en, sn, st)| (Some(t), Some(en), Some(sn), Some(st)))
        .unwrap_or((None, None, None, None));

    // User contribution info
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
        "episode_id": row.episode_id,
        "episode_title": ep_title,
        "season_number": season_num,
        "episode_number": ep_num,
        "series_title": series_title,
        "field_name": row.field_name,
        "current_value": row.current_value,
        "suggested_value": row.suggested_value,
        "reason": row.reason,
        "status": row.status,
        "was_auto_approved": row.status == "auto_approved",
        "reviewed_by": reviewer_name,
        "reviewed_at": row.reviewed_at.map(|d| d.to_rfc3339()),
        "review_notes": row.review_notes,
        "created_at": row.created_at.to_rfc3339(),
        "updated_at": row.updated_at.map(|d| d.to_rfc3339()),
        "user_contribution_level": user_contribution_level,
        "user_contribution_points": user_contribution_points,
    })
}

/// Apply an episode field change
async fn apply_episode_change(
    pool: &sqlx::PgPool,
    episode_id: i64,
    field_name: &str,
    value: &str,
) -> bool {
    let result = match field_name {
        "title" => {
            sqlx::query("UPDATE episode SET title = $1, updated_at = NOW() WHERE id = $2")
                .bind(value)
                .bind(episode_id)
                .execute(pool)
                .await
        }
        "overview" => {
            sqlx::query("UPDATE episode SET overview = $1, updated_at = NOW() WHERE id = $2")
                .bind(value)
                .bind(episode_id)
                .execute(pool)
                .await
        }
        "air_date" => {
            sqlx::query("UPDATE episode SET air_date = $1::date, updated_at = NOW() WHERE id = $2")
                .bind(value)
                .bind(episode_id)
                .execute(pool)
                .await
        }
        "runtime_minutes" => {
            if let Ok(minutes) = value.parse::<i32>() {
                sqlx::query(
                    "UPDATE episode SET runtime_minutes = $1, updated_at = NOW() WHERE id = $2",
                )
                .bind(minutes)
                .bind(episode_id)
                .execute(pool)
                .await
            } else {
                return false;
            }
        }
        _ => return false,
    };
    result.is_ok()
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// POST /api/v1/episode/{episode_id}/suggest
pub async fn create_episode_suggestion(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(episode_id): Path<i64>,
    Json(body): Json<EpisodeSuggestionCreateRequest>,
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

    // Verify episode exists
    let ep_exists: bool = sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM episode WHERE id = $1)")
        .bind(episode_id)
        .fetch_one(&state.pool)
        .await
        .unwrap_or(false);

    if !ep_exists {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Episode not found"})),
        )
            .into_response();
    }

    // Check for duplicate pending suggestion
    let dup: bool = sqlx::query_scalar(
        "SELECT EXISTS(SELECT 1 FROM episode_suggestion WHERE user_id = $1 AND episode_id = $2 AND field_name = $3 AND status = 'pending')",
    )
    .bind(user_id)
    .bind(episode_id)
    .bind(&body.field_name)
    .fetch_one(&state.pool)
    .await
    .unwrap_or(false);

    if dup {
        return (
            StatusCode::CONFLICT,
            Json(json!({"detail": "You already have a pending suggestion for this field"})),
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

    let suggestion_id = Uuid::new_v4().to_string();

    let mut reviewed_by: Option<String> = None;
    let mut review_notes: Option<String> = None;
    let mut apply_success = true;

    if can_auto_approve {
        reviewed_by = Some(user_id.to_string());
        review_notes = Some("Auto-approved based on user reputation".to_string());
        apply_success = apply_episode_change(
            &state.pool,
            episode_id,
            &body.field_name,
            &body.suggested_value,
        )
        .await;
    }

    let final_status = if can_auto_approve && apply_success {
        "auto_approved"
    } else {
        "pending"
    };
    let final_reviewed_by = if final_status == "auto_approved" {
        reviewed_by
    } else {
        None
    };
    let final_review_notes = if final_status == "auto_approved" {
        review_notes
    } else {
        None
    };

    if let Err(e) = sqlx::query(
        r#"INSERT INTO episode_suggestion
               (id, user_id, episode_id, field_name, current_value, suggested_value,
                reason, status, reviewed_by, reviewed_at, review_notes, created_at, updated_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9,
                   CASE WHEN $9 IS NOT NULL THEN NOW() ELSE NULL END,
                   $10, NOW(), NOW())"#,
    )
    .bind(&suggestion_id)
    .bind(user_id)
    .bind(episode_id)
    .bind(&body.field_name)
    .bind(&body.current_value)
    .bind(&body.suggested_value)
    .bind(&body.reason)
    .bind(final_status)
    .bind(&final_reviewed_by)
    .bind(&final_review_notes)
    .execute(&state.pool)
    .await
    {
        tracing::error!("create_episode_suggestion: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
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

/// GET /api/v1/episode-suggestions
pub async fn list_my_episode_suggestions(
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

    let mut count_sql = String::from("SELECT COUNT(*) FROM episode_suggestion WHERE user_id = $1");
    let mut fetch_sql = String::from("SELECT id FROM episode_suggestion WHERE user_id = $1");

    if let Some(ref s) = params.status {
        let esc = s.replace('\'', "''");
        count_sql.push_str(&format!(" AND status = '{esc}'"));
        fetch_sql.push_str(&format!(" AND status = '{esc}'"));
    }

    fetch_sql.push_str(" ORDER BY created_at DESC LIMIT $2 OFFSET $3");

    let total: i64 = sqlx::query_scalar(&count_sql)
        .bind(user_id)
        .fetch_one(&state.pool_ro)
        .await
        .unwrap_or(0);

    let ids: Vec<(String,)> = sqlx::query_as::<_, (String,)>(&fetch_sql)
        .bind(user_id)
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
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": offset + page_size < total,
    }))
    .into_response()
}

/// GET /api/v1/episode-suggestions/stats
pub async fn get_episode_suggestion_stats(
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
            let total: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM episode_suggestion")
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0);
            let pending: i64 = sqlx::query_scalar(
                "SELECT COUNT(*) FROM episode_suggestion WHERE status = 'pending'",
            )
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);
            let approved: i64 = sqlx::query_scalar(
                "SELECT COUNT(*) FROM episode_suggestion WHERE status = 'approved'",
            )
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);
            let auto_approved: i64 = sqlx::query_scalar(
                "SELECT COUNT(*) FROM episode_suggestion WHERE status = 'auto_approved'",
            )
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);
            let rejected: i64 = sqlx::query_scalar(
                "SELECT COUNT(*) FROM episode_suggestion WHERE status = 'rejected'",
            )
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);
            let approved_today: i64 = sqlx::query_scalar(
                "SELECT COUNT(*) FROM episode_suggestion WHERE status IN ('approved', 'auto_approved') AND reviewed_at >= CURRENT_DATE",
            )
            .fetch_one(&state.pool_ro).await.unwrap_or(0);
            let rejected_today: i64 = sqlx::query_scalar(
                "SELECT COUNT(*) FROM episode_suggestion WHERE status = 'rejected' AND reviewed_at >= CURRENT_DATE",
            )
            .fetch_one(&state.pool_ro).await.unwrap_or(0);
            (
                total,
                pending,
                approved,
                auto_approved,
                rejected,
                approved_today,
                rejected_today,
            )
        } else {
            (0, 0, 0, 0, 0, 0, 0)
        };

    let user_pending: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM episode_suggestion WHERE user_id = $1 AND status = 'pending'",
    )
    .bind(user_id)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);

    let user_approved: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM episode_suggestion WHERE user_id = $1 AND status = 'approved'",
    )
    .bind(user_id)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);

    let user_auto_approved: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM episode_suggestion WHERE user_id = $1 AND status = 'auto_approved'",
    )
    .bind(user_id)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);

    let user_rejected: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM episode_suggestion WHERE user_id = $1 AND status = 'rejected'",
    )
    .bind(user_id)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);

    let user_points: i32 =
        sqlx::query_scalar("SELECT COALESCE(contribution_points, 0) FROM users WHERE id = $1")
            .bind(user_id)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None)
            .unwrap_or(0);

    let user_level: String =
        sqlx::query_scalar("SELECT COALESCE(contribution_level, 'new') FROM users WHERE id = $1")
            .bind(user_id)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None)
            .unwrap_or_else(|| "new".to_string());

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
        "user_contribution_points": user_points,
        "user_contribution_level": user_level,
    }))
    .into_response()
}

/// GET /api/v1/episode-suggestions/pending  (moderator)
pub async fn list_pending_episode_suggestions(
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

    let mut count_sql =
        String::from("SELECT COUNT(*) FROM episode_suggestion WHERE status = 'pending'");
    let mut fetch_sql = String::from("SELECT id FROM episode_suggestion WHERE status = 'pending'");

    if let Some(ref fn_) = params.field_name {
        let esc = fn_.replace('\'', "''");
        count_sql.push_str(&format!(" AND field_name = '{esc}'"));
        fetch_sql.push_str(&format!(" AND field_name = '{esc}'"));
    }

    fetch_sql.push_str(" ORDER BY created_at ASC LIMIT $1 OFFSET $2");

    let total: i64 = sqlx::query_scalar(&count_sql)
        .fetch_one(&state.pool_ro)
        .await
        .unwrap_or(0);

    let ids: Vec<(String,)> = sqlx::query_as::<_, (String,)>(&fetch_sql)
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
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": offset + page_size < total,
    }))
    .into_response()
}

/// POST /api/v1/episode-suggestions/bulk-review  (moderator)
pub async fn bulk_review_episode_suggestions(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<BulkReviewQuery>,
    Json(suggestion_ids): Json<Vec<String>>,
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

    let points_per_edit: i32 = sqlx::query_scalar(
        "SELECT COALESCE(points_per_metadata_edit, 5) FROM contribution_settings WHERE id = 'default'",
    )
    .fetch_optional(&state.pool)
    .await
    .unwrap_or(None)
    .unwrap_or(5);

    let rejection_penalty: i32 = sqlx::query_scalar(
        "SELECT COALESCE(points_for_rejection_penalty, 0) FROM contribution_settings WHERE id = 'default'",
    )
    .fetch_optional(&state.pool)
    .await
    .unwrap_or(None)
    .unwrap_or(0);

    let new_status = match params.action.as_str() {
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

    let mut approved = 0i64;
    let mut rejected = 0i64;
    let mut skipped = 0i64;

    for id in &suggestion_ids {
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
            "UPDATE episode_suggestion SET status = $1, reviewed_by = $2, reviewed_at = NOW(), review_notes = $3, updated_at = NOW() WHERE id = $4",
        )
        .bind(new_status)
        .bind(user_id.to_string())
        .bind(&params.review_notes)
        .bind(id)
        .execute(&state.pool)
        .await
        {
            tracing::error!("bulk_review_episode_suggestions: {e}");
            skipped += 1;
            continue;
        }

        if new_status == "approved" {
            apply_episode_change(
                &state.pool,
                row.episode_id,
                &row.field_name,
                &row.suggested_value,
            )
            .await;
            if points_per_edit > 0 {
                let _ = sqlx::query(
                    "UPDATE users SET contribution_points = GREATEST(0, COALESCE(contribution_points, 0) + $1), metadata_edits_approved = COALESCE(metadata_edits_approved, 0) + 1 WHERE id = $2",
                )
                .bind(points_per_edit)
                .bind(row.user_id)
                .execute(&state.pool)
                .await;
            }
            approved += 1;
        } else {
            if rejection_penalty < 0 {
                let _ = sqlx::query(
                    "UPDATE users SET contribution_points = GREATEST(0, COALESCE(contribution_points, 0) + $1) WHERE id = $2",
                )
                .bind(rejection_penalty)
                .bind(row.user_id)
                .execute(&state.pool)
                .await;
            }
            rejected += 1;
        }
    }

    Json(json!({"approved": approved, "rejected": rejected, "skipped": skipped})).into_response()
}

/// GET /api/v1/episode-suggestions/{suggestion_id}
pub async fn get_episode_suggestion(
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

/// DELETE /api/v1/episode-suggestions/{suggestion_id}
pub async fn delete_episode_suggestion(
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

    if let Err(e) = sqlx::query("DELETE FROM episode_suggestion WHERE id = $1")
        .bind(&suggestion_id)
        .execute(&state.pool)
        .await
    {
        tracing::error!("delete_episode_suggestion: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    StatusCode::NO_CONTENT.into_response()
}

/// PUT /api/v1/episode-suggestions/{suggestion_id}/review  (moderator)
pub async fn review_episode_suggestion(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(suggestion_id): Path<String>,
    Json(body): Json<EpisodeSuggestionReviewRequest>,
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
        "UPDATE episode_suggestion SET status = $1, reviewed_by = $2, reviewed_at = NOW(), review_notes = $3, updated_at = NOW() WHERE id = $4",
    )
    .bind(new_status)
    .bind(user_id.to_string())
    .bind(&body.review_notes)
    .bind(&suggestion_id)
    .execute(&state.pool)
    .await
    {
        tracing::error!("review_episode_suggestion: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    let points_per_edit: i32 = sqlx::query_scalar(
        "SELECT COALESCE(points_per_metadata_edit, 5) FROM contribution_settings WHERE id = 'default'",
    )
    .fetch_optional(&state.pool)
    .await
    .unwrap_or(None)
    .unwrap_or(5);

    let rejection_penalty: i32 = sqlx::query_scalar(
        "SELECT COALESCE(points_for_rejection_penalty, 0) FROM contribution_settings WHERE id = 'default'",
    )
    .fetch_optional(&state.pool)
    .await
    .unwrap_or(None)
    .unwrap_or(0);

    if new_status == "approved" {
        apply_episode_change(
            &state.pool,
            row.episode_id,
            &row.field_name,
            &row.suggested_value,
        )
        .await;
        if points_per_edit > 0 {
            let _ = sqlx::query(
                "UPDATE users SET contribution_points = GREATEST(0, COALESCE(contribution_points, 0) + $1), metadata_edits_approved = COALESCE(metadata_edits_approved, 0) + 1 WHERE id = $2",
            )
            .bind(points_per_edit)
            .bind(row.user_id)
            .execute(&state.pool)
            .await;
        }
    } else if new_status == "rejected" && rejection_penalty < 0 {
        let _ = sqlx::query(
            "UPDATE users SET contribution_points = GREATEST(0, COALESCE(contribution_points, 0) + $1) WHERE id = $2",
        )
        .bind(rejection_penalty)
        .bind(row.user_id)
        .execute(&state.pool)
        .await;
    }

    let updated = match fetch_suggestion(&state.pool, &suggestion_id).await {
        None => return StatusCode::INTERNAL_SERVER_ERROR.into_response(),
        Some(r) => r,
    };
    Json(suggestion_to_json(&state.pool, &updated).await).into_response()
}
