/// Telegram bot webhook receiver.
///
/// Route: POST /api/v1/telegram/webhook
///
/// Security: validates X-Telegram-Bot-Api-Secret-Token header when
/// TELEGRAM_WEBHOOK_SECRET_TOKEN is configured.
use std::sync::Arc;

use axum::{
    body::Bytes,
    extract::{Request, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
};

use crate::{bot, state::AppState};

pub async fn handler(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    req: Request,
) -> Response {
    if state.config.telegram_bot_token.is_none() {
        return (
            StatusCode::BAD_REQUEST,
            r#"{"ok":false,"detail":"Telegram bot not configured"}"#,
        )
            .into_response();
    }

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

    let body = match axum::body::to_bytes(req.into_body(), 1024 * 1024).await {
        Ok(b) => b,
        Err(e) => {
            tracing::warn!("telegram webhook body read: {e}");
            return (StatusCode::BAD_REQUEST, r#"{"ok":false}"#).into_response();
        }
    };

    let update: bot::Update = match serde_json::from_slice(&body) {
        Ok(u) => u,
        Err(e) => {
            tracing::warn!("telegram webhook parse: {e}");
            return (StatusCode::OK, r#"{"ok":true}"#).into_response();
        }
    };

    let state_clone = Arc::clone(&state);
    tokio::spawn(async move {
        bot::dispatch_update(state_clone, update).await;
    });

    (StatusCode::OK, r#"{"ok":true}"#).into_response()
}
