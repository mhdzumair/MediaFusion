/// Admin user-management endpoints.
///
/// All endpoints require a valid JWT token with role == "admin".
///
/// Routes (prefix /api/v1/users):
///   GET    /                  → list_users
///   GET    /{user_id}         → get_user
///   PATCH  /{user_id}         → update_user
///   PATCH  /{user_id}/role    → update_user_role
///   DELETE /{user_id}         → delete_user
///   POST   /{user_id}/send-upload-warning → send_upload_warning
use std::sync::Arc;

use axum::{
    Json,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use crate::state::AppState;

use super::auth_guard;

async fn admin_auth(
    headers: &HeaderMap,
    pool: &sqlx::PgPool,
    secret: &str,
) -> Result<i32, Response> {
    auth_guard::require_active_role(pool, headers, secret, &["admin"])
        .await
        .map_err(|failure| auth_guard::auth_failure_response(failure).into_response())
}

// ─── Row / Response types ─────────────────────────────────────────────────────

/// Columns from the `users` table used by all responses.
/// (id, uuid, email, username, role, is_verified, is_active, created_at,
///  last_login, contribution_points, contribution_level, uploads_restricted)
type UserRow = (
    i32, // id is integer (INT4) in DB
    String,
    String,
    Option<String>,
    crate::db::UserRole,
    bool,
    bool,
    DateTime<Utc>,
    Option<DateTime<Utc>>,
    i32, // contribution_points is INT4 in DB
    String,
    bool,
);

#[derive(Serialize)]
pub struct UserResponse {
    pub id: i64,
    pub uuid: String,
    pub email: String,
    pub username: Option<String>,
    pub role: String,
    pub is_verified: bool,
    pub is_active: bool,
    pub created_at: DateTime<Utc>,
    pub last_login: Option<DateTime<Utc>>,
    pub contribution_points: i64,
    pub contribution_level: String,
    pub uploads_restricted: bool,
}

fn row_to_response(r: UserRow) -> UserResponse {
    UserResponse {
        id: r.0 as i64,
        uuid: r.1,
        email: r.2,
        username: r.3,
        role: r.4.as_api_wire().to_string(),
        is_verified: r.5,
        is_active: r.6,
        created_at: r.7,
        last_login: r.8,
        contribution_points: r.9 as i64,
        contribution_level: r.10,
        uploads_restricted: r.11,
    }
}

// ─── Request types ─────────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct ListUsersQuery {
    #[serde(default = "default_page")]
    pub page: i64,
    #[serde(default = "default_page_size")]
    pub per_page: i64,
    pub role: Option<String>,
    pub search: Option<String>,
    #[serde(default = "default_sort_by")]
    pub sort_by: String,
    #[serde(default = "default_sort_order")]
    pub sort_order: String,
}

fn default_page() -> i64 {
    1
}
fn default_page_size() -> i64 {
    20
}
fn default_sort_by() -> String {
    "joined".to_string()
}
fn default_sort_order() -> String {
    "desc".to_string()
}

#[derive(Deserialize)]
pub struct UpdateUserRequest {
    pub username: Option<String>,
    pub is_active: Option<bool>,
    pub is_verified: Option<bool>,
    pub uploads_restricted: Option<bool>,
}

#[derive(Deserialize)]
pub struct RoleUpdateRequest {
    pub role: String,
}

#[derive(Deserialize)]
pub struct SendUploadWarningRequest {
    pub reason: Option<String>,
}

// ─── Valid roles ──────────────────────────────────────────────────────────────

const VALID_ROLES: &[&str] = &["user", "paid_user", "moderator", "admin"];

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// GET /api/v1/users
pub async fn list_users(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Query(params): Query<ListUsersQuery>,
) -> Response {
    let _admin_id = match admin_auth(&headers, &state.pool, &state.config.secret_key_raw).await {
        Ok(id) => id,
        Err(resp) => return resp,
    };

    // Validate role filter
    if let Some(ref role) = params.role {
        if !VALID_ROLES.contains(&role.as_str()) {
            return (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(serde_json::json!({"error": format!("Invalid role: {role}")})),
            )
                .into_response();
        }
    }

    // Validate sort_by
    let order_expr = match params.sort_by.as_str() {
        "user" => "LOWER(COALESCE(username, email))".to_string(),
        "role" => "CASE role WHEN 'USER' THEN 1 WHEN 'PAID_USER' THEN 2 WHEN 'MODERATOR' THEN 3 WHEN 'ADMIN' THEN 4 ELSE 0 END".to_string(),
        "contribution" => "contribution_points".to_string(),
        "status" => "CASE WHEN is_active THEN 2 ELSE 0 END + CASE WHEN is_verified THEN 1 ELSE 0 END".to_string(),
        _ => "created_at".to_string(), // "joined" is default
    };
    let dir = if params.sort_order == "asc" {
        "ASC"
    } else {
        "DESC"
    };
    let secondary_dir = if params.sort_order == "asc" {
        "ASC"
    } else {
        "DESC"
    };

    // Build WHERE clause
    let mut conditions: Vec<String> = Vec::new();
    if let Some(ref role) = params.role {
        if let Some(user_role) = crate::db::UserRole::from_wire(role) {
            conditions.push(format!("role = '{}'", user_role.as_wire()));
        }
    }
    if let Some(ref search) = params.search {
        // Escape single quotes for safety (search is admin-only, but still good practice)
        let safe_search = search.replace('\'', "''");
        conditions.push(format!(
            "(email ILIKE '%{safe_search}%' OR username ILIKE '%{safe_search}%')"
        ));
    }

    let where_clause = if conditions.is_empty() {
        String::new()
    } else {
        format!("WHERE {}", conditions.join(" AND "))
    };

    let count_sql = format!("SELECT COUNT(*) FROM users {where_clause}");
    let total: i64 = match sqlx::query_scalar::<_, i64>(sqlx::AssertSqlSafe(count_sql.as_str()))
        .fetch_one(&state.pool_ro)
        .await
    {
        Ok(n) => n,
        Err(e) => {
            tracing::error!("list_users count error: {e}");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"error": "Database error"})),
            )
                .into_response();
        }
    };

    let page_size = params.per_page.clamp(1, 100);
    let page = params.page.max(1);
    let offset = (page - 1) * page_size;
    let pages = if total > 0 {
        (total + page_size - 1) / page_size
    } else {
        1
    };

    let data_sql = format!(
        r#"SELECT id, uuid, email, username, role, is_verified, is_active,
                  created_at, last_login, contribution_points, contribution_level, uploads_restricted
           FROM users {where_clause}
           ORDER BY {order_expr} {dir} NULLS LAST, id {secondary_dir}
           LIMIT {page_size} OFFSET {offset}"#
    );

    let rows = match sqlx::query_as::<_, UserRow>(sqlx::AssertSqlSafe(data_sql.as_str()))
        .fetch_all(&state.pool_ro)
        .await
    {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("list_users fetch error: {e}");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"error": "Database error"})),
            )
                .into_response();
        }
    };

    let items: Vec<UserResponse> = rows.into_iter().map(row_to_response).collect();
    (
        StatusCode::OK,
        Json(serde_json::json!({
            "items": items,
            "total": total,
            "page": page,
            "per_page": page_size,
            "pages": pages,
        })),
    )
        .into_response()
}

/// GET /api/v1/users/{user_id}
pub async fn get_user(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(user_id): Path<i32>,
) -> Response {
    let _admin_id = match admin_auth(&headers, &state.pool, &state.config.secret_key_raw).await {
        Ok(id) => id,
        Err(resp) => return resp,
    };

    let row = sqlx::query_as::<_, UserRow>(
        r#"SELECT id, uuid, email, username, role, is_verified, is_active,
                  created_at, last_login, contribution_points, contribution_level, uploads_restricted
           FROM users WHERE id = $1"#,
    )
    .bind(user_id)
    .fetch_optional(&state.pool_ro)
    .await;

    match row {
        Ok(Some(r)) => (StatusCode::OK, Json(row_to_response(r))).into_response(),
        Ok(None) => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"error": "User not found"})),
        )
            .into_response(),
        Err(e) => {
            tracing::error!("get_user error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"error": "Database error"})),
            )
                .into_response()
        }
    }
}

/// PATCH /api/v1/users/{user_id}
pub async fn update_user(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(user_id): Path<i32>,
    Json(body): Json<UpdateUserRequest>,
) -> Response {
    let _admin_id = match admin_auth(&headers, &state.pool, &state.config.secret_key_raw).await {
        Ok(id) => id,
        Err(resp) => return resp,
    };

    // Confirm user exists
    let exists: Option<(i32,)> = match sqlx::query_as("SELECT id FROM users WHERE id = $1")
        .bind(user_id)
        .fetch_optional(&state.pool)
        .await
    {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("update_user existence check: {e}");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"error": "Database error"})),
            )
                .into_response();
        }
    };
    if exists.is_none() {
        return (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"error": "User not found"})),
        )
            .into_response();
    }

    // Check username uniqueness if username is being changed
    if let Some(ref new_username) = body.username {
        let conflict: Option<(i32,)> = match sqlx::query_as(
            "SELECT id FROM users WHERE LOWER(username) = LOWER($1) AND id != $2",
        )
        .bind(new_username)
        .bind(user_id)
        .fetch_optional(&state.pool)
        .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("update_user username conflict: {e}");
                return (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(serde_json::json!({"error": "Database error"})),
                )
                    .into_response();
            }
        };
        if conflict.is_some() {
            return (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(serde_json::json!({"error": "Username already taken"})),
            )
                .into_response();
        }
    }

    // Build dynamic SET clause
    let mut sets: Vec<String> = Vec::new();
    let mut param_idx: i32 = 1;

    if body.username.is_some() {
        sets.push(format!("username = ${param_idx}"));
        param_idx += 1;
    }
    if body.is_active.is_some() {
        sets.push(format!("is_active = ${param_idx}"));
        param_idx += 1;
    }
    if body.is_verified.is_some() {
        sets.push(format!("is_verified = ${param_idx}"));
        param_idx += 1;
    }
    if body.uploads_restricted.is_some() {
        sets.push(format!("uploads_restricted = ${param_idx}"));
        param_idx += 1;
    }

    if sets.is_empty() {
        // Nothing to update — return current state
        return get_user_by_id_response(&state.pool_ro, user_id).await;
    }

    let sql = format!(
        "UPDATE users SET {} WHERE id = ${param_idx}",
        sets.join(", ")
    );

    let mut q = sqlx::query(sqlx::AssertSqlSafe(sql.as_str()));
    if let Some(ref v) = body.username {
        q = q.bind(v);
    }
    if let Some(v) = body.is_active {
        q = q.bind(v);
    }
    if let Some(v) = body.is_verified {
        q = q.bind(v);
    }
    if let Some(v) = body.uploads_restricted {
        q = q.bind(v);
    }
    q = q.bind(user_id);

    if let Err(e) = q.execute(&state.pool).await {
        tracing::error!("update_user execute: {e}");
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({"error": "Database error"})),
        )
            .into_response();
    }

    get_user_by_id_response(&state.pool_ro, user_id).await
}

/// PATCH /api/v1/users/{user_id}/role
pub async fn update_user_role(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(user_id): Path<i32>,
    Json(body): Json<RoleUpdateRequest>,
) -> Response {
    let admin_id = match admin_auth(&headers, &state.pool, &state.config.secret_key_raw).await {
        Ok(id) => id,
        Err(resp) => return resp,
    };

    // Validate role value
    if !VALID_ROLES.contains(&body.role.as_str()) {
        return (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(serde_json::json!({
                "error": format!("Invalid role: {}. Valid roles: {:?}", body.role, VALID_ROLES)
            })),
        )
            .into_response();
    }

    // Confirm user exists
    let exists: Option<(i32,)> = match sqlx::query_as("SELECT id FROM users WHERE id = $1")
        .bind(user_id)
        .fetch_optional(&state.pool)
        .await
    {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("update_user_role existence check: {e}");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"error": "Database error"})),
            )
                .into_response();
        }
    };
    if exists.is_none() {
        return (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"error": "User not found"})),
        )
            .into_response();
    }

    // Prevent self-demotion
    if user_id == admin_id {
        return (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(serde_json::json!({"error": "Cannot change your own role"})),
        )
            .into_response();
    }

    let Some(new_role) = crate::db::UserRole::from_wire(&body.role) else {
        return (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(serde_json::json!({
                "error": format!("Invalid role: {}. Valid roles: {:?}", body.role, VALID_ROLES)
            })),
        )
            .into_response();
    };

    if let Err(e) = sqlx::query("UPDATE users SET role = $1 WHERE id = $2")
        .bind(new_role)
        .bind(user_id)
        .execute(&state.pool)
        .await
    {
        tracing::error!("update_user_role execute: {e}");
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({"error": "Database error"})),
        )
            .into_response();
    }

    get_user_by_id_response(&state.pool_ro, user_id).await
}

/// DELETE /api/v1/users/{user_id}
pub async fn delete_user(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(user_id): Path<i32>,
) -> Response {
    let admin_id = match admin_auth(&headers, &state.pool, &state.config.secret_key_raw).await {
        Ok(id) => id,
        Err(resp) => return resp,
    };

    if user_id == admin_id {
        return (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(serde_json::json!({"error": "Cannot delete your own account"})),
        )
            .into_response();
    }

    let exists: Option<(i32,)> = match sqlx::query_as("SELECT id FROM users WHERE id = $1")
        .bind(user_id)
        .fetch_optional(&state.pool)
        .await
    {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("delete_user existence check: {e}");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"error": "Database error"})),
            )
                .into_response();
        }
    };
    if exists.is_none() {
        return (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"error": "User not found"})),
        )
            .into_response();
    }

    if let Err(e) = sqlx::query("DELETE FROM users WHERE id = $1")
        .bind(user_id)
        .execute(&state.pool)
        .await
    {
        tracing::error!("delete_user execute: {e}");
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({"error": "Database error"})),
        )
            .into_response();
    }

    (
        StatusCode::OK,
        Json(serde_json::json!({"message": "User deleted successfully"})),
    )
        .into_response()
}

/// POST /api/v1/users/{user_id}/send-upload-warning
pub async fn send_upload_warning(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(user_id): Path<i32>,
    Json(body): Json<SendUploadWarningRequest>,
) -> Response {
    let _admin_id = match admin_auth(&headers, &state.pool, &state.config.secret_key_raw).await {
        Ok(id) => id,
        Err(resp) => return resp,
    };

    // Fetch user email
    let row: Option<(String,)> = match sqlx::query_as("SELECT email FROM users WHERE id = $1")
        .bind(user_id)
        .fetch_optional(&state.pool_ro)
        .await
    {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("send_upload_warning fetch user: {e}");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"error": "Database error"})),
            )
                .into_response();
        }
    };

    let Some((email,)) = row else {
        return (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"error": "User not found"})),
        )
            .into_response();
    };

    // Check SMTP is configured
    if state.config.smtp_host.is_none() {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(serde_json::json!({"error": "Email service is not configured on this instance"})),
        )
            .into_response();
    }

    let reason = body
        .reason
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or("We detected upload activity that may violate contribution policies.")
        .to_string();

    let body_text = format!(
        "Dear MediaFusion User,\n\n\
        Your account has received an upload warning from an administrator.\n\n\
        Reason: {reason}\n\n\
        If you believe this is a mistake, please contact support.\n\n\
        The MediaFusion Team"
    );

    let send_result = send_email(&state, &email, "MediaFusion: Upload Warning", body_text).await;

    match send_result {
        Ok(()) => (
            StatusCode::OK,
            Json(serde_json::json!({"message": format!("Upload warning email sent to {email}")})),
        )
            .into_response(),
        Err(e) => {
            tracing::error!("send_upload_warning SMTP error: {e}");
            (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(serde_json::json!({"error": "Failed to send email"})),
            )
                .into_response()
        }
    }
}

// ─── Internal helpers ─────────────────────────────────────────────────────────

async fn send_email(
    state: &AppState,
    to: &str,
    subject: &str,
    body: String,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    use lettre::{
        AsyncTransport, Message, Tokio1Executor,
        message::header::ContentType,
        transport::smtp::{
            AsyncSmtpTransport,
            authentication::Credentials,
            client::{Tls, TlsParameters},
        },
    };

    let smtp_host = state
        .config
        .smtp_host
        .as_deref()
        .ok_or("SMTP not configured")?;

    let email = Message::builder()
        .from(state.config.smtp_from.parse()?)
        .to(to.parse()?)
        .subject(subject)
        .header(ContentType::TEXT_PLAIN)
        .body(body)?;

    // SMTP_USE_SSL=true  → implicit TLS wrapper (port 465)
    // SMTP_USE_TLS=true  → STARTTLS (port 587, default)
    // both false         → plaintext (internal relay / mailhog)
    // Port 465 always implies implicit SSL regardless of flags.
    let use_ssl = state.config.smtp_use_ssl || state.config.smtp_port == 465;
    let mut builder = if use_ssl {
        let tls = TlsParameters::new(smtp_host.to_string())?;
        AsyncSmtpTransport::<Tokio1Executor>::builder_dangerous(smtp_host)
            .port(state.config.smtp_port)
            .tls(Tls::Wrapper(tls))
    } else if state.config.smtp_use_tls {
        // STARTTLS: connect plaintext then upgrade. relay() would build an
        // implicit-TLS (Tls::Wrapper) transport and fail on port 587 with
        // "received corrupt message of type InvalidContentType".
        AsyncSmtpTransport::<Tokio1Executor>::starttls_relay(smtp_host)?
            .port(state.config.smtp_port)
    } else {
        AsyncSmtpTransport::<Tokio1Executor>::builder_dangerous(smtp_host)
            .port(state.config.smtp_port)
    };

    if let (Some(user), Some(pass)) = (
        state.config.smtp_username.as_deref(),
        state.config.smtp_password.as_deref(),
    ) {
        builder = builder.credentials(Credentials::new(user.to_string(), pass.to_string()));
    }

    let mailer = builder.build();
    mailer.send(email).await?;
    Ok(())
}

async fn get_user_by_id_response(pool: &sqlx::PgPool, user_id: i32) -> Response {
    let row = sqlx::query_as::<_, UserRow>(
        r#"SELECT id, uuid, email, username, role, is_verified, is_active,
                  created_at, last_login, contribution_points, contribution_level, uploads_restricted
           FROM users WHERE id = $1"#,
    )
    .bind(user_id)
    .fetch_optional(pool)
    .await;

    match row {
        Ok(Some(r)) => (StatusCode::OK, Json(row_to_response(r))).into_response(),
        Ok(None) => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"error": "User not found"})),
        )
            .into_response(),
        Err(e) => {
            tracing::error!("get_user_by_id_response error: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"error": "Database error"})),
            )
                .into_response()
        }
    }
}
