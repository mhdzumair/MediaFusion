//! Per-user Telegram MTProto session HTTP handlers.

use std::sync::Arc;

use axum::{
    Json,
    extract::State,
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
};
use serde::Deserialize;

use crate::{
    db::types::UserId,
    routes::auth_guard,
    services::telegram_login::{
        LoginPasswordResult, LoginStartResult, LoginVerifyResult, delete_user_session, start_login,
        verify_code, verify_password,
    },
    state::AppState,
};

fn unauthorized() -> Response {
    (
        StatusCode::UNAUTHORIZED,
        Json(serde_json::json!({"error": "Unauthorized"})),
    )
        .into_response()
}

fn bad_request(msg: &str) -> Response {
    (
        StatusCode::BAD_REQUEST,
        Json(serde_json::json!({"error": msg})),
    )
        .into_response()
}

#[derive(Deserialize)]
pub struct StartSessionBody {
    pub phone: String,
}

#[derive(Deserialize)]
pub struct VerifyCodeBody {
    pub code: String,
}

#[derive(Deserialize)]
pub struct VerifyPasswordBody {
    pub password: String,
}

/// GET /api/v1/telegram/session/status
pub async fn get_session_status(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
) -> Response {
    let Some(user_id) =
        auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await
    else {
        return unauthorized();
    };

    let connected =
        crate::db::user_telegram_session::has_session(&state.pool_ro, UserId(user_id)).await;
    let row = if connected {
        crate::db::user_telegram_session::get_session(&state.pool_ro, UserId(user_id)).await
    } else {
        None
    };

    (
        StatusCode::OK,
        Json(serde_json::json!({
            "connected": connected,
            "telegram_account_id": row.as_ref().map(|r| r.telegram_account_id),
            "linked_at": row.as_ref().map(|r| r.created_at),
            "last_used_at": row.as_ref().and_then(|r| r.last_used_at),
            "api_configured": state.telegram_clients.api_configured(),
        })),
    )
        .into_response()
}

/// POST /api/v1/telegram/session/start
pub async fn start_session_login(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(body): Json<StartSessionBody>,
) -> Response {
    let Some(user_id) =
        auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await
    else {
        return unauthorized();
    };

    match start_login(
        &state.telegram_pending_logins,
        &state.config,
        UserId(user_id),
        &body.phone,
    )
    .await
    {
        Ok(LoginStartResult::CodeSent { phone }) => (
            StatusCode::OK,
            Json(serde_json::json!({
                "status": "code_sent",
                "phone": phone,
                "message": "Verification code sent to your Telegram app",
            })),
        )
            .into_response(),
        Err(e) => bad_request(&e),
    }
}

/// POST /api/v1/telegram/session/verify
pub async fn verify_session_code(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(body): Json<VerifyCodeBody>,
) -> Response {
    let Some(user_id) =
        auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await
    else {
        return unauthorized();
    };

    match verify_code(
        &state.telegram_pending_logins,
        &state.pool,
        &state.config,
        UserId(user_id),
        &body.code,
    )
    .await
    {
        Ok(LoginVerifyResult::Completed {
            telegram_account_id,
        }) => {
            state.telegram_clients.invalidate(UserId(user_id)).await;
            crate::bot::notify_session_connected(&state, UserId(user_id), telegram_account_id)
                .await;
            (
                StatusCode::OK,
                Json(serde_json::json!({
                    "status": "connected",
                    "telegram_account_id": telegram_account_id,
                })),
            )
                .into_response()
        }
        Ok(LoginVerifyResult::PasswordRequired { hint }) => (
            StatusCode::OK,
            Json(serde_json::json!({
                "status": "password_required",
                "hint": hint,
            })),
        )
            .into_response(),
        Err(e) => bad_request(&e),
    }
}

/// POST /api/v1/telegram/session/password
pub async fn verify_session_password(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(body): Json<VerifyPasswordBody>,
) -> Response {
    let Some(user_id) =
        auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await
    else {
        return unauthorized();
    };

    match verify_password(
        &state.telegram_pending_logins,
        &state.pool,
        &state.config,
        UserId(user_id),
        &body.password,
    )
    .await
    {
        Ok(LoginPasswordResult::Completed {
            telegram_account_id,
        }) => {
            state.telegram_clients.invalidate(UserId(user_id)).await;
            crate::bot::notify_session_connected(&state, UserId(user_id), telegram_account_id)
                .await;
            (
                StatusCode::OK,
                Json(serde_json::json!({
                    "status": "connected",
                    "telegram_account_id": telegram_account_id,
                })),
            )
                .into_response()
        }
        Err(e) => bad_request(&e),
    }
}

/// DELETE /api/v1/telegram/session
pub async fn delete_session(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    let Some(user_id) =
        auth_guard::validate_active_user(&state.pool, &headers, &state.config.secret_key_raw).await
    else {
        return unauthorized();
    };

    match delete_user_session(
        &state.pool,
        &state.telegram_pending_logins,
        &state.telegram_clients,
        UserId(user_id),
    )
    .await
    {
        Ok(deleted) => (
            StatusCode::OK,
            Json(serde_json::json!({
                "success": deleted,
                "message": if deleted {
                    "Telegram scraping session removed"
                } else {
                    "No Telegram scraping session was stored"
                },
            })),
        )
            .into_response(),
        Err(e) => bad_request(&e),
    }
}
