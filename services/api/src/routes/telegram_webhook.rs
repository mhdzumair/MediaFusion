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
    body::Body,
    extract::{Request, State},
    http::{header, HeaderMap, StatusCode},
    response::{IntoResponse, Response},
};
use axum::body::to_bytes;

use crate::state::AppState;

pub async fn handler(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    req: Request,
) -> Response {
    // Require bot token
    if state.config.telegram_bot_token.is_none() {
        return (StatusCode::BAD_REQUEST, r#"{"ok":false,"detail":"Telegram bot not configured"}"#)
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
            return (StatusCode::FORBIDDEN, r#"{"ok":false,"detail":"Invalid secret token"}"#)
                .into_response();
        }
    }

    // If a Python proxy is configured, forward the full body there.
    // This lets the Python service handle the full bot wizard logic while
    // Rust owns the HTTP layer.
    if let Some(python_url) = &state.config.python_proxy_url {
        let target = format!("{python_url}/api/v1/telegram/webhook");

        // Read body
        let body_bytes = match to_bytes(req.into_body(), 4 * 1024 * 1024).await {
            Ok(b) => b,
            Err(_) => {
                return (StatusCode::BAD_REQUEST, r#"{"ok":false}"#).into_response();
            }
        };

        let mut forward_req = state.http
            .post(&target)
            .header(header::CONTENT_TYPE, "application/json")
            .body(body_bytes.to_vec());

        // Forward the secret token header so Python can re-validate if needed
        if let Some(token) = headers.get("x-telegram-bot-api-secret-token") {
            if let Ok(v) = token.to_str() {
                forward_req = forward_req.header("x-telegram-bot-api-secret-token", v);
            }
        }

        match forward_req.send().await {
            Ok(resp) => {
                let status = resp.status();
                let body = resp.bytes().await.unwrap_or_default();
                Response::builder()
                    .status(status.as_u16())
                    .header(header::CONTENT_TYPE, "application/json")
                    .body(Body::from(body))
                    .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
            }
            Err(e) => {
                tracing::error!("telegram webhook proxy error: {e}");
                (StatusCode::BAD_GATEWAY, r#"{"ok":false}"#).into_response()
            }
        }
    } else {
        // Standalone mode — acknowledge without processing
        (StatusCode::OK, r#"{"ok":true}"#).into_response()
    }
}
