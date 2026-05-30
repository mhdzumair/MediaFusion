/// Content stream management endpoints.
///
/// Routes (prefix /api/v1/streams):
///   DELETE /{stream_id}  → delete_stream  (moderator required)
use std::sync::Arc;

use axum::{
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::Utc;
use hmac::{Hmac, KeyInit, Mac};
use serde_json::json;
use sha2::Sha256;

use crate::state::AppState;

// ─── Auth helpers ─────────────────────────────────────────────────────────────

fn validate_moderator_token(headers: &HeaderMap, secret_key: &str) -> Option<i32> {
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
    let role = data["role"].as_str()?;
    if role != "moderator" && role != "admin" {
        return None;
    }
    data["sub"].as_str()?.parse().ok()
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

pub async fn delete_stream(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(stream_id): Path<i32>,
) -> Response {
    // 1. Validate moderator token
    let _user_id = match validate_moderator_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::FORBIDDEN,
                Json(json!({"detail": "Moderator role required"})),
            )
                .into_response();
        }
    };

    let stream_id_i32 = stream_id;

    // 2. Check stream exists and get stream_type
    let stream_type: Option<String> =
        match sqlx::query_scalar("SELECT stream_type::text FROM stream WHERE id = $1")
            .bind(stream_id_i32)
            .fetch_optional(&state.pool)
            .await
        {
            Ok(v) => v,
            Err(e) => {
                tracing::error!("DB error checking stream {stream_id}: {e}");
                return (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({"error": "Database error"})),
                )
                    .into_response();
            }
        };

    let stream_type = match stream_type {
        Some(t) => t,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"error": "Stream not found"})),
            )
                .into_response();
        }
    };

    // 3. Begin transaction
    let mut txn = match state.pool.begin().await {
        Ok(t) => t,
        Err(e) => {
            tracing::error!("DB error starting transaction: {e}");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "Database error"})),
            )
                .into_response();
        }
    };

    // 4. Fetch linked media IDs, then decrement total_streams
    let media_ids: Vec<i32> =
        match sqlx::query_scalar("SELECT media_id FROM stream_media_link WHERE stream_id = $1")
            .bind(stream_id_i32)
            .fetch_all(&mut *txn)
            .await
        {
            Ok(v) => v,
            Err(e) => {
                tracing::error!("DB error fetching media links for stream {stream_id}: {e}");
                return (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({"error": "Database error"})),
                )
                    .into_response();
            }
        };

    for media_id in &media_ids {
        if let Err(e) = sqlx::query(
            "UPDATE media SET total_streams = GREATEST(total_streams - 1, 0) WHERE id = $1",
        )
        .bind(media_id)
        .execute(&mut *txn)
        .await
        {
            tracing::warn!("Failed to decrement total_streams for media {media_id}: {e}");
        }
    }

    // 5. Delete stream_media_link rows
    if let Err(e) = sqlx::query("DELETE FROM stream_media_link WHERE stream_id = $1")
        .bind(stream_id_i32)
        .execute(&mut *txn)
        .await
    {
        tracing::error!("DB error deleting stream_media_link for stream {stream_id}: {e}");
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "Database error"})),
        )
            .into_response();
    }

    // 6. Delete playback_tracking rows
    if let Err(e) = sqlx::query("DELETE FROM playback_tracking WHERE stream_id = $1")
        .bind(stream_id_i32)
        .execute(&mut *txn)
        .await
    {
        tracing::warn!("Failed to delete playback_tracking for stream {stream_id}: {e}");
    }

    // 7. Delete type-specific row
    let type_table = match stream_type.as_str() {
        "torrent" => Some("torrent_stream"),
        "http" => Some("http_stream"),
        "youtube" => Some("youtube_stream"),
        "usenet" => Some("usenet_stream"),
        "telegram" => Some("telegram_stream"),
        "external_link" => Some("external_link_stream"),
        "acestream" => Some("acestream_stream"),
        _ => None,
    };

    if let Some(table) = type_table {
        let sql = format!("DELETE FROM {table} WHERE stream_id = $1");
        if let Err(e) = sqlx::query(&sql)
            .bind(stream_id_i32)
            .execute(&mut *txn)
            .await
        {
            tracing::warn!("Failed to delete from {table} for stream {stream_id}: {e}");
        }
    }

    // 8. Delete stream_votes
    if let Err(e) = sqlx::query("DELETE FROM stream_votes WHERE stream_id = $1")
        .bind(stream_id_i32)
        .execute(&mut *txn)
        .await
    {
        tracing::warn!("Failed to delete stream_votes for stream {stream_id}: {e}");
    }

    // 9. Delete from stream table
    if let Err(e) = sqlx::query("DELETE FROM stream WHERE id = $1")
        .bind(stream_id_i32)
        .execute(&mut *txn)
        .await
    {
        tracing::error!("DB error deleting stream {stream_id}: {e}");
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "Database error"})),
        )
            .into_response();
    }

    // 10. Commit transaction
    if let Err(e) = txn.commit().await {
        tracing::error!("DB error committing transaction for stream {stream_id}: {e}");
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "Database error"})),
        )
            .into_response();
    }

    // 11. Return success
    (
        StatusCode::OK,
        Json(json!({"message": format!("{stream_type} stream deleted successfully")})),
    )
        .into_response()
}
