/// Poster image upload endpoints.
///
/// Routes (prefix /api/v1/import):
///   POST /images/upload   → upload_image
///   GET  /images/{key}    → get_uploaded_image
use std::sync::Arc;

use axum::{
    extract::{Multipart, Path, State},
    http::{HeaderMap, HeaderValue, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::Utc;
use hmac::{Hmac, KeyInit, Mac};
use serde_json::json;
use sha2::Sha256;
use uuid::Uuid;

use crate::state::AppState;

// ─── Auth ─────────────────────────────────────────────────────────────────────

fn validate_token(headers: &HeaderMap, secret_key: &str) -> Option<i64> {
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

// ─── Magic-byte content type detection ───────────────────────────────────────

fn detect_content_type(bytes: &[u8]) -> (&'static str, &'static str) {
    if bytes.len() >= 4
        && bytes[0] == 0x89
        && bytes[1] == 0x50
        && bytes[2] == 0x4e
        && bytes[3] == 0x47
    {
        ("image/png", "png")
    } else if bytes.len() >= 2 && bytes[0] == 0xff && bytes[1] == 0xd8 {
        ("image/jpeg", "jpg")
    } else if bytes.len() >= 12 && &bytes[8..12] == b"WEBP" {
        ("image/webp", "webp")
    } else if bytes.len() >= 4 && (&bytes[0..4] == b"GIF8") {
        ("image/gif", "gif")
    } else {
        ("application/octet-stream", "bin")
    }
}

fn content_type_from_ext(ext: &str) -> &'static str {
    match ext {
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "webp" => "image/webp",
        "gif" => "image/gif",
        _ => "application/octet-stream",
    }
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// POST /api/v1/import/images/upload
pub async fn upload_image(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    mut multipart: Multipart,
) -> Response {
    if validate_token(&headers, &state.config.secret_key_raw).is_none() {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response();
    }

    if !state.config.image_upload_enabled {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Image upload is not enabled on this server."})),
        )
            .into_response();
    }

    // Read image field from multipart
    let mut image_bytes: Option<Vec<u8>> = None;
    while let Ok(Some(field)) = multipart.next_field().await {
        if field.name() == Some("image") || image_bytes.is_none() {
            match field.bytes().await {
                Ok(data) if !data.is_empty() => {
                    image_bytes = Some(data.to_vec());
                    break;
                }
                _ => continue,
            }
        }
    }

    let data = match image_bytes {
        Some(b) => b,
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"detail": "No image field found in multipart body"})),
            )
                .into_response();
        }
    };

    let (content_type, ext) = detect_content_type(&data);
    let key = format!("{}.{}", Uuid::new_v4(), ext);
    let images_dir = &state.config.images_dir;
    let file_path = format!("{images_dir}/{key}");

    // Create parent directory if needed
    if let Err(e) = tokio::fs::create_dir_all(images_dir).await {
        tracing::error!("upload_image: create_dir_all {images_dir}: {e}");
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"detail": "Failed to create image storage directory"})),
        )
            .into_response();
    }

    let size = data.len();
    if let Err(e) = tokio::fs::write(&file_path, &data).await {
        tracing::error!("upload_image: write {file_path}: {e}");
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"detail": "Failed to save image"})),
        )
            .into_response();
    }

    Json(json!({
        "url": format!("/api/v1/import/images/{key}"),
        "key": key,
        "content_type": content_type,
        "size": size,
    }))
    .into_response()
}

/// GET /api/v1/import/images/{key}
pub async fn get_uploaded_image(
    State(state): State<Arc<AppState>>,
    Path(key): Path<String>,
) -> Response {
    if !state.config.image_upload_enabled {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"detail": "Image upload is not enabled on this server."})),
        )
            .into_response();
    }

    // Validate key — only alphanumeric, hyphens, and dots (no path traversal)
    if !key
        .chars()
        .all(|c| c.is_alphanumeric() || c == '-' || c == '.')
    {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail": "Invalid image key"})),
        )
            .into_response();
    }

    let file_path = format!("{}/{key}", state.config.images_dir);
    let data = match tokio::fs::read(&file_path).await {
        Ok(d) => d,
        Err(_) => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"detail": "Image not found"})),
            )
                .into_response();
        }
    };

    // Determine content type from extension
    let ext = key.rsplit('.').next().unwrap_or("");
    let content_type = content_type_from_ext(ext);

    let mut response = axum::response::Response::builder()
        .status(StatusCode::OK)
        .header("Content-Type", content_type)
        .header(
            "Cache-Control",
            HeaderValue::from_static("public, max-age=31536000"),
        )
        .body(axum::body::Body::from(data))
        .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response());

    // Suppress unused variable warning from the builder pattern
    let _ = &mut response;
    response
}
