/// Admin NSFW poster review action endpoint.
///
/// The list of NSFW-flagged items is served by the existing media/blocked endpoint
/// via `?filter=nsfw_flagged`. Only the review action (confirm/clear) is here.
///
/// Routes (prefix /api/v1/admin):
///   PATCH /nsfw-flagged/{id}  → review_nsfw_item  (admin only; {flagged: bool})
use std::sync::Arc;

use axum::{
    Json,
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use chrono::Utc;
use hmac::{Hmac, KeyInit, Mac};
use serde::Deserialize;
use serde_json::{Value, json};
use sha2::Sha256;

use crate::state::AppState;

// ─── Auth helpers (also used by admin_extended) ───────────────────────────────

/// Verify HMAC + expiry. Returns the JWT payload on success.
pub fn extract_token_data(headers: &HeaderMap, secret_key: &str) -> Option<Value> {
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
    let data: Value = serde_json::from_slice(&decoded).ok()?;
    let exp = data["exp"].as_f64()?;
    if exp < Utc::now().timestamp() as f64 {
        return None;
    }
    if data["type"].as_str() != Some("access") {
        return None;
    }
    Some(data)
}

fn validate_admin(headers: &HeaderMap, secret_key: &str) -> bool {
    extract_token_data(headers, secret_key)
        .map(|d| d["role"].as_str() == Some("admin"))
        .unwrap_or(false)
}

fn forbidden() -> axum::response::Response {
    (StatusCode::FORBIDDEN, Json(json!({"detail": "Forbidden"}))).into_response()
}

// ─── Request ─────────────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct ReviewRequest {
    /// true = confirm NSFW (keeps item hidden); false = clear false positive (restores item)
    pub flagged: bool,
}

// ─── Handler ──────────────────────────────────────────────────────────────────

/// PATCH /api/v1/admin/nsfw-flagged/{id}
/// Admin-only. Sets `poster_nsfw_reviewed=true` and `poster_nsfw_flagged` to the
/// requested value.
///
/// Effect on catalog visibility:
///   flagged=true  → item stays hidden from catalog (nsfw_block_fragment filters it)
///   flagged=false → item becomes visible again; scan job will not re-score it
pub async fn review_nsfw_item(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Path(media_id): Path<i32>,
    Json(body): Json<ReviewRequest>,
) -> impl IntoResponse {
    if !validate_admin(&headers, &state.config.secret_key_raw) {
        return forbidden();
    }

    let result = sqlx::query(
        "UPDATE media
         SET poster_nsfw_reviewed = true,
             poster_nsfw_flagged  = $2
         WHERE id = $1",
    )
    .bind(media_id)
    .bind(body.flagged)
    .execute(&state.pool)
    .await;

    match result {
        Ok(r) if r.rows_affected() > 0 => Json(json!({
            "id": media_id,
            "nsfw_flagged": body.flagged,
            "nsfw_reviewed": true,
        }))
        .into_response(),
        Ok(_) => (StatusCode::NOT_FOUND, Json(json!({"detail": "not found"}))).into_response(),
        Err(e) => {
            tracing::error!("review_nsfw_item {media_id}: {e}");
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}
