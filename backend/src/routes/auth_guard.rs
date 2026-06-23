//! Shared JWT validation helpers for role-gated routes.

use axum::http::{HeaderMap, StatusCode};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use chrono::Utc;
use hmac::{Hmac, KeyInit, Mac};
use sha2::Sha256;
use sqlx::PgPool;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AuthFailure {
    Unauthorized,
    Forbidden,
}

pub fn bearer_token(headers: &HeaderMap) -> Option<&str> {
    headers
        .get("authorization")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "))
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

/// Decode a valid access token and return `(user_id, role)`.
pub fn decode_access_token(
    headers: &HeaderMap,
    secret_key: &str,
) -> Result<(i32, String), AuthFailure> {
    let token = bearer_token(headers).ok_or(AuthFailure::Unauthorized)?;
    let dot = token.rfind('.').ok_or(AuthFailure::Unauthorized)?;
    let (payload_str, sig) = token.split_at(dot);
    let sig = &sig[1..];

    let mut mac = Hmac::<Sha256>::new_from_slice(secret_key.as_bytes())
        .map_err(|_| AuthFailure::Unauthorized)?;
    mac.update(payload_str.as_bytes());
    let expected: String = mac
        .finalize()
        .into_bytes()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    if !constant_time_eq_hex(&expected, sig) {
        return Err(AuthFailure::Unauthorized);
    }

    let decoded = URL_SAFE_NO_PAD
        .decode(payload_str.trim_end_matches('='))
        .map_err(|_| AuthFailure::Unauthorized)?;
    let data: serde_json::Value =
        serde_json::from_slice(&decoded).map_err(|_| AuthFailure::Unauthorized)?;

    let exp = data["exp"].as_f64().ok_or(AuthFailure::Unauthorized)?;
    if exp < Utc::now().timestamp() as f64 {
        return Err(AuthFailure::Unauthorized);
    }
    if data["type"].as_str() != Some("access") {
        return Err(AuthFailure::Unauthorized);
    }

    let user_id: i32 = data["sub"]
        .as_str()
        .and_then(|s| s.parse().ok())
        .ok_or(AuthFailure::Unauthorized)?;
    let role = data["role"].as_str().unwrap_or("user").to_string();
    Ok((user_id, role))
}

async fn user_is_active(pool: &PgPool, user_id: i32) -> Result<bool, AuthFailure> {
    let active: Option<bool> = sqlx::query_scalar("SELECT is_active FROM users WHERE id = $1")
        .bind(user_id)
        .fetch_optional(pool)
        .await
        .map_err(|_| AuthFailure::Unauthorized)?;
    Ok(active == Some(true))
}

/// Validate access token signature/expiry and ensure the account is still active.
pub async fn validate_active_user(
    pool: &PgPool,
    headers: &HeaderMap,
    secret_key: &str,
) -> Option<i32> {
    let (user_id, _) = decode_access_token(headers, secret_key).ok()?;
    user_is_active(pool, user_id).await.ok()?.then_some(user_id)
}

pub fn require_role(
    headers: &HeaderMap,
    secret_key: &str,
    allowed: &[&str],
) -> Result<i32, AuthFailure> {
    let (user_id, role) = decode_access_token(headers, secret_key)?;
    if allowed.iter().any(|r| *r == role) {
        Ok(user_id)
    } else {
        Err(AuthFailure::Forbidden)
    }
}

/// Role gate with live `is_active` DB check (mirrors Python `require_auth`).
pub async fn require_active_role(
    pool: &PgPool,
    headers: &HeaderMap,
    secret_key: &str,
    allowed: &[&str],
) -> Result<i32, AuthFailure> {
    let (user_id, role) = decode_access_token(headers, secret_key)?;
    if !allowed.iter().any(|r| *r == role) {
        return Err(AuthFailure::Forbidden);
    }
    if !user_is_active(pool, user_id).await? {
        return Err(AuthFailure::Unauthorized);
    }
    Ok(user_id)
}

pub fn auth_failure_response(failure: AuthFailure) -> (StatusCode, axum::Json<serde_json::Value>) {
    match failure {
        AuthFailure::Unauthorized => (
            StatusCode::UNAUTHORIZED,
            axum::Json(serde_json::json!({"error": "Unauthorized"})),
        ),
        AuthFailure::Forbidden => (
            StatusCode::FORBIDDEN,
            axum::Json(serde_json::json!({"error": "Forbidden"})),
        ),
    }
}
