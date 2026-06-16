/// User authentication endpoints.
///
/// Routes (prefix /api/v1/auth):
///   POST /register
///   POST /login
///   POST /refresh
///   POST /verify-email
///   POST /resend-verification
///   POST /forgot-password
///   POST /reset-password
///   POST /change-password
///   DELETE /account
///   GET /me
///   PATCH /me
///
/// Token format: base64url(JSON) + "." + HMAC-SHA256(secret_key, payload_str)
/// This exactly mirrors the Python implementation in api/routers/user/auth.py.
///
/// Password format: "{salt}${sha256(password + salt).hexdigest()}"
use std::sync::Arc;

use axum::{
    extract::State,
    http::{HeaderMap, HeaderValue, StatusCode},
    response::IntoResponse,
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::{DateTime, Utc};
use fred::interfaces::KeysInterface;
use fred::types::Expiration;
use hmac::{Hmac, KeyInit, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use sqlx::PgPool;
use uuid::Uuid;

use crate::{db::UserRole, state::AppState};

// Token expiry
const ACCESS_TOKEN_EXPIRE_SECS: i64 = 60 * 60 * 24; // 24h
const REFRESH_TOKEN_EXPIRE_SECS: i64 = 60 * 60 * 24 * 30; // 30d
const EMAIL_VERIFY_TOKEN_EXPIRE_SECS: i64 = 60 * 60 * 24; // 24h
const PASSWORD_RESET_TOKEN_EXPIRE_SECS: i64 = 60 * 60; // 1h

// ─── Request / Response types ─────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct RegisterRequest {
    pub email: String,
    pub username: String,
    pub password: String,
    #[serde(default)]
    pub newsletter_opt_in: bool,
}

#[derive(Deserialize)]
pub struct LoginRequest {
    pub email: String,
    pub password: String,
}

#[derive(Deserialize)]
pub struct RefreshRequest {
    pub refresh_token: String,
}

#[derive(Deserialize)]
pub struct VerifyEmailRequest {
    pub token: String,
}

#[derive(Deserialize)]
pub struct ResendVerificationRequest {
    pub email: String,
}

#[derive(Deserialize)]
pub struct ForgotPasswordRequest {
    pub email: String,
}

#[derive(Deserialize)]
pub struct ResetPasswordRequest {
    pub token: String,
    pub new_password: String,
}

#[derive(Deserialize)]
pub struct ChangePasswordRequest {
    pub current_password: String,
    pub new_password: String,
}

#[derive(Deserialize)]
pub struct DeleteAccountRequest {
    pub password: String,
}

#[derive(Deserialize)]
pub struct UpdateMeRequest {
    pub username: Option<String>,
    pub contribute_anonymously: Option<bool>,
}

#[derive(Serialize)]
pub struct UserResponse {
    pub id: i32,
    pub uuid: String,
    pub email: String,
    pub username: Option<String>,
    pub role: String,
    pub is_verified: bool,
    pub is_active: bool,
    pub created_at: DateTime<Utc>,
    pub last_login: Option<DateTime<Utc>>,
    pub contribution_points: i32,
    pub contribution_level: String,
    pub contribute_anonymously: bool,
    pub uploads_restricted: bool,
}

#[derive(Serialize)]
pub struct TokenResponse {
    pub access_token: String,
    pub refresh_token: String,
    pub token_type: String,
    pub user: UserResponse,
}

#[derive(Serialize)]
pub struct RegisterResponse {
    pub message: String,
    pub email: String,
    pub requires_verification: bool,
}

// ─── Token helpers ────────────────────────────────────────────────────────────

fn create_token(payload: serde_json::Value, secret_key: &str) -> String {
    let payload_str = URL_SAFE_NO_PAD.encode(payload.to_string().as_bytes());
    let mut mac =
        Hmac::<Sha256>::new_from_slice(secret_key.as_bytes()).expect("HMAC accepts any key size");
    mac.update(payload_str.as_bytes());
    let signature = hex_encode(&mac.finalize().into_bytes());
    format!("{payload_str}.{signature}")
}

fn decode_token(token: &str, secret_key: &str) -> Option<serde_json::Value> {
    let dot = token.rfind('.')?;
    let (payload_str, sig) = token.split_at(dot);
    let sig = &sig[1..]; // remove leading '.'

    let mut mac =
        Hmac::<Sha256>::new_from_slice(secret_key.as_bytes()).expect("HMAC accepts any key size");
    mac.update(payload_str.as_bytes());
    let expected = hex_encode(&mac.finalize().into_bytes());

    // Constant-time comparison
    if !constant_time_eq_hex(&expected, sig) {
        return None;
    }

    // Strip base64 padding that Python's urlsafe_b64encode adds so tokens
    // created by the Python server are accepted here too.
    let decoded = URL_SAFE_NO_PAD
        .decode(payload_str.trim_end_matches('='))
        .ok()?;
    let data: serde_json::Value = serde_json::from_slice(&decoded).ok()?;

    // Check expiry
    let exp = data["exp"].as_f64()?;
    if exp < Utc::now().timestamp() as f64 {
        return None;
    }

    Some(data)
}

fn create_access_token(user_id: i32, role: &str, secret_key: &str) -> String {
    let exp = Utc::now().timestamp() + ACCESS_TOKEN_EXPIRE_SECS;
    create_token(
        serde_json::json!({"sub": user_id.to_string(), "role": role, "type": "access", "exp": exp}),
        secret_key,
    )
}

fn create_refresh_token(user_id: i32, secret_key: &str) -> String {
    let exp = Utc::now().timestamp() + REFRESH_TOKEN_EXPIRE_SECS;
    create_token(
        serde_json::json!({"sub": user_id.to_string(), "type": "refresh", "exp": exp}),
        secret_key,
    )
}

fn create_email_verify_token(user_id: i32, secret_key: &str) -> String {
    let exp = Utc::now().timestamp() + EMAIL_VERIFY_TOKEN_EXPIRE_SECS;
    create_token(
        serde_json::json!({"sub": user_id.to_string(), "type": "email_verify", "exp": exp}),
        secret_key,
    )
}

fn create_password_reset_token(user_id: i32, pwd_hash_prefix: &str, secret_key: &str) -> String {
    let exp = Utc::now().timestamp() + PASSWORD_RESET_TOKEN_EXPIRE_SECS;
    create_token(
        serde_json::json!({"sub": user_id.to_string(), "type": "password_reset", "pwd_hash": pwd_hash_prefix, "exp": exp}),
        secret_key,
    )
}

fn hex_encode(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn constant_time_eq_hex(expected: &str, actual: &str) -> bool {
    if expected.len() != actual.len() {
        return false;
    }
    expected
        .bytes()
        .zip(actual.bytes())
        .fold(0u8, |acc, (a, b)| acc | (a ^ b))
        == 0
}

// ─── Password helpers ─────────────────────────────────────────────────────────

fn hash_password(password: &str) -> String {
    use rand_core::Rng;
    let mut salt_bytes = [0u8; 16];
    rand::rng().fill_bytes(&mut salt_bytes);
    let salt = hex_encode(&salt_bytes);
    let digest = sha256_hex(&format!("{}{}", password, salt));
    format!("{salt}${digest}")
}

fn verify_password(password: &str, hashed: &str) -> bool {
    let parts: Vec<&str> = hashed.splitn(2, '$').collect();
    if parts.len() != 2 {
        return false;
    }
    let (salt, stored_hash) = (parts[0], parts[1]);
    sha256_hex(&format!("{}{}", password, salt)) == stored_hash
}

fn sha256_hex(input: &str) -> String {
    use sha2::Digest;
    let mut h = sha2::Sha256::new();
    h.update(input.as_bytes());
    hex_encode(&h.finalize())
}

// ─── DB helpers ───────────────────────────────────────────────────────────────

struct UserRow {
    id: i32,
    uuid: String,
    email: String,
    username: Option<String>,
    password_hash: Option<String>,
    role: String,
    is_verified: bool,
    is_active: bool,
    created_at: DateTime<Utc>,
    last_login: Option<DateTime<Utc>>,
    contribution_points: i32,
    contribution_level: String,
    contribute_anonymously: bool,
    uploads_restricted: bool,
}

impl From<UserRow> for UserResponse {
    fn from(u: UserRow) -> Self {
        UserResponse {
            id: u.id,
            uuid: u.uuid,
            email: u.email,
            username: u.username,
            role: u.role,
            is_verified: u.is_verified,
            is_active: u.is_active,
            created_at: u.created_at,
            last_login: u.last_login,
            contribution_points: u.contribution_points,
            contribution_level: u.contribution_level,
            contribute_anonymously: u.contribute_anonymously,
            uploads_restricted: u.uploads_restricted,
        }
    }
}

async fn fetch_user_by_email(pool: &PgPool, email: &str) -> Option<UserRow> {
    sqlx::query_as::<
        _,
        (
            i32,
            String,
            String,
            Option<String>,
            Option<String>,
            UserRole,
            bool,
            bool,
            DateTime<Utc>,
            Option<DateTime<Utc>>,
            i32,
            String,
            bool,
            bool,
        ),
    >(
        r#"SELECT id, uuid, email, username, password_hash, role, is_verified, is_active,
                  created_at, last_login,
                  contribution_points, contribution_level,
                  contribute_anonymously, uploads_restricted
           FROM users WHERE LOWER(email) = LOWER($1)"#,
    )
    .bind(email)
    .fetch_optional(pool)
    .await
    .map_err(|e| {
        tracing::error!("fetch_user_by_email error: {e}");
        e
    })
    .ok()
    .flatten()
    .map(row_to_user)
}

async fn fetch_user_by_id(pool: &PgPool, id: i32) -> Option<UserRow> {
    sqlx::query_as::<
        _,
        (
            i32,
            String,
            String,
            Option<String>,
            Option<String>,
            UserRole,
            bool,
            bool,
            DateTime<Utc>,
            Option<DateTime<Utc>>,
            i32,
            String,
            bool,
            bool,
        ),
    >(
        r#"SELECT id, uuid, email, username, password_hash, role, is_verified, is_active,
                  created_at, last_login,
                  contribution_points, contribution_level,
                  contribute_anonymously, uploads_restricted
           FROM users WHERE id = $1"#,
    )
    .bind(id)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten()
    .map(row_to_user)
}

#[allow(clippy::type_complexity)]
fn row_to_user(
    r: (
        i32,
        String,
        String,
        Option<String>,
        Option<String>,
        UserRole,
        bool,
        bool,
        DateTime<Utc>,
        Option<DateTime<Utc>>,
        i32,
        String,
        bool,
        bool,
    ),
) -> UserRow {
    UserRow {
        id: r.0,
        uuid: r.1,
        email: r.2,
        username: r.3,
        password_hash: r.4,
        role: r.5.as_api_wire().to_string(),
        is_verified: r.6,
        is_active: r.7,
        created_at: r.8,
        last_login: r.9,
        contribution_points: r.10,
        contribution_level: r.11,
        contribute_anonymously: r.12,
        uploads_restricted: r.13,
    }
}

/// Extract Bearer token from Authorization header.
fn bearer_token(headers: &HeaderMap) -> Option<String> {
    headers
        .get("authorization")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "))
        .map(|s| s.to_string())
}

/// Validate Bearer token → return user_id if valid access token.
pub fn validate_access_token(headers: &HeaderMap, secret_key: &str) -> Option<i32> {
    let token = bearer_token(headers)?;
    let data = decode_token(&token, secret_key)?;
    if data["type"].as_str() != Some("access") {
        return None;
    }
    data["sub"].as_str()?.parse().ok()
}

/// Validate access token and ensure the user account is still active.
pub async fn validate_active_access_token(
    pool: &PgPool,
    headers: &HeaderMap,
    secret_key: &str,
) -> Option<i32> {
    let user_id = validate_access_token(headers, secret_key)?;
    let active: Option<bool> = sqlx::query_scalar("SELECT is_active FROM users WHERE id = $1")
        .bind(user_id)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten();
    active.filter(|&a| a).map(|_| user_id)
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

pub async fn register(
    State(state): State<Arc<AppState>>,
    Json(req): Json<RegisterRequest>,
) -> impl IntoResponse {
    if req.password.len() < 8 {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"detail": "Password must be at least 8 characters"})),
        )
            .into_response();
    }
    if req.username.len() < 3 || req.username.len() > 100 {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"detail": "Username must be 3–100 characters"})),
        )
            .into_response();
    }

    // Check duplicate email / username
    let email_exists: Option<(i32,)> =
        sqlx::query_as("SELECT id FROM users WHERE LOWER(email) = LOWER($1)")
            .bind(&req.email)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);
    if email_exists.is_some() {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"detail": "Email already registered"})),
        )
            .into_response();
    }

    let username_exists: Option<(i32,)> =
        sqlx::query_as("SELECT id FROM users WHERE LOWER(username) = LOWER($1)")
            .bind(&req.username)
            .fetch_optional(&state.pool)
            .await
            .unwrap_or(None);
    if username_exists.is_some() {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"detail": "Username already taken"})),
        )
            .into_response();
    }

    let smtp_configured = state.config.smtp_host.is_some();
    let auto_verify = !smtp_configured;
    let password_hash = hash_password(&req.password);
    let user_uuid = Uuid::new_v4().to_string();

    let insert: Option<(i32,)> = sqlx::query_as(
        r#"INSERT INTO users (uuid, email, username, password_hash, role, is_verified, is_active, last_login, created_at,
                              contribution_points, metadata_edits_approved, stream_edits_approved,
                              contribution_level, contribute_anonymously, uploads_restricted)
           VALUES ($1, $2, $3, $4, $5, $6, true, $7, NOW(), 0, 0, 0, 'beginner', false, false)
           RETURNING id"#,
    )
    .bind(&user_uuid)
    .bind(&req.email)
    .bind(&req.username)
    .bind(&password_hash)
    .bind(crate::db::UserRole::User)
    .bind(auto_verify)
    .bind(if auto_verify { Some(Utc::now()) } else { None })
    .fetch_optional(&state.pool)
    .await
    .unwrap_or_else(|e| {
        tracing::error!("register INSERT failed: {e}");
        None
    });

    let user_id = match insert {
        Some((id,)) => id,
        None => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"detail": "Registration failed"})),
            )
                .into_response();
        }
    };

    // Create default profile
    let _ = sqlx::query(
        "INSERT INTO user_profiles (user_id, name, config, is_default) VALUES ($1, 'Default', '{}', true) ON CONFLICT DO NOTHING",
    )
    .bind(user_id)
    .execute(&state.pool)
    .await;

    // ConvertKit newsletter subscription (fire-and-forget)
    if req.newsletter_opt_in {
        if let (Some(api_key), Some(form_id)) = (
            state.config.convertkit_api_key.clone(),
            state.config.convertkit_form_id.clone(),
        ) {
            let email = req.email.clone();
            let http = state.http.clone();
            tokio::spawn(async move {
                if let Err(e) = subscribe_to_convertkit(&http, &api_key, &form_id, &email).await {
                    tracing::warn!("ConvertKit subscribe failed for {email}: {e}");
                }
            });
        }
    }

    if !auto_verify {
        // Send verification email if SMTP is configured
        let token = create_email_verify_token(user_id, &state.config.secret_key_raw);
        if let Err(e) =
            send_email_verification(&state, &req.email, Some(req.username.as_str()), &token).await
        {
            tracing::warn!("send_email_verification failed: {e}");
        }
        return (StatusCode::OK, Json(serde_json::json!({
            "message": "Registration successful. Please check your email to verify your account.",
            "email": req.email,
            "requires_verification": true,
        }))).into_response();
    }

    let user = match fetch_user_by_id(&state.pool, user_id).await {
        Some(u) => u,
        None => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"detail": "User not found after insert"})),
            )
                .into_response()
        }
    };

    let access_token = create_access_token(user.id, &user.role, &state.config.secret_key_raw);
    let refresh_token = create_refresh_token(user.id, &state.config.secret_key_raw);

    (
        StatusCode::OK,
        Json(TokenResponse {
            access_token,
            refresh_token,
            token_type: "bearer".into(),
            user: user.into(),
        }),
    )
        .into_response()
}

pub async fn login(
    State(state): State<Arc<AppState>>,
    Json(req): Json<LoginRequest>,
) -> impl IntoResponse {
    let user = match fetch_user_by_email(&state.pool, &req.email).await {
        Some(u) => u,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Invalid email or password"})),
            )
                .into_response()
        }
    };

    let hash = user.password_hash.as_deref().unwrap_or("");
    if !verify_password(&req.password, hash) {
        return (
            StatusCode::UNAUTHORIZED,
            Json(serde_json::json!({"detail": "Invalid email or password"})),
        )
            .into_response();
    }
    if !user.is_active {
        return (
            StatusCode::FORBIDDEN,
            Json(serde_json::json!({"detail": "Account is disabled"})),
        )
            .into_response();
    }
    if !user.is_verified && state.config.smtp_host.is_some() {
        return (
            StatusCode::FORBIDDEN,
            [(
                axum::http::HeaderName::from_static("x-error-code"),
                HeaderValue::from_static("EMAIL_NOT_VERIFIED"),
            )],
            Json(serde_json::json!({
                "detail": "Email not verified. Please check your inbox for a verification link."
            })),
        )
            .into_response();
    }

    // Update last_login
    let _ = sqlx::query("UPDATE users SET last_login = NOW() WHERE id = $1")
        .bind(user.id)
        .execute(&state.pool)
        .await;

    let access_token = create_access_token(user.id, &user.role, &state.config.secret_key_raw);
    let refresh_token = create_refresh_token(user.id, &state.config.secret_key_raw);

    (
        StatusCode::OK,
        Json(TokenResponse {
            access_token,
            refresh_token,
            token_type: "bearer".into(),
            user: user.into(),
        }),
    )
        .into_response()
}

pub async fn refresh(
    State(state): State<Arc<AppState>>,
    Json(req): Json<RefreshRequest>,
) -> impl IntoResponse {
    let data = match decode_token(&req.refresh_token, &state.config.secret_key_raw) {
        Some(d) if d["type"].as_str() == Some("refresh") => d,
        _ => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Invalid refresh token"})),
            )
                .into_response()
        }
    };

    let user_id: i32 = match data["sub"].as_str().and_then(|s| s.parse().ok()) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "Invalid token"})),
            )
                .into_response()
        }
    };

    let user = match fetch_user_by_id(&state.pool, user_id).await {
        Some(u) if u.is_active => u,
        _ => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"detail": "User not found or inactive"})),
            )
                .into_response()
        }
    };

    let access_token = create_access_token(user.id, &user.role, &state.config.secret_key_raw);
    let new_refresh = create_refresh_token(user.id, &state.config.secret_key_raw);

    (
        StatusCode::OK,
        Json(TokenResponse {
            access_token,
            refresh_token: new_refresh,
            token_type: "bearer".into(),
            user: user.into(),
        }),
    )
        .into_response()
}

pub async fn verify_email(
    State(state): State<Arc<AppState>>,
    Json(req): Json<VerifyEmailRequest>,
) -> impl IntoResponse {
    let data = match decode_token(&req.token, &state.config.secret_key_raw) {
        Some(d) if d["type"].as_str() == Some("email_verify") => d,
        _ => {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({"detail": "Invalid or expired verification token"})),
            )
                .into_response()
        }
    };

    let user_id: i32 = match data["sub"].as_str().and_then(|s| s.parse().ok()) {
        Some(id) => id,
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({"detail": "Invalid token"})),
            )
                .into_response()
        }
    };

    let user = match fetch_user_by_id(&state.pool, user_id).await {
        Some(u) => u,
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({"detail": "User not found."})),
            )
                .into_response()
        }
    };

    if user.is_verified {
        return (
            StatusCode::OK,
            Json(serde_json::json!({"message": "Email already verified. You can log in."})),
        )
            .into_response();
    }

    let _ = sqlx::query("UPDATE users SET is_verified = true WHERE id = $1")
        .bind(user_id)
        .execute(&state.pool)
        .await;

    (
        StatusCode::OK,
        Json(serde_json::json!({"message": "Email verified successfully. You can now log in."})),
    )
        .into_response()
}

pub async fn resend_verification(
    State(state): State<Arc<AppState>>,
    Json(req): Json<ResendVerificationRequest>,
) -> impl IntoResponse {
    if state.config.smtp_host.is_none() {
        return (
            StatusCode::BAD_REQUEST,
            Json(
                serde_json::json!({"detail": "Email service is not configured on this instance."}),
            ),
        )
            .into_response();
    }

    let email_key = req.email.to_lowercase();
    let cooldown_key = format!("resend_verify:{email_key}");
    if let Ok(Some(_)) = state.redis.get::<Option<String>, _>(&cooldown_key).await {
        return (
            StatusCode::TOO_MANY_REQUESTS,
            Json(serde_json::json!({"detail": "Please wait 60 seconds before requesting another email."})),
        )
            .into_response();
    }

    if let Some(user) = fetch_user_by_email(&state.pool, &req.email).await {
        if !user.is_verified {
            let token = create_email_verify_token(user.id, &state.config.secret_key_raw);
            let _ = send_email_verification(&state, &user.email, user.username.as_deref(), &token)
                .await;
            let _ = state
                .redis
                .set::<(), _, _>(cooldown_key, "1", Some(Expiration::EX(60)), None, false)
                .await;
        }
    }
    (
        StatusCode::OK,
        Json(
            serde_json::json!({"message": "If that email is registered and unverified, you'll receive a new link shortly."}),
        ),
    )
        .into_response()
}

pub async fn forgot_password(
    State(state): State<Arc<AppState>>,
    Json(req): Json<ForgotPasswordRequest>,
) -> impl IntoResponse {
    if state.config.smtp_host.is_none() {
        return (
            StatusCode::BAD_REQUEST,
            Json(
                serde_json::json!({"detail": "Email service is not configured on this instance."}),
            ),
        )
            .into_response();
    }

    if let Some(user) = fetch_user_by_email(&state.pool, &req.email).await {
        if user.is_active && user.password_hash.is_some() {
            let pwd_prefix = user
                .password_hash
                .as_deref()
                .unwrap_or("")
                .get(..16)
                .unwrap_or("")
                .to_string();
            let token =
                create_password_reset_token(user.id, &pwd_prefix, &state.config.secret_key_raw);
            let _ =
                send_password_reset_email(&state, &user.email, user.username.as_deref(), &token)
                    .await;
        }
    }
    (
        StatusCode::OK,
        Json(serde_json::json!({
            "message": "If an account with that email exists, a password reset link has been sent."
        })),
    )
        .into_response()
}

pub async fn reset_password(
    State(state): State<Arc<AppState>>,
    Json(req): Json<ResetPasswordRequest>,
) -> impl IntoResponse {
    if req.new_password.len() < 8 {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"detail": "Password must be at least 8 characters"})),
        )
            .into_response();
    }

    let data = match decode_token(&req.token, &state.config.secret_key_raw) {
        Some(d) if d["type"].as_str() == Some("password_reset") => d,
        _ => {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({"detail": "Invalid or expired reset token"})),
            )
                .into_response()
        }
    };

    let user_id: i32 = match data["sub"].as_str().and_then(|s| s.parse().ok()) {
        Some(id) => id,
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({"detail": "Invalid token"})),
            )
                .into_response()
        }
    };

    let user = match fetch_user_by_id(&state.pool, user_id).await {
        Some(u) => u,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({"detail": "User not found"})),
            )
                .into_response()
        }
    };

    // Verify the pwd_hash prefix matches (token invalidated if password already changed)
    let stored_prefix = user
        .password_hash
        .as_deref()
        .unwrap_or("")
        .get(..16)
        .unwrap_or("");
    if data["pwd_hash"].as_str().unwrap_or("") != stored_prefix {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"detail": "Reset token is no longer valid"})),
        )
            .into_response();
    }

    let new_hash = hash_password(&req.new_password);
    let _ = sqlx::query("UPDATE users SET password_hash = $1, is_verified = true WHERE id = $2")
        .bind(&new_hash)
        .bind(user_id)
        .execute(&state.pool)
        .await;

    (
        StatusCode::OK,
        Json(serde_json::json!({
            "message": "Password reset successfully. You can now log in with your new password."
        })),
    )
        .into_response()
}

pub async fn change_password(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(req): Json<ChangePasswordRequest>,
) -> impl IntoResponse {
    if req.new_password.len() < 8 {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"detail": "New password must be at least 8 characters"})),
        )
            .into_response();
    }

    let user_id =
        match validate_active_access_token(&state.pool, &headers, &state.config.secret_key_raw)
            .await
        {
            Some(id) => id,
            None => {
                return (
                    StatusCode::UNAUTHORIZED,
                    Json(serde_json::json!({"detail": "Authentication required"})),
                )
                    .into_response()
            }
        };

    let user = match fetch_user_by_id(&state.pool, user_id).await {
        Some(u) => u,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({"detail": "User not found"})),
            )
                .into_response()
        }
    };

    if !verify_password(
        &req.current_password,
        user.password_hash.as_deref().unwrap_or(""),
    ) {
        return (
            StatusCode::UNAUTHORIZED,
            Json(serde_json::json!({"detail": "Current password is incorrect"})),
        )
            .into_response();
    }

    let new_hash = hash_password(&req.new_password);
    let _ = sqlx::query("UPDATE users SET password_hash = $1 WHERE id = $2")
        .bind(&new_hash)
        .bind(user_id)
        .execute(&state.pool)
        .await;

    (
        StatusCode::OK,
        Json(serde_json::json!({"message": "Password changed successfully."})),
    )
        .into_response()
}

pub async fn delete_account(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(req): Json<DeleteAccountRequest>,
) -> impl IntoResponse {
    let user_id =
        match validate_active_access_token(&state.pool, &headers, &state.config.secret_key_raw)
            .await
        {
            Some(id) => id,
            None => {
                return (
                    StatusCode::UNAUTHORIZED,
                    Json(serde_json::json!({"detail": "Authentication required"})),
                )
                    .into_response()
            }
        };

    let user = match fetch_user_by_id(&state.pool, user_id).await {
        Some(u) => u,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({"detail": "User not found"})),
            )
                .into_response()
        }
    };

    if !verify_password(&req.password, user.password_hash.as_deref().unwrap_or("")) {
        return (
            StatusCode::UNAUTHORIZED,
            Json(serde_json::json!({"detail": "Invalid password"})),
        )
            .into_response();
    }

    let _ = sqlx::query("DELETE FROM users WHERE id = $1")
        .bind(user_id)
        .execute(&state.pool)
        .await;

    (
        StatusCode::OK,
        Json(serde_json::json!({"message": "Account deleted."})),
    )
        .into_response()
}

pub async fn logout(State(state): State<Arc<AppState>>, headers: HeaderMap) -> impl IntoResponse {
    // Stateless JWT — actual invalidation is client-side.
    // We validate the token so bots can't spam this endpoint without a valid token.
    if validate_active_access_token(&state.pool, &headers, &state.config.secret_key_raw)
        .await
        .is_none()
    {
        return (
            StatusCode::UNAUTHORIZED,
            Json(serde_json::json!({"detail": "Authentication required"})),
        )
            .into_response();
    }
    (
        StatusCode::OK,
        Json(serde_json::json!({"message": "Successfully logged out"})),
    )
        .into_response()
}

pub async fn get_me(State(state): State<Arc<AppState>>, headers: HeaderMap) -> impl IntoResponse {
    let user_id =
        match validate_active_access_token(&state.pool, &headers, &state.config.secret_key_raw)
            .await
        {
            Some(id) => id,
            None => {
                return (
                    StatusCode::UNAUTHORIZED,
                    Json(serde_json::json!({"detail": "Authentication required"})),
                )
                    .into_response()
            }
        };

    match fetch_user_by_id(&state.pool, user_id).await {
        Some(u) => (StatusCode::OK, Json(UserResponse::from(u))).into_response(),
        None => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"detail": "User not found"})),
        )
            .into_response(),
    }
}

pub async fn update_me(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(req): Json<UpdateMeRequest>,
) -> impl IntoResponse {
    let user_id =
        match validate_active_access_token(&state.pool, &headers, &state.config.secret_key_raw)
            .await
        {
            Some(id) => id,
            None => {
                return (
                    StatusCode::UNAUTHORIZED,
                    Json(serde_json::json!({"detail": "Authentication required"})),
                )
                    .into_response()
            }
        };

    if let Some(ref username) = req.username {
        if username.len() < 3 || username.len() > 100 {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({"detail": "Username must be 3–100 characters"})),
            )
                .into_response();
        }
        let exists: Option<(i32,)> =
            sqlx::query_as("SELECT id FROM users WHERE LOWER(username) = LOWER($1) AND id != $2")
                .bind(username)
                .bind(user_id)
                .fetch_optional(&state.pool)
                .await
                .unwrap_or(None);
        if exists.is_some() {
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({"detail": "Username already taken"})),
            )
                .into_response();
        }
        let _ = sqlx::query("UPDATE users SET username = $1 WHERE id = $2")
            .bind(username)
            .bind(user_id)
            .execute(&state.pool)
            .await;
    }

    if let Some(anon) = req.contribute_anonymously {
        let _ = sqlx::query("UPDATE users SET contribute_anonymously = $1 WHERE id = $2")
            .bind(anon)
            .bind(user_id)
            .execute(&state.pool)
            .await;
    }

    match fetch_user_by_id(&state.pool, user_id).await {
        Some(u) => (StatusCode::OK, Json(UserResponse::from(u))).into_response(),
        None => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"detail": "User not found"})),
        )
            .into_response(),
    }
}

// ─── Email helpers ────────────────────────────────────────────────────────────

async fn subscribe_to_convertkit(
    http: &reqwest::Client,
    api_key: &str,
    form_id: &str,
    email: &str,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let headers = reqwest::header::HeaderMap::from_iter([(
        reqwest::header::HeaderName::from_static("x-kit-api-key"),
        reqwest::header::HeaderValue::from_str(api_key)?,
    )]);
    http.post("https://api.kit.com/v4/subscribers")
        .headers(headers.clone())
        .json(&serde_json::json!({"email_address": email}))
        .send()
        .await?
        .error_for_status()?;
    http.post(format!(
        "https://api.kit.com/v4/forms/{form_id}/subscribers"
    ))
    .headers(headers)
    .json(&serde_json::json!({"email_address": email}))
    .send()
    .await?
    .error_for_status()?;
    Ok(())
}

async fn send_email_verification(
    state: &AppState,
    to_email: &str,
    username: Option<&str>,
    token: &str,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let verify_url = format!("{}/app/verify-email?token={token}", state.config.host_url);
    let app_name = &state.config.addon_name;
    let logo_url = &state.config.logo_url;

    let greeting = match username {
        Some(name) => format!(
            "Thanks for signing up, <strong style=\"color:#fafafa;\">{name}</strong>! \
             Please confirm your email address by clicking the button below."
        ),
        None => {
            "Thanks for signing up! Please confirm your email address by clicking the button below."
                .to_string()
        }
    };
    let html = format!(
        r#"<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Verify your email</title>
</head>
<body style="margin:0;padding:0;background-color:#0a0a0a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#0a0a0a;padding:40px 20px;">
    <tr>
      <td align="center">
        <table role="presentation" width="560" cellpadding="0" cellspacing="0" style="background-color:#141414;border-radius:12px;border:1px solid #262626;overflow:hidden;">
          <tr>
            <td style="padding:32px 40px 24px;text-align:center;border-bottom:1px solid #262626;">
              <img src="{logo_url}" alt="{app_name}" width="48" height="48" style="display:block;margin:0 auto 12px;border-radius:10px;" />
              <h1 style="margin:0;font-size:22px;font-weight:600;color:#fafafa;">{app_name}</h1>
            </td>
          </tr>
          <tr>
            <td style="padding:32px 40px;">
              <h2 style="margin:0 0 16px;font-size:20px;font-weight:600;color:#fafafa;">Verify your email address</h2>
              <p style="margin:0 0 24px;font-size:15px;line-height:1.6;color:#a1a1aa;">{greeting}</p>
              <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 auto 24px;">
                <tr>
                  <td align="center" style="border-radius:8px;background-color:#eab308;">
                    <a href="{verify_url}" target="_blank" style="display:inline-block;padding:12px 32px;font-size:15px;font-weight:600;color:#0a0a0a;text-decoration:none;border-radius:8px;">
                      Verify Email
                    </a>
                  </td>
                </tr>
              </table>
              <p style="margin:0 0 16px;font-size:13px;line-height:1.6;color:#71717a;">
                If the button doesn't work, copy and paste this link into your browser:
              </p>
              <p style="margin:0 0 24px;font-size:13px;line-height:1.6;color:#a1a1aa;word-break:break-all;">
                <a href="{verify_url}" style="color:#eab308;text-decoration:underline;">{verify_url}</a>
              </p>
              <p style="margin:0;font-size:13px;line-height:1.6;color:#71717a;">
                This link expires in 24 hours. If you didn't create an account, you can safely ignore this email.
              </p>
            </td>
          </tr>
          <tr>
            <td style="padding:24px 40px;border-top:1px solid #262626;text-align:center;">
              <p style="margin:0;font-size:12px;color:#52525b;">&copy; {app_name}. This is an automated message.</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"#,
        logo_url = logo_url,
        app_name = app_name,
        greeting = greeting,
        verify_url = verify_url,
    );
    let text = format!(
        "Verify your email address\n\nClick the link below to verify your email:\n{verify_url}\n\nThis link expires in 24 hours."
    );
    send_email(
        state,
        to_email,
        &format!("Verify your email - {app_name}"),
        html,
        text,
    )
    .await
}

async fn send_password_reset_email(
    state: &AppState,
    to_email: &str,
    username: Option<&str>,
    token: &str,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let reset_url = format!("{}/app/reset-password?token={token}", state.config.host_url);
    let app_name = &state.config.addon_name;
    let logo_url = &state.config.logo_url;

    let account_ref = match username {
        Some(name) => format!(" (<strong style=\"color:#fafafa;\">{name}</strong>)"),
        None => String::new(),
    };
    let html = format!(
        r#"<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Reset your password</title>
</head>
<body style="margin:0;padding:0;background-color:#0a0a0a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#0a0a0a;padding:40px 20px;">
    <tr>
      <td align="center">
        <table role="presentation" width="560" cellpadding="0" cellspacing="0" style="background-color:#141414;border-radius:12px;border:1px solid #262626;overflow:hidden;">
          <tr>
            <td style="padding:32px 40px 24px;text-align:center;border-bottom:1px solid #262626;">
              <img src="{logo_url}" alt="{app_name}" width="48" height="48" style="display:block;margin:0 auto 12px;border-radius:10px;" />
              <h1 style="margin:0;font-size:22px;font-weight:600;color:#fafafa;">{app_name}</h1>
            </td>
          </tr>
          <tr>
            <td style="padding:32px 40px;">
              <h2 style="margin:0 0 16px;font-size:20px;font-weight:600;color:#fafafa;">Reset your password</h2>
              <p style="margin:0 0 24px;font-size:15px;line-height:1.6;color:#a1a1aa;">
                We received a request to reset the password for your account{account_ref}. Click the button below to set a new password.
              </p>
              <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 auto 24px;">
                <tr>
                  <td align="center" style="border-radius:8px;background-color:#eab308;">
                    <a href="{reset_url}" target="_blank" style="display:inline-block;padding:12px 32px;font-size:15px;font-weight:600;color:#0a0a0a;text-decoration:none;border-radius:8px;">
                      Reset Password
                    </a>
                  </td>
                </tr>
              </table>
              <p style="margin:0 0 16px;font-size:13px;line-height:1.6;color:#71717a;">
                If the button doesn't work, copy and paste this link into your browser:
              </p>
              <p style="margin:0 0 24px;font-size:13px;line-height:1.6;color:#a1a1aa;word-break:break-all;">
                <a href="{reset_url}" style="color:#eab308;text-decoration:underline;">{reset_url}</a>
              </p>
              <p style="margin:0;font-size:13px;line-height:1.6;color:#71717a;">
                This link expires in 1 hour. If you didn't request a password reset, you can safely ignore this email — your password will not be changed.
              </p>
            </td>
          </tr>
          <tr>
            <td style="padding:24px 40px;border-top:1px solid #262626;text-align:center;">
              <p style="margin:0;font-size:12px;color:#52525b;">&copy; {app_name}. This is an automated message.</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"#,
        logo_url = logo_url,
        app_name = app_name,
        account_ref = account_ref,
        reset_url = reset_url,
    );
    let text = format!(
        "Reset your password\n\nClick the link below to reset your password:\n{reset_url}\n\nThis link expires in 1 hour. If you didn't request a password reset, you can safely ignore this email."
    );
    send_email(
        state,
        to_email,
        &format!("Reset your password - {app_name}"),
        html,
        text,
    )
    .await
}

async fn send_email(
    state: &AppState,
    to: &str,
    subject: &str,
    html: String,
    text: String,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    use lettre::{
        message::MultiPart,
        transport::smtp::{
            authentication::Credentials,
            client::{Tls, TlsParameters},
            AsyncSmtpTransport,
        },
        AsyncTransport, Message, Tokio1Executor,
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
        .multipart(MultiPart::alternative_plain_html(text, html))?;

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
