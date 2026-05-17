/// Contributions endpoints — user-submitted metadata/stream contributions with mod review.
///
/// Routes (prefix /api/v1/contributions):
///   GET    /                           → list_contributions
///   POST   /                           → create_contribution
///   GET    /stats                      → get_contribution_stats
///   GET    /contributors               → list_contribution_contributors  (moderator)
///   GET    /review/pending             → list_pending_contributions      (moderator)
///   GET    /review/stats               → get_all_contribution_stats      (moderator)
///   POST   /review/bulk                → bulk_review_contributions       (moderator)
///   GET    /{contribution_id}          → get_contribution
///   DELETE /{contribution_id}          → delete_contribution
///   PATCH  /{contribution_id}/review   → review_contribution             (moderator)
///   PATCH  /{contribution_id}/flag-admin-review → flag_contribution_for_admin_review (moderator)
///   PATCH  /{contribution_id}/reject-approved  → reject_approved_contribution       (moderator)
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
use serde::{Deserialize, Deserializer};
use serde_json::json;
use sha2::Sha256;

fn bool_from_str<'de, D: Deserializer<'de>>(d: D) -> Result<bool, D::Error> {
    let s: String = String::deserialize(d)?;
    Ok(!matches!(
        s.to_lowercase().as_str(),
        "false" | "0" | "no" | ""
    ))
}
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

fn is_admin(role: &str) -> bool {
    role == "admin"
}

// ─── Request / Response structs ───────────────────────────────────────────────

#[derive(Deserialize)]
pub struct ListContributionsQuery {
    pub contribution_type: Option<String>,
    pub contribution_status: Option<String>,
    pub contributor: Option<String>,
    pub uploader_query: Option<String>,
    pub reviewer_query: Option<String>,
    #[serde(default, deserialize_with = "bool_from_str")]
    pub me_only: bool,
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
pub struct PendingListQuery {
    pub contribution_type: Option<String>,
    #[serde(default = "default_page")]
    pub page: i64,
    #[serde(default = "default_page_size")]
    pub page_size: i64,
}

#[derive(Deserialize)]
pub struct ContributionCreate {
    pub contribution_type: String,
    pub target_id: Option<String>,
    pub data: serde_json::Value,
}

#[derive(Deserialize)]
pub struct ContributionReview {
    pub status: String,
    pub review_notes: Option<String>,
}

#[derive(Deserialize)]
pub struct AdminFlagRequest {
    pub reason: Option<String>,
}

#[derive(Deserialize)]
pub struct AdminRejectRequest {
    pub review_notes: Option<String>,
}

#[derive(Deserialize)]
pub struct BulkReviewRequest {
    pub action: String,
    pub contribution_type: Option<String>,
    pub contribution_ids: Option<Vec<String>>,
    pub review_notes: Option<String>,
}

#[derive(Deserialize)]
pub struct ContributorsQuery {
    pub contribution_type: Option<String>,
    pub contribution_status: Option<String>,
    pub query: Option<String>,
    #[serde(default = "default_limit")]
    pub limit: i64,
}

fn default_limit() -> i64 {
    80
}

// ─── DB row helper ────────────────────────────────────────────────────────────

struct ContribRow {
    id: String,
    user_id: Option<i32>,
    contribution_type: String,
    target_id: Option<String>,
    data: serde_json::Value,
    status: String,
    reviewed_by: Option<String>,
    reviewed_at: Option<DateTime<Utc>>,
    review_notes: Option<String>,
    admin_review_requested: bool,
    admin_review_requested_by: Option<String>,
    admin_review_requested_at: Option<DateTime<Utc>>,
    admin_review_reason: Option<String>,
    created_at: DateTime<Utc>,
    updated_at: Option<DateTime<Utc>>,
}

async fn fetch_contrib_row(pool: &sqlx::PgPool, id: &str) -> Option<ContribRow> {
    type RowTuple = (
        String,
        Option<i32>,
        String,
        Option<String>,
        serde_json::Value,
        String,
        Option<String>,
        Option<DateTime<Utc>>,
        Option<String>,
        bool,
        Option<String>,
        Option<DateTime<Utc>>,
        Option<String>,
        DateTime<Utc>,
        Option<DateTime<Utc>>,
    );
    let row = sqlx::query_as::<_, RowTuple>(
        r#"SELECT id, user_id, contribution_type, target_id, data::jsonb, status::text,
                      reviewed_by, reviewed_at, review_notes,
                      admin_review_requested, admin_review_requested_by,
                      admin_review_requested_at, admin_review_reason,
                      created_at, updated_at
               FROM contributions WHERE id = $1"#,
    )
    .bind(id)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten()?;

    Some(ContribRow {
        id: row.0,
        user_id: row.1,
        contribution_type: row.2,
        target_id: row.3,
        data: row.4,
        status: row.5,
        reviewed_by: row.6,
        reviewed_at: row.7,
        review_notes: row.8,
        admin_review_requested: row.9,
        admin_review_requested_by: row.10,
        admin_review_requested_at: row.11,
        admin_review_reason: row.12,
        created_at: row.13,
        updated_at: row.14,
    })
}

async fn contrib_row_to_json(pool: &sqlx::PgPool, row: &ContribRow) -> serde_json::Value {
    let username: Option<String> = if let Some(uid) = row.user_id {
        sqlx::query_scalar("SELECT username FROM users WHERE id = $1")
            .bind(uid)
            .fetch_optional(pool)
            .await
            .unwrap_or(None)
    } else {
        None
    };

    let reviewer_name: Option<String> = if let Some(ref rb) = row.reviewed_by {
        if rb == "auto" {
            Some("Auto-approved".to_string())
        } else if let Ok(rid) = rb.parse::<i64>() {
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

    json!({
        "id": row.id,
        "user_id": row.user_id,
        "username": username,
        "contribution_type": row.contribution_type,
        "target_id": row.target_id,
        "data": row.data,
        "status": row.status,
        "reviewed_by": row.reviewed_by,
        "reviewer_name": reviewer_name,
        "reviewed_at": row.reviewed_at.map(|d| d.to_rfc3339()),
        "review_notes": row.review_notes,
        "admin_review_requested": row.admin_review_requested,
        "admin_review_requested_by": row.admin_review_requested_by,
        "admin_review_requested_at": row.admin_review_requested_at.map(|d| d.to_rfc3339()),
        "admin_review_reason": row.admin_review_reason,
        "created_at": row.created_at.to_rfc3339(),
        "updated_at": row.updated_at.map(|d| d.to_rfc3339()),
    })
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// GET /api/v1/contributions
pub async fn list_contributions(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<ListContributionsQuery>,
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
    let is_privileged = is_mod_or_admin(&role);
    let show_all = is_privileged && !params.me_only;

    let page = params.page.max(1);
    let page_size = params.page_size.clamp(1, 100);
    let offset = (page - 1) * page_size;

    let mut count_sql = String::from("SELECT COUNT(*) FROM contributions WHERE 1=1");
    let mut fetch_sql = String::from(
        r#"SELECT id, user_id, contribution_type, target_id, data::jsonb, status::text,
                  reviewed_by, reviewed_at, review_notes,
                  admin_review_requested, admin_review_requested_by,
                  admin_review_requested_at, admin_review_reason,
                  created_at, updated_at
           FROM contributions WHERE 1=1"#,
    );

    let mut bind_values: Vec<serde_json::Value> = Vec::new();
    let mut idx = 1i32;

    macro_rules! push_condition {
        ($cond:expr, $val:expr) => {{
            count_sql.push_str(&format!(" AND {}", $cond));
            fetch_sql.push_str(&format!(" AND {}", $cond));
            bind_values.push(json!($val));
            idx += 1;
        }};
    }

    if !show_all {
        push_condition!(format!("user_id = ${idx}"), user_id);
    }
    if let Some(ref ct) = params.contribution_type {
        push_condition!(format!("contribution_type = ${idx}"), ct.clone());
    }
    if let Some(ref cs) = params.contribution_status {
        push_condition!(format!("status = ${idx}"), cs.clone());
    }

    fetch_sql.push_str(&format!(
        " ORDER BY created_at DESC LIMIT ${idx} OFFSET ${}",
        idx + 1
    ));

    // Build and execute count query
    let mut cq = sqlx::query_scalar::<_, i64>(&count_sql);
    for v in &bind_values {
        cq = cq.bind(v.clone());
    }
    let total: i64 = cq.fetch_one(&state.pool_ro).await.unwrap_or(0);

    // Build and execute fetch query
    type ContribTuple = (
        String,
        Option<i32>,
        String,
        Option<String>,
        serde_json::Value,
        String,
        Option<String>,
        Option<DateTime<Utc>>,
        Option<String>,
        bool,
        Option<String>,
        Option<DateTime<Utc>>,
        Option<String>,
        DateTime<Utc>,
        Option<DateTime<Utc>>,
    );
    let mut fq = sqlx::query_as::<_, ContribTuple>(&fetch_sql);
    for v in &bind_values {
        fq = fq.bind(v.clone());
    }
    fq = fq.bind(page_size).bind(offset);

    let rows: Vec<ContribTuple> = fq.fetch_all(&state.pool_ro).await.unwrap_or_default();

    let mut items = Vec::new();
    for r in &rows {
        let row = ContribRow {
            id: r.0.clone(),
            user_id: r.1,
            contribution_type: r.2.clone(),
            target_id: r.3.clone(),
            data: r.4.clone(),
            status: r.5.clone(),
            reviewed_by: r.6.clone(),
            reviewed_at: r.7,
            review_notes: r.8.clone(),
            admin_review_requested: r.9,
            admin_review_requested_by: r.10.clone(),
            admin_review_requested_at: r.11,
            admin_review_reason: r.12.clone(),
            created_at: r.13,
            updated_at: r.14,
        };
        items.push(contrib_row_to_json(&state.pool_ro, &row).await);
    }

    let has_more = offset + page_size < total;
    Json(json!({
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": has_more,
    }))
    .into_response()
}

/// GET /api/v1/contributions/stats
pub async fn get_contribution_stats(
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

    let total: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM contributions WHERE user_id = $1")
        .bind(user_id)
        .fetch_one(&state.pool_ro)
        .await
        .unwrap_or(0);

    let pending: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM contributions WHERE user_id = $1 AND status = 'PENDING'",
    )
    .bind(user_id)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);

    let approved: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM contributions WHERE user_id = $1 AND status = 'APPROVED'",
    )
    .bind(user_id)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);

    let rejected: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM contributions WHERE user_id = $1 AND status = 'REJECTED'",
    )
    .bind(user_id)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);

    let stream_total: i64 =
        sqlx::query_scalar("SELECT COUNT(*) FROM stream_suggestion WHERE user_id = $1")
            .bind(user_id)
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);

    let stream_pending: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM stream_suggestion WHERE user_id = $1 AND status = 'PENDING'",
    )
    .bind(user_id)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);

    let stream_approved: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM stream_suggestion WHERE user_id = $1 AND status IN ('approved', 'auto_approved')",
    )
    .bind(user_id)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);

    let stream_rejected: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM stream_suggestion WHERE user_id = $1 AND status = 'REJECTED'",
    )
    .bind(user_id)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);

    let contribution_types = [
        "metadata",
        "stream",
        "torrent",
        "telegram",
        "youtube",
        "nzb",
        "http",
        "acestream",
    ];
    let mut by_type = serde_json::Map::new();
    for ct in contribution_types {
        let cnt: i64 = sqlx::query_scalar(
            "SELECT COUNT(*) FROM contributions WHERE user_id = $1 AND contribution_type = $2",
        )
        .bind(user_id)
        .bind(ct)
        .fetch_one(&state.pool_ro)
        .await
        .unwrap_or(0);
        by_type.insert(ct.to_string(), json!(cnt));
    }
    by_type.insert("stream_suggestions".to_string(), json!(stream_total));

    Json(json!({
        "total_contributions": total + stream_total,
        "pending": pending + stream_pending,
        "approved": approved + stream_approved,
        "rejected": rejected + stream_rejected,
        "by_type": serde_json::Value::Object(by_type),
    }))
    .into_response()
}

/// GET /api/v1/contributions/contributors  (moderator)
#[allow(clippy::type_complexity)]
pub async fn list_contribution_contributors(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<ContributorsQuery>,
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

    let limit = params.limit.clamp(1, 200);

    let mut sql = String::from(
        r#"SELECT c.user_id, u.username,
                  COUNT(*) as total,
                  COUNT(*) FILTER (WHERE c.status = 'PENDING') as pending,
                  COUNT(*) FILTER (WHERE c.status = 'APPROVED') as approved,
                  COUNT(*) FILTER (WHERE c.status = 'REJECTED') as rejected
           FROM contributions c
           JOIN users u ON u.id = c.user_id
           WHERE c.user_id IS NOT NULL"#,
    );

    if let Some(ref ct) = params.contribution_type {
        sql.push_str(&format!(
            " AND c.contribution_type = '{}'",
            ct.replace('\'', "''")
        ));
    }
    if let Some(ref cs) = params.contribution_status {
        sql.push_str(&format!(" AND c.status = '{}'", cs.replace('\'', "''")));
    }
    if let Some(ref q) = params.query {
        let esc = q.replace('\'', "''");
        sql.push_str(&format!(
            " AND (u.username ILIKE '%{esc}%' OR c.user_id::text ILIKE '%{esc}%')"
        ));
    }

    sql.push_str(&format!(
        " GROUP BY c.user_id, u.username ORDER BY total DESC, u.username ASC LIMIT {limit}"
    ));

    let rows: Vec<(Option<i32>, Option<String>, i64, i64, i64, i64)> = sqlx::query_as(&sql)
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default();

    let contributors: Vec<serde_json::Value> = rows
        .into_iter()
        .map(|(uid, uname, total, pending, approved, rejected)| {
            let label = uname
                .clone()
                .unwrap_or_else(|| format!("User #{}", uid.unwrap_or(0)));
            json!({
                "key": format!("user:{}", uid.unwrap_or(0)),
                "label": label,
                "user_id": uid,
                "anonymous_display_name": null,
                "total": total,
                "pending": pending,
                "approved": approved,
                "rejected": rejected,
            })
        })
        .collect();

    Json(json!({"items": contributors})).into_response()
}

/// GET /api/v1/contributions/review/pending  (moderator)
pub async fn list_pending_contributions(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<PendingListQuery>,
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

    let mut count_sql = String::from("SELECT COUNT(*) FROM contributions WHERE status = 'PENDING'");
    let mut fetch_sql = String::from(
        r#"SELECT id, user_id, contribution_type, target_id, data::jsonb, status::text,
                  reviewed_by, reviewed_at, review_notes,
                  admin_review_requested, admin_review_requested_by,
                  admin_review_requested_at, admin_review_reason,
                  created_at, updated_at
           FROM contributions WHERE status = 'PENDING'"#,
    );

    if let Some(ref ct) = params.contribution_type {
        let esc = ct.replace('\'', "''");
        count_sql.push_str(&format!(" AND contribution_type = '{esc}'"));
        fetch_sql.push_str(&format!(" AND contribution_type = '{esc}'"));
    }

    fetch_sql.push_str(" ORDER BY created_at ASC LIMIT $1 OFFSET $2");

    let total: i64 = sqlx::query_scalar(&count_sql)
        .fetch_one(&state.pool_ro)
        .await
        .unwrap_or(0);

    type ContribTuple = (
        String,
        Option<i32>,
        String,
        Option<String>,
        serde_json::Value,
        String,
        Option<String>,
        Option<DateTime<Utc>>,
        Option<String>,
        bool,
        Option<String>,
        Option<DateTime<Utc>>,
        Option<String>,
        DateTime<Utc>,
        Option<DateTime<Utc>>,
    );
    let rows: Vec<ContribTuple> = sqlx::query_as::<_, ContribTuple>(&fetch_sql)
        .bind(page_size)
        .bind(offset)
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default();

    let mut items = Vec::new();
    for r in &rows {
        let row = ContribRow {
            id: r.0.clone(),
            user_id: r.1,
            contribution_type: r.2.clone(),
            target_id: r.3.clone(),
            data: r.4.clone(),
            status: r.5.clone(),
            reviewed_by: r.6.clone(),
            reviewed_at: r.7,
            review_notes: r.8.clone(),
            admin_review_requested: r.9,
            admin_review_requested_by: r.10.clone(),
            admin_review_requested_at: r.11,
            admin_review_reason: r.12.clone(),
            created_at: r.13,
            updated_at: r.14,
        };
        items.push(contrib_row_to_json(&state.pool_ro, &row).await);
    }

    let has_more = offset + page_size < total;
    Json(json!({
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": has_more,
    }))
    .into_response()
}

/// GET /api/v1/contributions/review/stats  (moderator)
pub async fn get_all_contribution_stats(
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
    if !is_mod_or_admin(&role) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "Moderator role required"})),
        )
            .into_response();
    }

    let total: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM contributions")
        .fetch_one(&state.pool_ro)
        .await
        .unwrap_or(0);
    let pending: i64 =
        sqlx::query_scalar("SELECT COUNT(*) FROM contributions WHERE status = 'PENDING'")
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);
    let approved: i64 =
        sqlx::query_scalar("SELECT COUNT(*) FROM contributions WHERE status = 'APPROVED'")
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);
    let rejected: i64 =
        sqlx::query_scalar("SELECT COUNT(*) FROM contributions WHERE status = 'REJECTED'")
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);

    let stream_total: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM stream_suggestion")
        .fetch_one(&state.pool_ro)
        .await
        .unwrap_or(0);
    let stream_pending: i64 =
        sqlx::query_scalar("SELECT COUNT(*) FROM stream_suggestion WHERE status = 'PENDING'")
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);
    let stream_approved: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM stream_suggestion WHERE status IN ('approved', 'auto_approved')",
    )
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);
    let stream_rejected: i64 =
        sqlx::query_scalar("SELECT COUNT(*) FROM stream_suggestion WHERE status = 'REJECTED'")
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);

    let contribution_types = [
        "metadata",
        "stream",
        "torrent",
        "telegram",
        "youtube",
        "nzb",
        "http",
        "acestream",
    ];
    let mut by_type = serde_json::Map::new();
    for ct in contribution_types {
        let cnt: i64 =
            sqlx::query_scalar("SELECT COUNT(*) FROM contributions WHERE contribution_type = $1")
                .bind(ct)
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0);
        by_type.insert(ct.to_string(), json!(cnt));
    }
    by_type.insert("stream_suggestions".to_string(), json!(stream_total));

    Json(json!({
        "total_contributions": total + stream_total,
        "pending": pending + stream_pending,
        "approved": approved + stream_approved,
        "rejected": rejected + stream_rejected,
        "by_type": serde_json::Value::Object(by_type),
    }))
    .into_response()
}

/// GET /api/v1/contributions/{contribution_id}
pub async fn get_contribution(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(contribution_id): Path<String>,
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

    let row = match fetch_contrib_row(&state.pool_ro, &contribution_id).await {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Contribution not found"})),
            )
                .into_response()
        }
        Some(r) => r,
    };

    let role = get_user_role(&state.pool_ro, user_id)
        .await
        .unwrap_or_default();
    if row.user_id != Some(user_id) && !is_mod_or_admin(&role) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "Not authorized"})),
        )
            .into_response();
    }

    Json(contrib_row_to_json(&state.pool_ro, &row).await).into_response()
}

/// POST /api/v1/contributions
pub async fn create_contribution(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<ContributionCreate>,
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
    let is_privileged = is_mod_or_admin(&role);
    let is_anonymous = body
        .data
        .get("is_anonymous")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);

    let auto_types = ["torrent", "stream"];
    let is_active: bool = sqlx::query_scalar("SELECT is_active FROM users WHERE id = $1")
        .bind(user_id)
        .fetch_optional(&state.pool)
        .await
        .unwrap_or(None)
        .unwrap_or(false);

    let should_auto_approve = is_privileged
        || (!is_anonymous && is_active && auto_types.contains(&body.contribution_type.as_str()));

    let initial_status = if should_auto_approve {
        "APPROVED"
    } else {
        "PENDING"
    };
    let reviewer_id = if should_auto_approve {
        Some("auto".to_string())
    } else {
        None
    };
    let review_notes = if is_privileged {
        Some("Auto-approved: Privileged reviewer".to_string())
    } else if should_auto_approve {
        Some("Auto-approved: Active user content import".to_string())
    } else {
        None
    };

    let contrib_id = Uuid::new_v4().to_string();
    let stored_user_id: Option<i32> = if is_anonymous { None } else { Some(user_id) };

    match sqlx::query(
        r#"INSERT INTO contributions (id, user_id, contribution_type, target_id, data, status,
                                    reviewed_by, reviewed_at, review_notes,
                                    admin_review_requested, created_at, updated_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7,
                   CASE WHEN $7 IS NOT NULL THEN NOW() ELSE NULL END,
                   $8, false, NOW(), NOW())"#,
    )
    .bind(&contrib_id)
    .bind(stored_user_id)
    .bind(&body.contribution_type)
    .bind(&body.target_id)
    .bind(&body.data)
    .bind(initial_status)
    .bind(&reviewer_id)
    .bind(&review_notes)
    .execute(&state.pool)
    .await
    {
        Ok(_) => {}
        Err(e) => {
            tracing::error!("create_contribution: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    }

    let row = match fetch_contrib_row(&state.pool, &contrib_id).await {
        None => return StatusCode::INTERNAL_SERVER_ERROR.into_response(),
        Some(r) => r,
    };

    (
        StatusCode::CREATED,
        Json(contrib_row_to_json(&state.pool, &row).await),
    )
        .into_response()
}

/// DELETE /api/v1/contributions/{contribution_id}
pub async fn delete_contribution(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(contribution_id): Path<String>,
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

    let row = match fetch_contrib_row(&state.pool, &contribution_id).await {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Contribution not found"})),
            )
                .into_response()
        }
        Some(r) => r,
    };

    let role = get_user_role(&state.pool, user_id)
        .await
        .unwrap_or_default();
    let is_owner = row.user_id == Some(user_id);
    let is_pending = row.status == "pending";

    if !(is_admin(&role) || is_owner && is_pending) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "Cannot delete this contribution. Only pending contributions can be deleted by their owner."})),
        )
            .into_response();
    }

    if let Err(e) = sqlx::query("DELETE FROM contributions WHERE id = $1")
        .bind(&contribution_id)
        .execute(&state.pool)
        .await
    {
        tracing::error!("delete_contribution: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    StatusCode::NO_CONTENT.into_response()
}

/// PATCH /api/v1/contributions/{contribution_id}/review  (moderator)
pub async fn review_contribution(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(contribution_id): Path<String>,
    Json(body): Json<ContributionReview>,
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

    let row = match fetch_contrib_row(&state.pool, &contribution_id).await {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Contribution not found"})),
            )
                .into_response()
        }
        Some(r) => r,
    };

    if row.status != "pending" {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": format!("Contribution already reviewed with status: {}", row.status)})),
        )
            .into_response();
    }

    let new_status = match body.status.as_str() {
        "APPROVED" | "approved" => "APPROVED",
        "REJECTED" | "rejected" => "REJECTED",
        _ => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "status must be approved or rejected"})),
            )
                .into_response()
        }
    };

    if let Err(e) = sqlx::query(
        "UPDATE contributions SET status = $1, reviewed_by = $2, reviewed_at = NOW(), review_notes = $3 WHERE id = $4",
    )
    .bind(new_status)
    .bind(user_id.to_string())
    .bind(&body.review_notes)
    .bind(&contribution_id)
    .execute(&state.pool)
    .await
    {
        tracing::error!("review_contribution: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    let updated = match fetch_contrib_row(&state.pool, &contribution_id).await {
        None => return StatusCode::INTERNAL_SERVER_ERROR.into_response(),
        Some(r) => r,
    };
    Json(contrib_row_to_json(&state.pool, &updated).await).into_response()
}

/// PATCH /api/v1/contributions/{contribution_id}/flag-admin-review  (moderator)
pub async fn flag_contribution_for_admin_review(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(contribution_id): Path<String>,
    Json(body): Json<AdminFlagRequest>,
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

    let row = match fetch_contrib_row(&state.pool, &contribution_id).await {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Contribution not found"})),
            )
                .into_response()
        }
        Some(r) => r,
    };

    if row.status != "approved" {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": format!("Only approved contributions can be flagged (current status: {})", row.status)})),
        )
            .into_response();
    }

    let reason = body.reason.as_ref().map(|r| r.trim().to_string());

    if let Err(e) = sqlx::query(
        r#"UPDATE contributions
           SET admin_review_requested = true,
               admin_review_requested_by = $1,
               admin_review_requested_at = NOW(),
               admin_review_reason = $2
           WHERE id = $3"#,
    )
    .bind(user_id.to_string())
    .bind(&reason)
    .bind(&contribution_id)
    .execute(&state.pool)
    .await
    {
        tracing::error!("flag_contribution_for_admin_review: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    let updated = match fetch_contrib_row(&state.pool, &contribution_id).await {
        None => return StatusCode::INTERNAL_SERVER_ERROR.into_response(),
        Some(r) => r,
    };
    Json(contrib_row_to_json(&state.pool, &updated).await).into_response()
}

/// PATCH /api/v1/contributions/{contribution_id}/reject-approved  (moderator)
pub async fn reject_approved_contribution(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(contribution_id): Path<String>,
    Json(body): Json<AdminRejectRequest>,
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

    let row = match fetch_contrib_row(&state.pool, &contribution_id).await {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Contribution not found"})),
            )
                .into_response()
        }
        Some(r) => r,
    };

    if row.status != "approved" {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": format!("Only approved contributions can be rejected (current status: {})", row.status)})),
        )
            .into_response();
    }

    let rollback_note = "Moderation rejection rollback: no linked stream could be resolved.";
    let mut notes = row.review_notes.clone().unwrap_or_default();
    if !notes.is_empty() {
        notes.push('\n');
    }
    notes.push_str(rollback_note);
    if let Some(ref extra) = body.review_notes {
        let trimmed = extra.trim();
        if !trimmed.is_empty() {
            notes.push('\n');
            notes.push_str(trimmed);
        }
    }

    if let Err(e) = sqlx::query(
        r#"UPDATE contributions
           SET status = 'REJECTED',
               reviewed_by = $1,
               reviewed_at = NOW(),
               admin_review_requested = false,
               review_notes = $2
           WHERE id = $3"#,
    )
    .bind(user_id.to_string())
    .bind(&notes)
    .bind(&contribution_id)
    .execute(&state.pool)
    .await
    {
        tracing::error!("reject_approved_contribution: {e}");
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }

    let updated = match fetch_contrib_row(&state.pool, &contribution_id).await {
        None => return StatusCode::INTERNAL_SERVER_ERROR.into_response(),
        Some(r) => r,
    };
    Json(contrib_row_to_json(&state.pool, &updated).await).into_response()
}

fn is_adult_contribution(
    data: &serde_json::Value,
    cache: &crate::state::KeywordFilterCache,
) -> bool {
    let check_text = |text: &str| -> bool {
        if text.is_empty() {
            return false;
        }
        let lower = text.to_lowercase();
        // whitelist check first
        if cache.whitelist.iter().any(|p| lower.contains(p.as_str())) {
            return false;
        }
        // keyword check
        cache.keywords.iter().any(|kw| lower.contains(kw.as_str()))
    };

    // Check top-level name and title fields (torrent_name, display name, resolved title)
    for key in &["name", "title"] {
        if let Some(text) = data.get(key).and_then(|v| v.as_str()) {
            if check_text(text) {
                return true;
            }
        }
    }

    // Check per-file fields inside file_data
    if let Some(files) = data.get("file_data").and_then(|v| v.as_array()) {
        for file in files {
            for key in &["filename", "meta_title", "episode_title", "title"] {
                if let Some(text) = file.get(key).and_then(|v| v.as_str()) {
                    if check_text(text) {
                        return true;
                    }
                }
            }
        }
    }

    false
}

/// POST /api/v1/contributions/review/bulk  (moderator)
pub async fn bulk_review_contributions(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<BulkReviewRequest>,
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
        "approve" => "APPROVED",
        "reject" => "REJECTED",
        _ => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "action must be approve or reject"})),
            )
                .into_response()
        }
    };

    let (fetch_sql, rows): (String, Vec<(String, serde_json::Value)>) = if let Some(ref ct) =
        body.contribution_type
    {
        let sql = String::from(
                "SELECT id, data::jsonb FROM contributions WHERE status = 'PENDING' AND contribution_type = $1 ORDER BY created_at ASC",
            );
        let r = sqlx::query_as::<_, (String, serde_json::Value)>(&sql)
            .bind(ct)
            .fetch_all(&state.pool)
            .await
            .unwrap_or_default();
        (sql, r)
    } else {
        let sql = String::from(
                "SELECT id, data::jsonb FROM contributions WHERE status = 'PENDING' ORDER BY created_at ASC",
            );
        let r = sqlx::query_as::<_, (String, serde_json::Value)>(&sql)
            .fetch_all(&state.pool)
            .await
            .unwrap_or_default();
        (sql, r)
    };
    let _ = fetch_sql;

    let mut approved = 0i64;
    let mut rejected = 0i64;
    let mut skipped = 0i64;

    for (id, data) in rows {
        if let Some(ref allowed_ids) = body.contribution_ids {
            if !allowed_ids.contains(&id) {
                skipped += 1;
                continue;
            }
        }

        // When approving, skip adult content
        if new_status == "APPROVED" {
            let cache = state
                .keyword_filters
                .read()
                .unwrap_or_else(|e| e.into_inner());
            let is_adult = is_adult_contribution(&data, &cache);
            drop(cache);
            if is_adult {
                skipped += 1;
                continue;
            }
        }

        let result = sqlx::query(
            "UPDATE contributions SET status = $1, reviewed_by = $2, reviewed_at = NOW(), review_notes = $3 WHERE id = $4 AND status = 'PENDING'",
        )
        .bind(new_status)
        .bind(user_id.to_string())
        .bind(&body.review_notes)
        .bind(&id)
        .execute(&state.pool)
        .await;

        match result {
            Ok(r) if r.rows_affected() > 0 => {
                if new_status == "APPROVED" {
                    approved += 1;
                } else {
                    rejected += 1;
                }
            }
            _ => {
                skipped += 1;
            }
        }
    }

    Json(json!({
        "approved": approved,
        "rejected": rejected,
        "skipped": skipped,
    }))
    .into_response()
}
