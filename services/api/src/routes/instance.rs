/// Instance information endpoints.
///
/// Routes:
///   GET /api/v1/instance/info           → get_instance_info
///   GET /api/v1/instance/app-config     → get_app_config
///   GET /api/v1/instance/constants      → get_system_constants
///   POST /api/v1/instance/setup/create-admin → create_initial_admin

use std::sync::Arc;

use axum::{
    extract::State,
    http::StatusCode,
    response::IntoResponse,
    Json,
};
use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::state::AppState;

// ─── Response types ───────────────────────────────────────────────────────────

#[derive(Serialize)]
pub struct NewsletterConfig {
    pub enabled: bool,
    pub label: String,
    pub default_checked: bool,
}

#[derive(Serialize)]
pub struct InstanceInfo {
    pub is_public: bool,
    pub requires_api_key: bool,
    pub setup_required: bool,
    pub addon_name: String,
    pub version: String,
    pub logo_url: String,
}

#[derive(Serialize)]
pub struct TelegramFeatureConfig {
    pub enabled: bool,
    pub bot_configured: bool,
    pub bot_username: Option<String>,
    pub scraping_enabled: bool,
}

#[derive(Serialize)]
pub struct AppConfigResponse {
    pub addon_name: String,
    pub logo_url: String,
    pub host_url: String,
    pub version: String,
    pub is_public_instance: bool,
    pub contact_email: Option<String>,
    pub authentication_required: bool,
    pub telegram: TelegramFeatureConfig,
}

// ─── Request types ────────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct CreateAdminRequest {
    pub api_password: String,
    pub email: String,
    pub username: String,
    pub password: String,
}

// ─── Handlers ────────────────────────────────────────────────────────────────

/// GET /api/v1/instance/info
pub async fn get_instance_info(
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    // Check if setup is required (no users exist)
    let setup_required: bool = sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM users")
        .fetch_one(&state.pool_ro)
        .await
        .map(|c| c == 0)
        .unwrap_or(false);

    let is_public = state.config.api_password.is_none();

    Json(InstanceInfo {
        is_public,
        requires_api_key: !is_public,
        setup_required,
        addon_name: state.config.addon_name.clone(),
        version: state.config.addon_version.clone(),
        logo_url: state.config.logo_url.clone(),
    })
    .into_response()
}

/// GET /api/v1/instance/app-config
pub async fn get_app_config(
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    let is_public = state.config.api_password.is_none();
    let telegram_enabled = state.config.telegram_api_id.is_some();
    let bot_configured = state.config.telegram_bot_token.is_some();

    Json(AppConfigResponse {
        addon_name: state.config.addon_name.clone(),
        logo_url: state.config.logo_url.clone(),
        host_url: state.config.host_url.clone(),
        version: state.config.addon_version.clone(),
        is_public_instance: is_public,
        contact_email: state.config.contact_email.clone(),
        authentication_required: !is_public,
        telegram: TelegramFeatureConfig {
            enabled: telegram_enabled,
            bot_configured,
            bot_username: state.config.telegram_bot_username.clone(),
            scraping_enabled: telegram_enabled,
        },
    })
    .into_response()
}

/// GET /api/v1/instance/constants
pub async fn get_system_constants() -> impl IntoResponse {
    // Return a minimal constants structure; the full constant tables live in Python.
    Json(json!({
        "CATALOG_DATA": [],
        "RESOLUTIONS": ["4K", "2160p", "1440p", "1080p", "720p", "480p", "360p"],
        "SUPPORTED_LANGUAGES": [],
    }))
    .into_response()
}

/// POST /api/v1/instance/setup/create-admin
pub async fn create_initial_admin(
    State(state): State<Arc<AppState>>,
    Json(body): Json<CreateAdminRequest>,
) -> impl IntoResponse {
    // Validate API password
    match &state.config.api_password {
        Some(pw) if pw == &body.api_password => {}
        _ => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Invalid API password."})),
            )
                .into_response();
        }
    }

    // Verify setup is actually required
    let user_count: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM users")
        .fetch_one(&state.pool)
        .await
        .unwrap_or(0);

    if user_count > 0 {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Setup has already been completed."})),
        )
            .into_response();
    }

    // Check email uniqueness
    let email_exists: bool = sqlx::query_scalar::<_, i64>(
        "SELECT COUNT(*) FROM users WHERE email = $1",
    )
    .bind(&body.email)
    .fetch_one(&state.pool)
    .await
    .map(|c| c > 0)
    .unwrap_or(false);

    if email_exists {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Email already registered."})),
        )
            .into_response();
    }

    // Check username uniqueness
    let username_exists: bool = sqlx::query_scalar::<_, i64>(
        "SELECT COUNT(*) FROM users WHERE username = $1",
    )
    .bind(&body.username)
    .fetch_one(&state.pool)
    .await
    .map(|c| c > 0)
    .unwrap_or(false);

    if username_exists {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Username already taken."})),
        )
            .into_response();
    }

    // Hash password using SHA-256 + random salt (same scheme as auth.rs)
    let salt = {
        use rand_core::{OsRng, RngCore};
        let mut b = [0u8; 16];
        OsRng.fill_bytes(&mut b);
        b.iter().map(|x| format!("{x:02x}")).collect::<String>()
    };
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(format!("{}{}", body.password, salt));
    let digest = hasher.finalize().iter().map(|x| format!("{x:02x}")).collect::<String>();
    let password_hash = format!("{salt}${digest}");

    // Create admin user
    let user_id: i64 = match sqlx::query_scalar(
        r#"INSERT INTO users (email, username, password_hash, role, is_verified, is_active, created_at, last_login)
           VALUES ($1, $2, $3, 'admin', true, true, NOW(), NOW())
           RETURNING id"#,
    )
    .bind(&body.email)
    .bind(&body.username)
    .bind(&password_hash)
    .fetch_one(&state.pool)
    .await
    {
        Ok(id) => id,
        Err(e) => {
            tracing::error!("create_initial_admin insert user: {e}");
            return StatusCode::INTERNAL_SERVER_ERROR.into_response();
        }
    };

    // Create default profile
    if let Err(e) = sqlx::query(
        r#"INSERT INTO user_profiles (user_id, name, config, is_default) VALUES ($1, 'Default', '{}', true)"#,
    )
    .bind(user_id)
    .execute(&state.pool)
    .await
    {
        tracing::error!("create_initial_admin profile: {e}");
    }

    (
        StatusCode::CREATED,
        Json(json!({
            "detail": "Admin account created successfully.",
            "user_id": user_id,
            "email": body.email,
            "username": body.username,
            "role": "admin",
        })),
    )
        .into_response()
}
