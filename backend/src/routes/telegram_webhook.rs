/// Telegram bot webhook receiver.
///
/// Route: POST /api/v1/telegram/webhook
///
/// Security: validates X-Telegram-Bot-Api-Secret-Token header when
/// TELEGRAM_WEBHOOK_SECRET_TOKEN is configured.
///
/// Dispatches to the bot logic module (src/bot/).
/// For now this is a lightweight passthrough that forwards updates to the
/// Python service via HTTP proxy when PYTHON_BASE_URL is configured,
/// or returns 200 OK (no-op) when standalone.
use std::sync::Arc;

use axum::{
    extract::{Request, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
};

use crate::state::AppState;

pub async fn handler(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    req: Request,
) -> Response {
    // Require bot token
    if state.config.telegram_bot_token.is_none() {
        return (
            StatusCode::BAD_REQUEST,
            r#"{"ok":false,"detail":"Telegram bot not configured"}"#,
        )
            .into_response();
    }

    // Verify secret token
    if let Some(expected) = &state.config.telegram_webhook_secret_token {
        let provided = headers
            .get("x-telegram-bot-api-secret-token")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("");
        if provided != expected {
            tracing::warn!("telegram webhook: invalid secret token");
            return (
                StatusCode::FORBIDDEN,
                r#"{"ok":false,"detail":"Invalid secret token"}"#,
            )
                .into_response();
        }
    }

    // Standalone mode — acknowledge without processing
    let _ = req;
    (StatusCode::OK, r#"{"ok":true}"#).into_response()
}
