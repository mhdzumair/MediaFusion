/// Content voting and rating endpoints.
///
/// Routes:
///   POST   /api/v1/streams/{stream_id}/vote      → vote_stream
///   DELETE /api/v1/streams/{stream_id}/vote      → delete_stream_vote  (204)
///   GET    /api/v1/streams/{stream_id}/votes     → get_stream_votes    (optional auth)
///   POST   /api/v1/streams/votes/bulk            → bulk_stream_votes   (optional auth)
///   POST   /api/v1/content/{media_id}/rate       → rate_content
///   GET    /api/v1/content/{media_id}/ratings    → get_content_ratings (optional auth)
///   POST   /api/v1/content/ratings/bulk          → bulk_content_ratings (optional auth)
///   POST   /api/v1/content/{media_id}/like       → like_content
///   DELETE /api/v1/content/{media_id}/like       → unlike_content      (204)
///   GET    /api/v1/content/{media_id}/likes      → get_content_likes   (optional auth)
use std::sync::Arc;

use axum::{
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use chrono::{DateTime, Utc};
use hmac::{Hmac, KeyInit, Mac};
use serde::Deserialize;
use serde_json::json;
use sha2::Sha256;
use uuid::Uuid;

use crate::state::AppState;

// ─── Auth helpers ─────────────────────────────────────────────────────────────

fn validate_token(headers: &HeaderMap, secret_key: &str) -> Option<i32> {
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

// ─── Request types ────────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct StreamVoteRequest {
    pub vote: Option<i32>,
    pub vote_type: Option<String>,
    pub quality_status: Option<String>,
    pub comment: Option<String>,
}

#[derive(Deserialize)]
pub struct ContentRatingRequest {
    pub rating: f64,
}

// ─── Helper: resolve vote_type string from request ───────────────────────────

fn resolve_vote_type(req: &StreamVoteRequest) -> Option<&str> {
    // Prefer vote_type if present
    if let Some(ref vt) = req.vote_type {
        return match vt.as_str() {
            "up" | "down" => Some(vt.as_str()),
            _ => None,
        };
    }
    // Fall back to numeric vote
    match req.vote {
        Some(1) => Some("up"),
        Some(-1) => Some("down"),
        _ => None,
    }
}

// ─── Helper: get stream vote summary for a single stream_id ──────────────────

struct StreamVoteSummary {
    upvotes: i64,
    downvotes: i64,
    score: i64,
    score_percent: i64,
    user_vote: Option<serde_json::Value>,
}

async fn stream_vote_summary(
    pool_ro: &sqlx::PgPool,
    stream_id: i32,
    user_id: Option<i32>,
) -> Result<StreamVoteSummary, sqlx::Error> {
    // Get aggregated counts
    let row: (Option<i64>, Option<i64>) = sqlx::query_as(
        r#"
        SELECT
            COUNT(*) FILTER (WHERE vote_type = 'up'),
            COUNT(*) FILTER (WHERE vote_type = 'down')
        FROM stream_votes
        WHERE stream_id = $1
        "#,
    )
    .bind(stream_id)
    .fetch_one(pool_ro)
    .await?;

    let upvotes = row.0.unwrap_or(0);
    let downvotes = row.1.unwrap_or(0);
    let total = upvotes + downvotes;
    let score = upvotes - downvotes;
    let score_percent = if total > 0 { 100 * upvotes / total } else { 0 };

    let user_vote = if let Some(uid) = user_id {
        sqlx::query_as::<_, (String, Option<String>, Option<String>)>(
            "SELECT vote_type, quality_status, comment FROM stream_votes WHERE user_id = $1 AND stream_id = $2 LIMIT 1",
        )
        .bind(uid)
        .bind(stream_id)
        .fetch_optional(pool_ro)
        .await?
        .map(|(vote_type, quality_status, comment)| {
            json!({
                "vote_type": vote_type,
                "quality_status": quality_status,
                "comment": comment,
            })
        })
    } else {
        None
    };

    Ok(StreamVoteSummary {
        upvotes,
        downvotes,
        score,
        score_percent,
        user_vote,
    })
}

// ─── Helper: get content rating summary for a single media_id ────────────────

struct ContentRatingSummary {
    average_rating: Option<f64>,
    ratings_count: i64,
    user_rating: Option<i32>,
}

async fn content_rating_summary(
    pool_ro: &sqlx::PgPool,
    media_id: i32,
    user_id: Option<i32>,
) -> Result<ContentRatingSummary, sqlx::Error> {
    let row: (Option<f64>, Option<i64>) = sqlx::query_as(
        r#"
        SELECT
            AVG(vote::float),
            COUNT(*)
        FROM metadata_votes
        WHERE media_id = $1 AND vote_type = 'rating'
        "#,
    )
    .bind(media_id)
    .fetch_one(pool_ro)
    .await?;

    let average_rating = row.0;
    let ratings_count = row.1.unwrap_or(0);

    let user_rating = if let Some(uid) = user_id {
        sqlx::query_scalar::<_, Option<i32>>(
            "SELECT vote FROM metadata_votes WHERE user_id = $1 AND media_id = $2 AND vote_type = 'rating' LIMIT 1",
        )
        .bind(uid)
        .bind(media_id)
        .fetch_optional(pool_ro)
        .await?
        .flatten()
    } else {
        None
    };

    Ok(ContentRatingSummary {
        average_rating,
        ratings_count,
        user_rating,
    })
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// POST /api/v1/streams/{stream_id}/vote
pub async fn vote_stream(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(stream_id): Path<i32>,
    Json(body): Json<StreamVoteRequest>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"error": "Authentication required"})),
            )
                .into_response();
        }
    };

    // Verify stream exists
    let exists: bool =
        sqlx::query_scalar::<_, bool>("SELECT EXISTS(SELECT 1 FROM stream WHERE id = $1)")
            .bind(stream_id)
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(false);

    if !exists {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"error": "Stream not found"})),
        )
            .into_response();
    }

    // Resolve vote_type
    let vote_type_str = match resolve_vote_type(&body) {
        Some(v) => v,
        None => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"error": "vote must be 1 or -1, or vote_type must be 'up' or 'down'"})),
            )
                .into_response();
        }
    };

    let vote_id = Uuid::new_v4().to_string();

    // Upsert
    let row: (String, String, Option<String>, Option<String>, DateTime<Utc>, DateTime<Utc>) =
        match sqlx::query_as(
            r#"
            INSERT INTO stream_votes(id, user_id, stream_id, vote_type, quality_status, comment, created_at, updated_at)
            VALUES($1, $2, $3, $4, $5, $6, NOW(), NOW())
            ON CONFLICT (user_id, stream_id)
            DO UPDATE SET vote_type = $4, quality_status = $5, comment = $6, updated_at = NOW()
            RETURNING id, vote_type, quality_status, comment, created_at, updated_at
            "#,
        )
        .bind(&vote_id)
        .bind(user_id)
        .bind(stream_id)
        .bind(vote_type_str)
        .bind(&body.quality_status)
        .bind(&body.comment)
        .fetch_one(&state.pool)
        .await
        {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("DB error upserting stream vote: {e}");
                return (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({"error": "Database error"})),
                )
                    .into_response();
            }
        };

    let vote_int: i32 = if row.1 == "up" { 1 } else { -1 };

    (
        StatusCode::OK,
        Json(json!({
            "id": row.0,
            "stream_id": stream_id,
            "user_id": user_id,
            "vote": vote_int,
            "vote_type": row.1,
            "quality_status": row.2,
            "comment": row.3,
            "voted_at": row.5,
        })),
    )
        .into_response()
}

/// DELETE /api/v1/streams/{stream_id}/vote
pub async fn delete_stream_vote(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(stream_id): Path<i32>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"error": "Authentication required"})),
            )
                .into_response();
        }
    };

    let result = sqlx::query("DELETE FROM stream_votes WHERE user_id = $1 AND stream_id = $2")
        .bind(user_id)
        .bind(stream_id)
        .execute(&state.pool)
        .await;

    match result {
        Ok(r) if r.rows_affected() == 0 => (
            StatusCode::NOT_FOUND,
            Json(json!({"error": "Vote not found"})),
        )
            .into_response(),
        Ok(_) => StatusCode::NO_CONTENT.into_response(),
        Err(e) => {
            tracing::error!("DB error deleting stream vote: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "Database error"})),
            )
                .into_response()
        }
    }
}

/// GET /api/v1/streams/{stream_id}/votes
pub async fn get_stream_votes(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(stream_id): Path<i32>,
) -> Response {
    let user_id = validate_token(&headers, &state.config.secret_key_raw);

    match stream_vote_summary(&state.pool_ro, stream_id, user_id).await {
        Ok(summary) => (
            StatusCode::OK,
            Json(json!({
                "stream_id": stream_id,
                "upvotes": summary.upvotes,
                "downvotes": summary.downvotes,
                "score": summary.score,
                "score_percent": summary.score_percent,
                "user_vote": summary.user_vote,
            })),
        )
            .into_response(),
        Err(e) => {
            tracing::error!("DB error getting stream votes: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "Database error"})),
            )
                .into_response()
        }
    }
}

/// POST /api/v1/streams/votes/bulk
pub async fn bulk_stream_votes(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let stream_ids: Vec<i32> = body
        .get("stream_ids")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_i64().map(|n| n as i32))
                .collect()
        })
        .unwrap_or_default();

    if stream_ids.len() > 50 {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "Maximum 50 stream_ids allowed"})),
        )
            .into_response();
    }

    let user_id = validate_token(&headers, &state.config.secret_key_raw);

    let mut summaries = serde_json::Map::new();
    for stream_id in &stream_ids {
        match stream_vote_summary(&state.pool_ro, *stream_id, user_id).await {
            Ok(summary) => {
                summaries.insert(
                    stream_id.to_string(),
                    json!({
                        "stream_id": stream_id,
                        "upvotes": summary.upvotes,
                        "downvotes": summary.downvotes,
                        "score": summary.score,
                        "score_percent": summary.score_percent,
                        "user_vote": summary.user_vote,
                    }),
                );
            }
            Err(e) => {
                tracing::warn!("DB error getting stream votes for {stream_id}: {e}");
            }
        }
    }

    (StatusCode::OK, Json(json!({"summaries": summaries}))).into_response()
}

/// POST /api/v1/content/{media_id}/rate
pub async fn rate_content(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(media_id): Path<i32>,
    Json(body): Json<ContentRatingRequest>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"error": "Authentication required"})),
            )
                .into_response();
        }
    };

    // Verify media exists
    let exists: bool =
        sqlx::query_scalar::<_, bool>("SELECT EXISTS(SELECT 1 FROM media WHERE id = $1)")
            .bind(media_id)
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(false);

    if !exists {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"error": "Media not found"})),
        )
            .into_response();
    }

    // Validate rating range
    if body.rating < 1.0 || body.rating > 10.0 {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "Rating must be between 1.0 and 10.0"})),
        )
            .into_response();
    }

    let rating_int = body.rating.round() as i32;
    let vote_id = Uuid::new_v4().to_string();

    let row: (DateTime<Utc>, DateTime<Utc>) = match sqlx::query_as(
        r#"
        INSERT INTO metadata_votes(id, user_id, media_id, vote_type, vote, created_at, updated_at)
        VALUES($1, $2, $3, 'rating', $4, NOW(), NOW())
        ON CONFLICT (user_id, media_id)
        DO UPDATE SET vote_type = 'rating', vote = $4, updated_at = NOW()
        RETURNING created_at, updated_at
        "#,
    )
    .bind(&vote_id)
    .bind(user_id)
    .bind(media_id)
    .bind(rating_int)
    .fetch_one(&state.pool)
    .await
    {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("DB error upserting content rating: {e}");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "Database error"})),
            )
                .into_response();
        }
    };

    (
        StatusCode::OK,
        Json(json!({
            "media_id": media_id,
            "user_id": user_id,
            "rating": rating_int,
            "voted_at": row.1,
        })),
    )
        .into_response()
}

/// GET /api/v1/content/{media_id}/ratings
pub async fn get_content_ratings(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(media_id): Path<i32>,
) -> Response {
    let user_id = validate_token(&headers, &state.config.secret_key_raw);

    match content_rating_summary(&state.pool_ro, media_id, user_id).await {
        Ok(summary) => (
            StatusCode::OK,
            Json(json!({
                "media_id": media_id,
                "average_rating": summary.average_rating,
                "ratings_count": summary.ratings_count,
                "user_rating": summary.user_rating,
            })),
        )
            .into_response(),
        Err(e) => {
            tracing::error!("DB error getting content ratings: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "Database error"})),
            )
                .into_response()
        }
    }
}

/// POST /api/v1/content/ratings/bulk
pub async fn bulk_content_ratings(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let media_ids: Vec<i32> = body
        .get("media_ids")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_i64().map(|n| n as i32))
                .collect()
        })
        .unwrap_or_default();
    if media_ids.len() > 100 {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "Maximum 100 media_ids allowed"})),
        )
            .into_response();
    }

    let user_id = validate_token(&headers, &state.config.secret_key_raw);

    let mut summaries = serde_json::Map::new();
    for media_id in &media_ids {
        match content_rating_summary(&state.pool_ro, *media_id, user_id).await {
            Ok(summary) => {
                summaries.insert(
                    media_id.to_string(),
                    json!({
                        "media_id": media_id,
                        "average_rating": summary.average_rating,
                        "ratings_count": summary.ratings_count,
                        "user_rating": summary.user_rating,
                    }),
                );
            }
            Err(e) => {
                tracing::warn!("DB error getting ratings for media {media_id}: {e}");
            }
        }
    }

    (StatusCode::OK, Json(json!({"summaries": summaries}))).into_response()
}

/// POST /api/v1/content/{media_id}/like
pub async fn like_content(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(media_id): Path<i32>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"error": "Authentication required"})),
            )
                .into_response();
        }
    };

    // Verify media exists
    let exists: bool =
        sqlx::query_scalar::<_, bool>("SELECT EXISTS(SELECT 1 FROM media WHERE id = $1)")
            .bind(media_id)
            .fetch_one(&state.pool_ro)
            .await
            .unwrap_or(false);

    if !exists {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"error": "Media not found"})),
        )
            .into_response();
    }

    // Check if like already exists (idempotent)
    let existing: Option<(String, DateTime<Utc>)> = match sqlx::query_as(
        "SELECT id, created_at FROM metadata_votes WHERE user_id = $1 AND media_id = $2 AND vote_type = 'like' LIMIT 1",
    )
    .bind(user_id)
    .bind(media_id)
    .fetch_optional(&state.pool_ro)
    .await
    {
        Ok(v) => v,
        Err(e) => {
            tracing::error!("DB error checking existing like: {e}");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "Database error"})),
            )
                .into_response();
        }
    };

    if let Some((like_id, created_at)) = existing {
        return (
            StatusCode::OK,
            Json(json!({
                "id": like_id,
                "media_id": media_id,
                "liked": true,
                "created_at": created_at,
            })),
        )
            .into_response();
    }

    // Insert new like
    let like_id = Uuid::new_v4().to_string();

    let row: (String, DateTime<Utc>) = match sqlx::query_as(
        r#"
        INSERT INTO metadata_votes(id, user_id, media_id, vote_type, created_at, updated_at)
        VALUES($1, $2, $3, 'like', NOW(), NOW())
        RETURNING id, created_at
        "#,
    )
    .bind(&like_id)
    .bind(user_id)
    .bind(media_id)
    .fetch_one(&state.pool)
    .await
    {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("DB error inserting like: {e}");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "Database error"})),
            )
                .into_response();
        }
    };

    (
        StatusCode::OK,
        Json(json!({
            "id": row.0,
            "media_id": media_id,
            "liked": true,
            "created_at": row.1,
        })),
    )
        .into_response()
}

/// DELETE /api/v1/content/{media_id}/like
pub async fn unlike_content(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(media_id): Path<i32>,
) -> Response {
    let user_id = match validate_token(&headers, &state.config.secret_key_raw) {
        Some(id) => id,
        None => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"error": "Authentication required"})),
            )
                .into_response();
        }
    };

    let result = sqlx::query(
        "DELETE FROM metadata_votes WHERE user_id = $1 AND media_id = $2 AND vote_type = 'like'",
    )
    .bind(user_id)
    .bind(media_id)
    .execute(&state.pool)
    .await;

    match result {
        Ok(r) if r.rows_affected() == 0 => (
            StatusCode::NOT_FOUND,
            Json(json!({"error": "Like not found"})),
        )
            .into_response(),
        Ok(_) => StatusCode::NO_CONTENT.into_response(),
        Err(e) => {
            tracing::error!("DB error deleting like: {e}");
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "Database error"})),
            )
                .into_response()
        }
    }
}

/// GET /api/v1/content/{media_id}/likes
pub async fn get_content_likes(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Path(media_id): Path<i32>,
) -> Response {
    let user_id = validate_token(&headers, &state.config.secret_key_raw);

    let likes_count: i64 = match sqlx::query_scalar::<_, i64>(
        "SELECT COUNT(*) FROM metadata_votes WHERE media_id = $1 AND vote_type = 'like'",
    )
    .bind(media_id)
    .fetch_one(&state.pool_ro)
    .await
    {
        Ok(v) => v,
        Err(e) => {
            tracing::error!("DB error counting likes: {e}");
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": "Database error"})),
            )
                .into_response();
        }
    };

    let user_liked = if let Some(uid) = user_id {
        match sqlx::query_scalar::<_, bool>(
            "SELECT EXISTS(SELECT 1 FROM metadata_votes WHERE user_id = $1 AND media_id = $2 AND vote_type = 'like')",
        )
        .bind(uid)
        .bind(media_id)
        .fetch_one(&state.pool_ro)
        .await
        {
            Ok(v) => v,
            Err(e) => {
                tracing::warn!("DB error checking user like: {e}");
                false
            }
        }
    } else {
        false
    };

    (
        StatusCode::OK,
        Json(json!({
            "media_id": media_id,
            "likes_count": likes_count,
            "user_liked": user_liked,
        })),
    )
        .into_response()
}
