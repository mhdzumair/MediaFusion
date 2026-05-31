/// Telegram stream playback via MediaFlow Proxy.
///
/// Routes:
///   GET /{secret_str}/telegram/{chat_id}/{message_id}
///   GET /{secret_str}/telegram/stream/{telegram_stream_id}
///
/// Flow:
///   1. Require MediaFlow Proxy config in user's UserData
///   2. Look up TelegramStream from DB (contains file_id)
///   3. Get user's Telegram ID from DB
///   4. Get or create a per-user forward via Bot API sendVideo
///   5. Build MediaFlow Telegram streaming URL
///   6. 302 redirect to MediaFlow
use std::sync::Arc;

use axum::{
    body::Body,
    extract::{Path, Query, State},
    http::{header, StatusCode},
    response::{IntoResponse, Response},
};
use fred::prelude::{Expiration, KeysInterface};
use serde::Deserialize;

use crate::{
    crypto,
    db::{telegram as tg_db, UserId},
    models::user_data::UserData,
    state::AppState,
};

const FORWARD_LOCK_TTL: i64 = 60;

#[derive(Deserialize)]
pub struct ChatMessagePath {
    pub secret_str: String,
    pub chat_id: String,
    pub message_id: i64,
}

#[derive(Deserialize)]
pub struct StreamIdPath {
    pub secret_str: String,
    pub telegram_stream_id: i64,
}

#[derive(Deserialize)]
pub struct PlaybackQuery {
    #[serde(default)]
    pub transcode: bool,
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

pub async fn handler_by_chat_message(
    Path(p): Path<ChatMessagePath>,
    Query(q): Query<PlaybackQuery>,
    State(state): State<Arc<AppState>>,
) -> Response {
    match dispatch_by_chat_message(&state, &p.secret_str, &p.chat_id, p.message_id, q.transcode)
        .await
    {
        Ok(url) => redirect(url),
        Err(e) => error_response(e),
    }
}

pub async fn handler_by_stream_id(
    Path(p): Path<StreamIdPath>,
    Query(q): Query<PlaybackQuery>,
    State(state): State<Arc<AppState>>,
) -> Response {
    match dispatch_by_stream_id(&state, &p.secret_str, p.telegram_stream_id, q.transcode).await {
        Ok(url) => redirect(url),
        Err(e) => error_response(e),
    }
}

// ─── Core logic ───────────────────────────────────────────────────────────────

async fn dispatch_by_chat_message(
    state: &AppState,
    secret_str: &str,
    chat_id: &str,
    message_id: i64,
    transcode: bool,
) -> Result<String, PlaybackError> {
    let user_data = resolve_user_data(state, secret_str).await?;
    let stream = tg_db::fetch_telegram_stream_by_chat_message(&state.pool_ro, chat_id, message_id)
        .await
        .ok_or(PlaybackError::NotFound("Telegram stream not found"))?;
    build_mediaflow_url(state, &user_data, stream, transcode).await
}

async fn dispatch_by_stream_id(
    state: &AppState,
    secret_str: &str,
    telegram_stream_id: i64,
    transcode: bool,
) -> Result<String, PlaybackError> {
    let user_data = resolve_user_data(state, secret_str).await?;
    let stream = tg_db::fetch_telegram_stream_by_id(&state.pool_ro, telegram_stream_id)
        .await
        .ok_or(PlaybackError::NotFound("Telegram stream not found"))?;
    build_mediaflow_url(state, &user_data, stream, transcode).await
}

async fn resolve_user_data(state: &AppState, secret_str: &str) -> Result<UserData, PlaybackError> {
    let raw = crypto::resolve_user_data(
        secret_str,
        &state.config.secret_key,
        &state.pool,
        &state.redis,
    )
    .await;
    Ok(serde_json::from_value(raw).unwrap_or_default())
}

async fn build_mediaflow_url(
    state: &AppState,
    user_data: &UserData,
    stream: tg_db::TelegramStreamRow,
    transcode: bool,
) -> Result<String, PlaybackError> {
    // 1. Require MediaFlow Proxy with Telegram support
    let mfc = user_data
        .mediaflow_config
        .as_ref()
        .ok_or(PlaybackError::NoMediaFlow)?;
    let proxy_url = mfc.proxy_url.as_deref().ok_or(PlaybackError::NoMediaFlow)?;
    let api_password = mfc
        .api_password
        .as_deref()
        .ok_or(PlaybackError::NoMediaFlow)?;

    // 2. Require file_id
    let file_id = stream.file_id.as_deref().ok_or(PlaybackError::NoFileId)?;

    // 3. Require auth user
    let user_id = user_data.user_id.ok_or(PlaybackError::Unauthorized)?;

    // 4. Get or create per-user forward
    let forward = get_or_create_forward(
        state,
        stream.id as i64,
        file_id,
        user_id,
        &stream.stream_name,
    )
    .await?;

    // 5. Build MediaFlow URL
    let endpoint = if let Some(ref fname) = stream.file_name {
        format!("/proxy/telegram/stream/{}", urlencoding::encode(fname))
    } else {
        "/proxy/telegram/stream".to_string()
    };

    let mut params: Vec<(&str, String)> = vec![
        ("api_password", api_password.to_string()),
        ("chat_id", forward.forwarded_chat_id.clone()),
    ];

    // Prefer document_id; fall back to decoding it from file_unique_id
    let document_id = stream.document_id.or_else(|| {
        stream
            .file_unique_id
            .as_deref()
            .and_then(extract_document_id_from_file_id)
    });

    if let Some(doc_id) = document_id {
        params.push(("document_id", doc_id.to_string()));
    }
    if let Some(ref fid) = stream.file_id {
        params.push(("file_id", fid.clone()));
    }
    if let Some(sz) = stream.size {
        params.push(("file_size", sz.to_string()));
    }
    if transcode {
        params.push(("transcode", "true".to_string()));
    }

    let query: String = params
        .iter()
        .map(|(k, v)| format!("{}={}", k, urlencoding::encode(v)))
        .collect::<Vec<_>>()
        .join("&");

    let base = format!(
        "{}/{}{}",
        proxy_url.trim_end_matches('/'),
        endpoint.trim_start_matches('/'),
        if query.is_empty() {
            String::new()
        } else {
            format!("?{query}")
        }
    );

    Ok(base)
}

/// Get an existing TelegramUserForward or create one via Bot API sendVideo.
async fn get_or_create_forward(
    state: &AppState,
    telegram_stream_id: i64,
    file_id: &str,
    user_id: UserId,
    stream_name: &Option<String>,
) -> Result<tg_db::TelegramUserForwardRow, PlaybackError> {
    // Fast path: existing record
    if let Some(fwd) =
        tg_db::get_telegram_user_forward(&state.pool_ro, telegram_stream_id, user_id).await
    {
        return Ok(fwd);
    }

    // Require bot token
    let bot_token = state
        .config
        .telegram_bot_token
        .as_deref()
        .ok_or(PlaybackError::BotNotConfigured)?;

    // Get user's Telegram ID
    let telegram_user_id = tg_db::get_user_telegram_id(&state.pool_ro, user_id)
        .await
        .ok_or(PlaybackError::NoTelegramLink)?;

    // Acquire Redis lock to prevent duplicate sends
    let lock_key = format!("telegram_forward:{telegram_stream_id}:{user_id}");
    let lock_acquired = state
        .redis
        .set::<bool, _, _>(
            &lock_key,
            true,
            Some(Expiration::EX(FORWARD_LOCK_TTL)),
            None,
            true,
        )
        .await
        .unwrap_or(false);

    if !lock_acquired {
        // Another request is in flight — wait briefly and try DB again
        tokio::time::sleep(std::time::Duration::from_secs(2)).await;
        if let Some(fwd) =
            tg_db::get_telegram_user_forward(&state.pool_ro, telegram_stream_id, user_id).await
        {
            return Ok(fwd);
        }
        return Err(PlaybackError::LockTimeout);
    }

    // Double-check after acquiring lock
    if let Some(fwd) =
        tg_db::get_telegram_user_forward(&state.pool_ro, telegram_stream_id, user_id).await
    {
        let _ = state.redis.del::<(), _>(&lock_key).await;
        return Ok(fwd);
    }

    // Send video via Bot API
    let caption = stream_name.as_deref().map(|n| format!("🎬 {n}"));
    let send_result = send_video_to_user(
        &state.http,
        bot_token,
        telegram_user_id,
        file_id,
        caption.as_deref(),
    )
    .await;

    let _ = state.redis.del::<(), _>(&lock_key).await;

    let (forwarded_chat_id, forwarded_message_id) = send_result?;

    // Persist the forward
    tg_db::create_telegram_user_forward(
        &state.pool,
        telegram_stream_id,
        user_id,
        telegram_user_id,
        &forwarded_chat_id,
        forwarded_message_id,
    )
    .await
    .map_err(|e| {
        tracing::warn!("create_telegram_user_forward: {e}");
        PlaybackError::DbError
    })
}

/// Call Bot API sendVideo and return (forwarded_chat_id, forwarded_message_id).
async fn send_video_to_user(
    http: &reqwest::Client,
    bot_token: &str,
    telegram_user_id: i64,
    file_id: &str,
    caption: Option<&str>,
) -> Result<(String, i64), PlaybackError> {
    let url = format!("https://api.telegram.org/bot{bot_token}/sendVideo");

    let mut body = serde_json::json!({
        "chat_id": telegram_user_id,
        "video": file_id,
        "supports_streaming": true,
    });
    if let Some(cap) = caption {
        body["caption"] = serde_json::Value::String(cap.to_string());
    }

    let resp = http
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(|_| PlaybackError::BotApiError("sendVideo network error"))?;

    let json: serde_json::Value = resp
        .json()
        .await
        .map_err(|_| PlaybackError::BotApiError("sendVideo invalid JSON"))?;

    if !json["ok"].as_bool().unwrap_or(false) {
        let desc = json["description"].as_str().unwrap_or("unknown");
        tracing::warn!("sendVideo failed: {desc}");
        return Err(PlaybackError::BotSendFailed(desc.to_string()));
    }

    let message_id = json["result"]["message_id"]
        .as_i64()
        .ok_or(PlaybackError::BotApiError("sendVideo: no message_id"))?;
    let chat_id = json["result"]["chat"]["id"]
        .as_i64()
        .ok_or(PlaybackError::BotApiError("sendVideo: no chat.id"))?;

    Ok((chat_id.to_string(), message_id))
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

/// Decode the `document_id` embedded in a Telegram `file_unique_id`.
/// Telegram encodes it as: base64url(type_byte || little-endian-i64 || ...).
fn extract_document_id_from_file_id(file_unique_id: &str) -> Option<i64> {
    use base64::{engine::general_purpose::URL_SAFE, Engine};
    // file_unique_id may lack padding — pad to multiple of 4
    let padded = match file_unique_id.len() % 4 {
        2 => format!("{file_unique_id}=="),
        3 => format!("{file_unique_id}="),
        _ => file_unique_id.to_string(),
    };
    let decoded = URL_SAFE.decode(&padded).ok()?;
    if decoded.len() < 9 {
        return None;
    }
    let arr: [u8; 8] = decoded[1..9].try_into().ok()?;
    Some(i64::from_le_bytes(arr))
}

fn redirect(url: String) -> Response {
    Response::builder()
        .status(StatusCode::FOUND)
        .header(header::LOCATION, url)
        .header(header::CACHE_CONTROL, "no-store, no-cache, must-revalidate")
        .body(Body::empty())
        .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
}

fn error_response(e: PlaybackError) -> Response {
    let (status, msg) = e.to_http();
    tracing::warn!("telegram_playback error: {msg}");
    Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(format!(r#"{{"error":"{msg}"}}"#)))
        .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
}

// ─── Error type ───────────────────────────────────────────────────────────────

enum PlaybackError {
    NoMediaFlow,
    NoFileId,
    Unauthorized,
    NotFound(&'static str),
    BotNotConfigured,
    NoTelegramLink,
    LockTimeout,
    BotApiError(&'static str),
    BotSendFailed(String),
    DbError,
}

impl PlaybackError {
    fn to_http(&self) -> (StatusCode, String) {
        match self {
            Self::NoMediaFlow => (StatusCode::BAD_REQUEST,
                "MediaFlow Proxy with Telegram support is required. Configure MediaFlow in your profile.".into()),
            Self::NoFileId => (StatusCode::BAD_REQUEST, "Telegram stream has no file_id.".into()),
            Self::Unauthorized => (StatusCode::UNAUTHORIZED, "Authentication required.".into()),
            Self::NotFound(msg) => (StatusCode::NOT_FOUND, msg.to_string()),
            Self::BotNotConfigured => (StatusCode::BAD_GATEWAY, "Telegram bot not configured on server.".into()),
            Self::NoTelegramLink => (StatusCode::BAD_REQUEST,
                "Link your Telegram account first. Send /login to the MediaFusion bot.".into()),
            Self::LockTimeout => (StatusCode::TOO_MANY_REQUESTS, "Too many requests. Try again shortly.".into()),
            Self::BotApiError(msg) => (StatusCode::BAD_GATEWAY, msg.to_string()),
            Self::BotSendFailed(msg) => (StatusCode::BAD_GATEWAY,
                format!("Failed to send video to your Telegram: {msg}. Make sure you've started the bot with /start.")),
            Self::DbError => (StatusCode::INTERNAL_SERVER_ERROR, "Database error.".into()),
        }
    }
}
