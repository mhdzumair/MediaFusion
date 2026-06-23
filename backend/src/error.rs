use axum::{
    Json,
    http::StatusCode,
    response::{IntoResponse, Response},
};
use serde_json::json;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum AppError {
    #[error("not found")]
    NotFound,

    #[error("bad request: {0}")]
    BadRequest(String),

    #[error("database error: {0}")]
    Database(#[from] sqlx::Error),

    #[error("crypto error: {0}")]
    Crypto(String),

    #[error("internal error: {0}")]
    Internal(String),
}

impl From<Box<dyn std::error::Error + Send + Sync>> for AppError {
    fn from(e: Box<dyn std::error::Error + Send + Sync>) -> Self {
        AppError::Internal(e.to_string())
    }
}

impl IntoResponse for AppError {
    fn into_response(self) -> Response {
        let status = match &self {
            AppError::NotFound => StatusCode::NOT_FOUND,
            AppError::BadRequest(_) => StatusCode::BAD_REQUEST,
            AppError::Crypto(_) => StatusCode::UNAUTHORIZED,
            _ => StatusCode::INTERNAL_SERVER_ERROR,
        };
        if status == StatusCode::INTERNAL_SERVER_ERROR {
            tracing::error!(error = %self, "internal server error");
        }
        (status, Json(json!({"error": self.to_string()}))).into_response()
    }
}
