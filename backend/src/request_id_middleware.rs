//! Assigns an `X-Request-Id` correlation header to every request.

use axum::{
    extract::Request,
    http::{HeaderValue, header},
    middleware::Next,
    response::Response,
};
use uuid::Uuid;

pub async fn request_id_middleware(mut request: Request, next: Next) -> Response {
    let req_id = request
        .headers()
        .get("x-request-id")
        .and_then(|v| v.to_str().ok())
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| Uuid::new_v4().simple().to_string());

    if let Ok(value) = HeaderValue::from_str(&req_id) {
        request
            .headers_mut()
            .insert(header::HeaderName::from_static("x-request-id"), value);
    }

    let mut response = next.run(request).await;
    if let Ok(value) = HeaderValue::from_str(&req_id) {
        response
            .headers_mut()
            .insert(header::HeaderName::from_static("x-request-id"), value);
    }
    response
}
