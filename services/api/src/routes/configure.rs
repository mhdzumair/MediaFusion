use axum::{
    body::Body,
    http::{header, StatusCode},
    response::{IntoResponse, Response},
};

pub async fn handler() -> impl IntoResponse {
    Response::builder()
        .status(StatusCode::FOUND)
        .header(header::LOCATION, "/app/configure")
        .body(Body::empty())
        .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response())
}
