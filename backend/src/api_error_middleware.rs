use axum::{
    extract::Request,
    http::StatusCode,
    middleware::Next,
    response::{IntoResponse, Json, Response},
};
use serde_json::json;

/// Wraps all /api/v1/* error responses (4xx/5xx) as HTTP 200 with an error
/// envelope, mirroring the Python exception_handlers behaviour.
///
/// Non-API paths (Stremio protocol, health, static) are passed through unchanged.
pub async fn api_error_middleware(request: Request, next: Next) -> Response {
    let path = request.uri().path().to_string();
    let response = next.run(request).await;

    if !path.starts_with("/api/v1/") {
        return response;
    }

    let status = response.status();
    if status.is_success() || status.is_redirection() {
        return response;
    }

    // Preserve original status code for the envelope body, then convert to 200.
    let status_code = status.as_u16();

    let bytes = match axum::body::to_bytes(response.into_body(), 1024 * 1024).await {
        Ok(b) => b,
        Err(_) => {
            return error_response(
                status.canonical_reason().unwrap_or("unknown error").to_string(),
                status_code,
            );
        }
    };

    let detail = extract_detail(&bytes, status);
    error_response(detail, status_code)
}

fn extract_detail(bytes: &[u8], status: StatusCode) -> String {
    let fallback = || {
        status
            .canonical_reason()
            .unwrap_or("unknown error")
            .to_string()
    };

    let Ok(value) = serde_json::from_slice::<serde_json::Value>(bytes) else {
        let raw = String::from_utf8_lossy(bytes);
        let trimmed = raw.trim();
        return if trimmed.is_empty() {
            fallback()
        } else {
            trimmed.chars().take(500).collect()
        };
    };

    // Handle {"error": "string"} from AppError::into_response
    if let Some(s) = value.get("error").and_then(|v| v.as_str()) {
        return s.to_string();
    }
    // Handle {"detail": "string"} from api_key_middleware / validation errors
    if let Some(s) = value.get("detail").and_then(|v| v.as_str()) {
        return s.to_string();
    }
    // Handle {"message": "string"}
    if let Some(s) = value.get("message").and_then(|v| v.as_str()) {
        return s.to_string();
    }

    fallback()
}

fn error_response(detail: String, status_code: u16) -> Response {
    (
        StatusCode::OK,
        Json(json!({
            "error": true,
            "detail": detail,
            "status_code": status_code
        })),
    )
        .into_response()
}
