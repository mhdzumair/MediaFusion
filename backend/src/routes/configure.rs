use axum::{
    body::Body,
    extract::Path,
    http::{StatusCode, header},
    response::{IntoResponse, Response},
};

/// Public configure: redirect to SPA (no user context).
pub async fn handler() -> impl IntoResponse {
    Response::builder()
        .status(StatusCode::FOUND)
        .header(header::LOCATION, "/app/configure")
        .body(Body::empty())
        .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
}

/// User configure: redirect to SPA preserving secret_str as a query param so
/// ConfigurePage can call /decrypt-user-data and pre-populate the form.
pub async fn user_handler(Path(secret_str): Path<String>) -> impl IntoResponse {
    let location = format!(
        "/app/configure?secret_str={}",
        urlencoding::encode(&secret_str)
    );
    Response::builder()
        .status(StatusCode::FOUND)
        .header(header::LOCATION, location)
        .body(Body::empty())
        .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
}
