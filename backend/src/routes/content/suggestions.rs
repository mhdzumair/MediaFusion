/// Metadata correction suggestion endpoints.
///
/// Routes (prefix /api/v1):
///   POST   /metadata/{media_id}/suggest    → create_suggestion
///   GET    /suggestions                    → list_my_suggestions
///   GET    /contributions/me               → get_my_contribution_info
///   GET    /suggestions/pending            → list_pending_suggestions   (moderator)
///   POST   /suggestions/bulk-review        → bulk_review_suggestions    (moderator)
///   GET    /suggestions/stats              → get_suggestion_stats
///   GET    /suggestions/{suggestion_id}    → get_suggestion
///   DELETE /suggestions/{suggestion_id}    → delete_suggestion
///   PUT    /suggestions/{suggestion_id}/review → review_suggestion      (moderator)
use std::sync::Arc;

use axum::{
    Json,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use chrono::Utc;
use hmac::{Hmac, KeyInit, Mac};
use serde::Deserialize;
use serde_json::json;
use sha2::Sha256;

use crate::{db::contribution_defaults, state::AppState};

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

// ─── Request/Response shapes ──────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct SuggestionCreateRequest {
    pub field_name: String,
    pub current_value: Option<String>,
    pub suggested_value: String,
    pub reason: Option<String>,
}

#[derive(Deserialize)]
pub struct SuggestionListQuery {
    pub status: Option<String>,
    #[serde(default = "default_page")]
    pub page: i64,
    #[serde(default = "default_page_size")]
    pub page_size: i64,
}

#[derive(Deserialize)]
pub struct PendingListQuery {
    pub field_name: Option<String>,
    pub status: Option<String>,
    pub uploader_query: Option<String>,
    pub reviewer_query: Option<String>,
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

#[derive(Deserialize)]
pub struct SuggestionReviewRequest {
    pub action: String,
    pub review_notes: Option<String>,
}

fn default_page() -> i64 {
    1
}
fn default_page_size() -> i64 {
    20
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

/// Check if a user is a moderator or admin (role stored as TEXT in DB).
async fn is_moderator(pool: &sqlx::PgPool, user_id: i32) -> bool {
    crate::db::get_user_role(pool, user_id)
        .await
        .is_some_and(crate::db::is_mod_or_admin)
}

async fn get_username(pool: &sqlx::PgPool, user_id: i32) -> Option<String> {
    sqlx::query_scalar("SELECT username FROM users WHERE id = $1")
        .bind(user_id)
        .fetch_optional(pool)
        .await
        .unwrap_or(None)
}

#[allow(clippy::type_complexity)]
async fn build_suggestion_json(
    pool: &sqlx::PgPool,
    row: &(
        String,
        i32,
        i32,
        String,
        Option<String>,
        String,
        Option<String>,
        String,
        Option<String>,
        Option<String>,
        Option<String>,
        chrono::DateTime<Utc>,
        Option<chrono::DateTime<Utc>>,
    ),
) -> serde_json::Value {
    let (
        id,
        user_id,
        media_id,
        field_name,
        current_value,
        suggested_value,
        reason,
        status,
        reviewed_by,
        review_notes,
        _updated_at,
        created_at,
        reviewed_at,
    ) = row;

    let username = get_username(pool, *user_id).await;
    let reviewer_name = if let Some(rb) = reviewed_by.as_deref() {
        if let Ok(rid) = rb.parse::<i32>() {
            get_username(pool, rid).await
        } else {
            None
        }
    } else {
        None
    };

    // Media context
    let media_info: Option<(String, crate::db::MediaType, Option<i32>)> =
        sqlx::query_as("SELECT title, type, year FROM media WHERE id = $1")
            .bind(media_id)
            .fetch_optional(pool)
            .await
            .unwrap_or(None);

    let (media_title, media_type, media_year) = media_info
        .map(|(t, mt, y)| (Some(t), Some(mt.as_wire().to_string()), y))
        .unwrap_or((None, None, None));

    // User contribution info
    let contrib: Option<(i32, String)> =
        sqlx::query_as("SELECT contribution_points, contribution_level FROM users WHERE id = $1")
            .bind(user_id)
            .fetch_optional(pool)
            .await
            .unwrap_or(None);

    json!({
        "id": id,
        "user_id": user_id,
        "username": username,
        "media_id": media_id,
        "media_title": media_title,
        "media_type": media_type,
        "media_year": media_year,
        "field_name": field_name,
        "current_value": current_value,
        "suggested_value": suggested_value,
        "reason": reason,
        "status": status,
        "was_auto_approved": status == "auto_approved",
        "reviewed_by": reviewer_name,
        "reviewed_at": reviewed_at.map(|d| d.to_rfc3339()),
        "review_notes": review_notes,
        "created_at": created_at.to_rfc3339(),
        "user_contribution_level": contrib.as_ref().map(|(_, l)| l.as_str()),
        "user_contribution_points": contrib.as_ref().map(|(p, _)| *p),
    })
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

fn parse_comma_list(value: &str) -> Vec<String> {
    value
        .split(',')
        .map(str::trim)
        .filter(|part| !part.is_empty())
        .map(str::to_string)
        .collect()
}

/// Apply an approved metadata suggestion directly to the media record.
async fn apply_metadata_field_change(
    pool: &sqlx::PgPool,
    media_id: i32,
    field_name: &str,
    suggested_value: &str,
) {
    match field_name {
        "title" => {
            if let Err(e) =
                sqlx::query("UPDATE media SET title = $1, updated_at = NOW() WHERE id = $2")
                    .bind(suggested_value)
                    .bind(media_id)
                    .execute(pool)
                    .await
            {
                tracing::warn!("apply_metadata_field_change: title update failed: {e}");
            }
        }
        "description" => {
            if let Err(e) =
                sqlx::query("UPDATE media SET description = $1, updated_at = NOW() WHERE id = $2")
                    .bind(suggested_value)
                    .bind(media_id)
                    .execute(pool)
                    .await
            {
                tracing::warn!("apply_metadata_field_change: description update failed: {e}");
            }
        }
        "year" => {
            if let Ok(year) = suggested_value.parse::<i32>()
                && let Err(e) =
                    sqlx::query("UPDATE media SET year = $1, updated_at = NOW() WHERE id = $2")
                        .bind(year)
                        .bind(media_id)
                        .execute(pool)
                        .await
            {
                tracing::warn!("apply_metadata_field_change: year update failed: {e}");
            }
        }
        "runtime" => {
            if let Ok(minutes) = suggested_value.parse::<i32>()
                && let Err(e) = sqlx::query(
                    "UPDATE media SET runtime_minutes = $1, updated_at = NOW() WHERE id = $2",
                )
                .bind(minutes)
                .bind(media_id)
                .execute(pool)
                .await
            {
                tracing::warn!("apply_metadata_field_change: runtime update failed: {e}");
            }
        }
        "nudity_status" => {
            const VALID_NUDITY: &[&str] =
                &["NONE", "MILD", "MODERATE", "SEVERE", "UNKNOWN", "DISABLE"];
            let nudity_val = suggested_value.to_ascii_uppercase();
            if !VALID_NUDITY.contains(&nudity_val.as_str()) {
                tracing::warn!(
                    "apply_metadata_field_change: invalid nudity_status value: {suggested_value}"
                );
            } else {
                if let Err(e) = sqlx::query(
                    "UPDATE media SET nudity_status = $1, updated_at = NOW() WHERE id = $2",
                )
                .bind(
                    crate::db::NudityStatus::from_wire(&nudity_val)
                        .unwrap_or(crate::db::NudityStatus::Unknown),
                )
                .bind(media_id)
                .execute(pool)
                .await
                {
                    tracing::warn!("apply_metadata_field_change: nudity_status update failed: {e}");
                }
            }
        }
        "is_add_title_to_poster" => {
            let val = matches!(
                suggested_value.to_ascii_lowercase().as_str(),
                "true" | "1" | "yes"
            );
            if let Err(e) = sqlx::query(
                "UPDATE media SET is_add_title_to_poster = $1, updated_at = NOW() WHERE id = $2",
            )
            .bind(val)
            .bind(media_id)
            .execute(pool)
            .await
            {
                tracing::warn!(
                    "apply_metadata_field_change: is_add_title_to_poster update failed: {e}"
                );
            }
        }
        "imdb_id" | "tmdb_id" | "tvdb_id" | "mal_id" | "kitsu_id" => {
            let provider = field_name.trim_end_matches("_id");
            crate::db::set_external_id_for_media(pool, media_id, provider, suggested_value).await;
        }
        "catalogs" => {
            let catalogs = parse_comma_list(suggested_value);
            crate::db::replace_catalogs_for_media(pool, media_id, &catalogs).await;
        }
        "poster" => {
            crate::db::replace_primary_image_for_media(pool, media_id, "poster", suggested_value)
                .await;
        }
        "background" => {
            crate::db::replace_primary_image_for_media(
                pool,
                media_id,
                "background",
                suggested_value,
            )
            .await;
        }
        "genres" => {
            let genres = parse_comma_list(suggested_value);
            crate::db::replace_genres_for_media(pool, media_id, &genres).await;
        }
        "country" => {
            crate::db::update_tv_country_for_media(pool, media_id, suggested_value).await;
        }
        "language" => {
            crate::db::update_tv_language_for_media(pool, media_id, suggested_value).await;
        }
        "aka_titles" => {
            let titles = parse_comma_list(suggested_value);
            crate::db::replace_aka_titles_for_media(pool, media_id, &titles).await;
        }
        "cast" => {
            let names = parse_comma_list(suggested_value);
            crate::db::replace_cast_for_media(pool, media_id, &names).await;
        }
        "directors" => {
            let names = parse_comma_list(suggested_value);
            crate::db::replace_directors_for_media(pool, media_id, &names).await;
        }
        "writers" => {
            let names = parse_comma_list(suggested_value);
            crate::db::replace_writers_for_media(pool, media_id, &names).await;
        }
        "parental_certificate" => {
            let certificates = crate::db::expand_parental_certificate_selection(suggested_value);
            crate::db::replace_parental_certificates_for_media(pool, media_id, &certificates).await;
        }
        _ => {
            tracing::debug!(
                "apply_metadata_field_change: no direct DB mapping for field {field_name}, skipping"
            );
        }
    }
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// POST /api/v1/metadata/{media_id}/suggest
pub async fn create_suggestion(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i32>,
    Json(body): Json<SuggestionCreateRequest>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    // Validate field_name
    const EDITABLE_FIELDS: &[&str] = &[
        "title",
        "description",
        "year",
        "poster",
        "background",
        "runtime",
        "genres",
        "country",
        "language",
        "aka_titles",
        "cast",
        "directors",
        "writers",
        "imdb_id",
        "tmdb_id",
        "tvdb_id",
        "mal_id",
        "kitsu_id",
        "catalogs",
        "parental_certificate",
        "nudity_status",
        "is_add_title_to_poster",
    ];
    if !EDITABLE_FIELDS.contains(&body.field_name.as_str()) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": format!("Invalid field_name: {}", body.field_name)})),
        )
            .into_response();
    }

    const ARRAY_FIELDS: &[&str] = &[
        "genres",
        "cast",
        "directors",
        "writers",
        "catalogs",
        "aka_titles",
    ];
    if body.suggested_value.is_empty() && !ARRAY_FIELDS.contains(&body.field_name.as_str()) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "suggested_value must not be empty"})),
        )
            .into_response();
    }

    // Check media exists
    let media_exists: bool = sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM media WHERE id = $1)")
        .bind(media_id)
        .fetch_one(&state.pool)
        .await
        .unwrap_or(false);

    if !media_exists {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Metadata not found"})),
        )
            .into_response();
    }

    // Check pending limit (default 10)
    let pending_count: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM metadata_suggestions WHERE user_id = $1 AND status = 'pending'",
    )
    .bind(user_id)
    .fetch_one(&state.pool)
    .await
    .unwrap_or(0);

    if pending_count >= 10 {
        return (
            StatusCode::TOO_MANY_REQUESTS,
            Json(json!({"detail": "You have reached the maximum number of pending suggestions (10)"})),
        ).into_response();
    }

    // Check for duplicate pending suggestion
    let existing: Option<String> = sqlx::query_scalar(
        r#"SELECT id::text FROM metadata_suggestions
           WHERE user_id = $1 AND media_id = $2 AND field_name = $3 AND status = 'pending'
           LIMIT 1"#,
    )
    .bind(user_id)
    .bind(media_id)
    .bind(&body.field_name)
    .fetch_optional(&state.pool)
    .await
    .unwrap_or(None);

    if existing.is_some() {
        return (
            StatusCode::CONFLICT,
            Json(json!({"detail": "You already have a pending suggestion for this field"})),
        )
            .into_response();
    }

    // Check auto-approval: moderators/admins auto-approve
    let suggestion_status = if is_moderator(&state.pool, user_id).await {
        "auto_approved"
    } else {
        "pending"
    };

    let suggestion_id: String = sqlx::query_scalar(
        r#"INSERT INTO metadata_suggestions
               (user_id, media_id, field_name, current_value, suggested_value, reason, status, created_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
           RETURNING id::text"#,
    )
    .bind(user_id)
    .bind(media_id)
    .bind(&body.field_name)
    .bind(&body.current_value)
    .bind(&body.suggested_value)
    .bind(&body.reason)
    .bind(suggestion_status)
    .fetch_one(&state.pool)
    .await
    .unwrap_or_default();

    if suggestion_status == "auto_approved" {
        apply_metadata_field_change(
            &state.pool,
            media_id,
            &body.field_name,
            &body.suggested_value,
        )
        .await;
    }

    let username = get_username(&state.pool, user_id).await;
    let contrib: Option<(i32, String)> =
        sqlx::query_as("SELECT contribution_points, contribution_level FROM users WHERE id = $1")
            .bind(user_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);

    (
        StatusCode::CREATED,
        Json(json!({
            "id": suggestion_id,
            "user_id": user_id,
            "username": username,
            "media_id": media_id,
            "field_name": body.field_name,
            "current_value": body.current_value,
            "suggested_value": body.suggested_value,
            "reason": body.reason,
            "status": suggestion_status,
            "was_auto_approved": suggestion_status == "auto_approved",
            "reviewed_by": null,
            "reviewed_at": null,
            "review_notes": null,
            "created_at": Utc::now().to_rfc3339(),
            "user_contribution_level": contrib.as_ref().map(|(_, l)| l.as_str()),
            "user_contribution_points": contrib.as_ref().map(|(p, _)| *p),
        })),
    )
        .into_response()
}

/// GET /api/v1/suggestions
#[allow(clippy::type_complexity)]
pub async fn list_my_suggestions(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Query(params): Query<SuggestionListQuery>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    let page = params.page.max(1);
    let page_size = params.page_size.clamp(1, 100);
    let offset = (page - 1) * page_size;

    let (total, rows) = if let Some(ref st) = params.status {
        let total: i64 = sqlx::query_scalar(
            "SELECT COUNT(*) FROM metadata_suggestions WHERE user_id = $1 AND status = $2",
        )
        .bind(user_id)
        .bind(st)
        .fetch_one(&state.pool_ro)
        .await
        .unwrap_or(0);

        let rows: Vec<(
            String,
            i32,
            i32,
            String,
            Option<String>,
            String,
            Option<String>,
            String,
            Option<String>,
            Option<String>,
            Option<String>,
            chrono::DateTime<Utc>,
            Option<chrono::DateTime<Utc>>,
        )> = sqlx::query_as(
            r#"SELECT id::text, user_id, media_id, field_name, current_value, suggested_value,
                          reason, status, reviewed_by, review_notes, NULL::text,
                          created_at, reviewed_at
                   FROM metadata_suggestions
                   WHERE user_id = $1 AND status = $2
                   ORDER BY CASE WHEN status = 'pending' THEN 0 ELSE 1 END, created_at DESC
                   LIMIT $3 OFFSET $4"#,
        )
        .bind(user_id)
        .bind(st)
        .bind(page_size)
        .bind(offset)
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default();

        (total, rows)
    } else {
        let total: i64 =
            sqlx::query_scalar("SELECT COUNT(*) FROM metadata_suggestions WHERE user_id = $1")
                .bind(user_id)
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0);

        let rows: Vec<(
            String,
            i32,
            i32,
            String,
            Option<String>,
            String,
            Option<String>,
            String,
            Option<String>,
            Option<String>,
            Option<String>,
            chrono::DateTime<Utc>,
            Option<chrono::DateTime<Utc>>,
        )> = sqlx::query_as(
            r#"SELECT id::text, user_id, media_id, field_name, current_value, suggested_value,
                          reason, status, reviewed_by, review_notes, NULL::text,
                          created_at, reviewed_at
                   FROM metadata_suggestions
                   WHERE user_id = $1
                   ORDER BY CASE WHEN status = 'pending' THEN 0 ELSE 1 END, created_at DESC
                   LIMIT $2 OFFSET $3"#,
        )
        .bind(user_id)
        .bind(page_size)
        .bind(offset)
        .fetch_all(&state.pool_ro)
        .await
        .unwrap_or_default();

        (total, rows)
    };

    let mut suggestions = Vec::with_capacity(rows.len());
    for row in &rows {
        suggestions.push(build_suggestion_json(&state.pool_ro, row).await);
    }

    Json(json!({
        "suggestions": suggestions,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": (offset + page_size) < total,
    }))
    .into_response()
}

/// GET /api/v1/contributions/me
pub async fn get_my_contribution_info(
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
                .into_response();
        }
    };

    let row: Option<(i32, String, i32, i32)> = sqlx::query_as(
        "SELECT contribution_points, contribution_level, metadata_edits_approved, stream_edits_approved FROM users WHERE id = $1",
    )
    .bind(user_id)
    .fetch_optional(&state.pool_ro)
    .await
    .unwrap_or(None);

    let (points, level, meta_approved, stream_approved) =
        row.unwrap_or((0, "new".to_string(), 0, 0));

    let is_mod = is_moderator(&state.pool_ro, user_id).await;

    let thresholds: Option<(i32, i32, i32, i32)> = sqlx::query_as(
        "SELECT contributor_threshold, trusted_threshold, expert_threshold, auto_approval_threshold FROM contribution_settings WHERE id = 'default'",
    )
    .fetch_optional(&state.pool_ro)
    .await
    .unwrap_or(None);

    let (contributor_t, trusted_t, expert_t, auto_approval_t) = thresholds.unwrap_or((
        contribution_defaults::CONTRIBUTOR_THRESHOLD as i32,
        contribution_defaults::TRUSTED_THRESHOLD as i32,
        contribution_defaults::EXPERT_THRESHOLD as i32,
        contribution_defaults::AUTO_APPROVAL_THRESHOLD,
    ));

    // Calculate points to next level
    let (next_level, points_to_next) = match level.as_str() {
        "new" => (Some("contributor"), (contributor_t - points).max(0)),
        "contributor" => (Some("trusted"), (trusted_t - points).max(0)),
        "trusted" => (Some("expert"), (expert_t - points).max(0)),
        _ => (None, 0),
    };

    Json(json!({
        "contribution_points": points,
        "contribution_level": level,
        "metadata_edits_approved": meta_approved,
        "stream_edits_approved": stream_approved,
        "can_auto_approve": is_mod || points >= auto_approval_t,
        "points_to_next_level": points_to_next,
        "next_level": next_level,
    }))
    .into_response()
}

/// GET /api/v1/suggestions/pending  (moderator)
#[allow(clippy::type_complexity)]
pub async fn list_pending_suggestions(
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
                .into_response();
        }
    };

    if !is_moderator(&state.pool_ro, user_id).await {
        return (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response();
    }

    let page = params.page.max(1);
    let page_size = params.page_size.clamp(1, 100);
    let offset = (page - 1) * page_size;

    // Default to pending if no status given
    let status_filter = params.status.as_deref().unwrap_or("pending");

    type SuggestionRow = (
        String,
        i32,
        i32,
        String,
        Option<String>,
        String,
        Option<String>,
        String,
        Option<String>,
        Option<String>,
        Option<String>,
        chrono::DateTime<Utc>,
        Option<chrono::DateTime<Utc>>,
    );

    let mut count_sql = String::from("SELECT COUNT(*) FROM metadata_suggestions WHERE 1=1");
    let mut list_sql = String::from(
        "SELECT id::text, user_id, media_id, field_name, current_value, suggested_value, reason, status, reviewed_by, review_notes, NULL::text, created_at, reviewed_at FROM metadata_suggestions WHERE 1=1",
    );
    let mut extra_binds: Vec<String> = Vec::new();
    let mut next_idx = 1i32;

    if status_filter != "all" {
        count_sql.push_str(&format!(" AND status = ${next_idx}"));
        list_sql.push_str(&format!(" AND status = ${next_idx}"));
        extra_binds.push(status_filter.to_string());
        next_idx += 1;
    }

    if let Some(ref fn_filter) = params.field_name {
        count_sql.push_str(&format!(" AND field_name = ${next_idx}"));
        list_sql.push_str(&format!(" AND field_name = ${next_idx}"));
        extra_binds.push(fn_filter.clone());
        next_idx += 1;
    }

    let (user_filters, user_binds) = super::suggestion_query_filters::build_suggestion_user_filters(
        &mut next_idx,
        params.uploader_query.as_deref(),
        params.reviewer_query.as_deref(),
    );
    count_sql.push_str(&user_filters);
    list_sql.push_str(&user_filters);
    extra_binds.extend(user_binds);

    list_sql.push_str(super::suggestion_query_filters::PENDING_FIRST_ORDER);
    list_sql.push_str(&format!(" LIMIT ${next_idx} OFFSET ${}", next_idx + 1));

    let mut cq = sqlx::query_scalar::<_, i64>(sqlx::AssertSqlSafe(count_sql.as_str()));
    for v in &extra_binds {
        cq = cq.bind(v.clone());
    }
    let total: i64 = cq.fetch_one(&state.pool_ro).await.unwrap_or(0);

    let mut fq = sqlx::query_as::<_, SuggestionRow>(sqlx::AssertSqlSafe(list_sql.as_str()));
    for v in &extra_binds {
        fq = fq.bind(v.clone());
    }
    fq = fq.bind(page_size).bind(offset);
    let rows: Vec<SuggestionRow> = fq.fetch_all(&state.pool_ro).await.unwrap_or_default();

    let mut suggestions = Vec::with_capacity(rows.len());
    for row in &rows {
        suggestions.push(build_suggestion_json(&state.pool_ro, row).await);
    }

    Json(json!({
        "suggestions": suggestions,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": (offset + page_size) < total,
    }))
    .into_response()
}

/// POST /api/v1/suggestions/bulk-review  (moderator)
pub async fn bulk_review_suggestions(
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
                .into_response();
        }
    };

    if !is_moderator(&state.pool, user_id).await {
        return (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response();
    }

    let action = params.action.as_str();
    let new_status = match action {
        "approve" => "approved",
        "reject" => "rejected",
        _ => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "action must be approve or reject"})),
            )
                .into_response();
        }
    };

    let mut approved = 0i32;
    let mut rejected = 0i32;
    let mut skipped = 0i32;

    for sid in &suggestion_ids {
        let current_status: Option<String> =
            sqlx::query_scalar("SELECT status FROM metadata_suggestions WHERE id = $1")
                .bind(sid)
                .fetch_optional(&state.pool)
                .await
                .unwrap_or(None);

        match current_status.as_deref() {
            Some("pending") => {}
            _ => {
                skipped += 1;
                continue;
            }
        }

        let result = sqlx::query(
            r#"UPDATE metadata_suggestions
               SET status = $1, reviewed_by = $2::text, reviewed_at = NOW(), review_notes = $3, updated_at = NOW()
               WHERE id = $4 AND status = 'pending'"#,
        )
        .bind(new_status)
        .bind(user_id.to_string())
        .bind(params.review_notes.as_deref())
        .bind(sid)
        .execute(&state.pool)
        .await;

        match result {
            Ok(r) if r.rows_affected() > 0 => {
                if new_status == "approved" {
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

    Json(json!({"approved": approved, "rejected": rejected, "skipped": skipped})).into_response()
}

/// GET /api/v1/suggestions/stats
pub async fn get_suggestion_stats(
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
                .into_response();
        }
    };

    let is_mod = is_moderator(&state.pool_ro, user_id).await;

    let (total, pending, approved, auto_approved, rejected, approved_today, rejected_today) =
        if is_mod {
            let total: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM metadata_suggestions")
                .fetch_one(&state.pool_ro)
                .await
                .unwrap_or(0);
            let pending: i64 = sqlx::query_scalar(
                "SELECT COUNT(*) FROM metadata_suggestions WHERE status = 'pending'",
            )
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);
            let approved: i64 = sqlx::query_scalar(
                "SELECT COUNT(*) FROM metadata_suggestions WHERE status = 'approved'",
            )
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);
            let auto_approved: i64 = sqlx::query_scalar(
                "SELECT COUNT(*) FROM metadata_suggestions WHERE status = 'auto_approved'",
            )
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);
            let rejected: i64 = sqlx::query_scalar(
                "SELECT COUNT(*) FROM metadata_suggestions WHERE status = 'rejected'",
            )
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(0);
            let approved_today: i64 = sqlx::query_scalar(
            "SELECT COUNT(*) FROM metadata_suggestions WHERE status IN ('approved','auto_approved') AND reviewed_at >= NOW()::date",
        ).fetch_one(&state.pool_ro).await.unwrap_or(0);
            let rejected_today: i64 = sqlx::query_scalar(
            "SELECT COUNT(*) FROM metadata_suggestions WHERE status = 'rejected' AND reviewed_at >= NOW()::date",
        ).fetch_one(&state.pool_ro).await.unwrap_or(0);
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
            (0i64, 0i64, 0i64, 0i64, 0i64, 0i64, 0i64)
        };

    let user_pending: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM metadata_suggestions WHERE user_id = $1 AND status = 'pending'",
    )
    .bind(user_id)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);
    let user_approved: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM metadata_suggestions WHERE user_id = $1 AND status = 'approved'",
    )
    .bind(user_id)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);
    let user_auto_approved: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM metadata_suggestions WHERE user_id = $1 AND status = 'auto_approved'",
    )
    .bind(user_id)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);
    let user_rejected: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM metadata_suggestions WHERE user_id = $1 AND status = 'rejected'",
    )
    .bind(user_id)
    .fetch_one(&state.pool_ro)
    .await
    .unwrap_or(0);

    let contrib: Option<(i32, String)> =
        sqlx::query_as("SELECT contribution_points, contribution_level FROM users WHERE id = $1")
            .bind(user_id)
            .fetch_optional(&state.pool_ro)
            .await
            .unwrap_or(None);

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
        "user_contribution_points": contrib.as_ref().map(|(p,_)| *p).unwrap_or(0),
        "user_contribution_level": contrib.as_ref().map(|(_,l)| l.as_str()).unwrap_or("new"),
    }))
    .into_response()
}

/// GET /api/v1/suggestions/{suggestion_id}
#[allow(clippy::type_complexity)]
pub async fn get_suggestion(
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
                .into_response();
        }
    };

    let row: Option<(
        String,
        i32,
        i32,
        String,
        Option<String>,
        String,
        Option<String>,
        String,
        Option<String>,
        Option<String>,
        Option<String>,
        chrono::DateTime<Utc>,
        Option<chrono::DateTime<Utc>>,
    )> = sqlx::query_as(
        r#"SELECT id::text, user_id, media_id, field_name, current_value, suggested_value,
                      reason, status, reviewed_by, review_notes, NULL::text, created_at, reviewed_at
               FROM metadata_suggestions WHERE id = $1"#,
    )
    .bind(&suggestion_id)
    .fetch_optional(&state.pool_ro)
    .await
    .unwrap_or(None);

    let row = match row {
        Some(r) => r,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Suggestion not found"})),
            )
                .into_response();
        }
    };

    let suggestion_user_id = row.1;
    let is_mod = is_moderator(&state.pool_ro, user_id).await;
    if suggestion_user_id != user_id && !is_mod {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"detail": "Access denied"})),
        )
            .into_response();
    }

    Json(build_suggestion_json(&state.pool_ro, &row).await).into_response()
}

/// DELETE /api/v1/suggestions/{suggestion_id}
pub async fn delete_suggestion(
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
                .into_response();
        }
    };

    let row: Option<(String,)> =
        sqlx::query_as("SELECT status FROM metadata_suggestions WHERE id = $1 AND user_id = $2")
            .bind(&suggestion_id)
            .bind(user_id)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);

    match row {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Suggestion not found"})),
            )
                .into_response();
        }
        Some((ref st,)) if st != "pending" => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Can only delete pending suggestions"})),
            )
                .into_response();
        }
        _ => {}
    }

    sqlx::query("DELETE FROM metadata_suggestions WHERE id = $1 AND user_id = $2")
        .bind(&suggestion_id)
        .bind(user_id)
        .execute(&state.pool)
        .await
        .ok();

    StatusCode::NO_CONTENT.into_response()
}

/// PUT /api/v1/suggestions/{suggestion_id}/review  (moderator)
#[allow(clippy::type_complexity)]
pub async fn review_suggestion(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(suggestion_id): Path<String>,
    Json(body): Json<SuggestionReviewRequest>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    };

    if !is_moderator(&state.pool, user_id).await {
        return (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response();
    }

    let new_status = match body.action.as_str() {
        "approve" => "approved",
        "reject" => "rejected",
        _ => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "action must be approve or reject"})),
            )
                .into_response();
        }
    };

    let row: Option<(String, i32, String, String)> = sqlx::query_as(
        "SELECT status, media_id, field_name, suggested_value FROM metadata_suggestions WHERE id = $1",
    )
    .bind(&suggestion_id)
    .fetch_optional(&state.pool)
    .await
    .unwrap_or(None);

    let (suggestion_media_id, suggestion_field, suggestion_value) = match row {
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Suggestion not found"})),
            )
                .into_response();
        }
        Some((ref st, _, _, _)) if st != "pending" => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "Suggestion has already been reviewed"})),
            )
                .into_response();
        }
        Some((_, mid, field, val)) => (mid, field, val),
    };

    sqlx::query(
        r#"UPDATE metadata_suggestions
           SET status = $1, reviewed_by = $2::text, reviewed_at = NOW(), review_notes = $3, updated_at = NOW()
           WHERE id = $4"#,
    )
    .bind(new_status)
    .bind(user_id.to_string())
    .bind(body.review_notes.as_deref())
    .bind(&suggestion_id)
    .execute(&state.pool)
    .await
    .ok();

    if new_status == "approved" {
        apply_metadata_field_change(
            &state.pool,
            suggestion_media_id,
            &suggestion_field,
            &suggestion_value,
        )
        .await;
    }

    let updated_row: Option<(
        String,
        i32,
        i32,
        String,
        Option<String>,
        String,
        Option<String>,
        String,
        Option<String>,
        Option<String>,
        Option<String>,
        chrono::DateTime<Utc>,
        Option<chrono::DateTime<Utc>>,
    )> = sqlx::query_as(
        r#"SELECT id::text, user_id, media_id, field_name, current_value, suggested_value,
                      reason, status, reviewed_by, review_notes, NULL::text, created_at, reviewed_at
               FROM metadata_suggestions WHERE id = $1"#,
    )
    .bind(&suggestion_id)
    .fetch_optional(&state.pool)
    .await
    .unwrap_or(None);

    match updated_row {
        Some(row) => Json(build_suggestion_json(&state.pool, &row).await).into_response(),
        None => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    }
}
