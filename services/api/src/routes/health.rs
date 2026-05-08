use axum::{response::IntoResponse, Json};
use serde_json::json;

pub async fn handler() -> impl IntoResponse {
    Json(json!({"status": "ok"}))
}
