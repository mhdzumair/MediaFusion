/// Unified stream community stats — votes, signals, and watched counts in one bulk call.
///
/// Routes:
///   POST /api/v1/streams/community/bulk → bulk_stream_community
use std::{collections::HashMap, sync::Arc};

use axum::{
    Json,
    extract::State,
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use chrono::Utc;
use hmac::{Hmac, KeyInit, Mac};
use serde::Deserialize;
use serde_json::json;
use sha2::Sha256;

use crate::{db, state::AppState};

const ISSUE_SUGGESTION_TYPES: &[&str] = &["report_broken", "other"];
const MAX_BULK_STREAM_IDS: usize = 100;

#[derive(Deserialize)]
pub struct BulkStreamCommunityRequest {
    pub stream_ids: Vec<i32>,
}

fn validate_token_optional(headers: &HeaderMap, secret_key: &str) -> Option<i32> {
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

struct VoteAgg {
    upvotes: i64,
    downvotes: i64,
}

struct UserVoteRow {
    vote_type: String,
    quality_status: Option<String>,
    comment: Option<String>,
}

/// POST /api/v1/streams/community/bulk
pub async fn bulk_stream_community(
    headers: HeaderMap,
    State(state): State<Arc<AppState>>,
    Json(body): Json<BulkStreamCommunityRequest>,
) -> Response {
    let stream_ids: Vec<i32> = body
        .stream_ids
        .into_iter()
        .filter(|id| *id > 0)
        .collect();

    if stream_ids.is_empty() {
        return (StatusCode::OK, Json(json!({ "streams": {} }))).into_response();
    }
    if stream_ids.len() > MAX_BULK_STREAM_IDS {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": format!("Maximum {MAX_BULK_STREAM_IDS} stream_ids allowed")})),
        )
            .into_response();
    }

    let user_id = validate_token_optional(&headers, &state.config.secret_key_raw);
    let pool = &state.pool_ro;

    let vote_aggs: HashMap<i32, VoteAgg> = sqlx::query_as::<_, (i32, Option<i64>, Option<i64>)>(
        r#"SELECT stream_id,
                  COUNT(*) FILTER (WHERE vote_type = 'up'),
                  COUNT(*) FILTER (WHERE vote_type = 'down')
           FROM stream_votes
           WHERE stream_id = ANY($1)
           GROUP BY stream_id"#,
    )
    .bind(&stream_ids)
    .fetch_all(pool)
    .await
    .unwrap_or_default()
    .into_iter()
    .map(|(stream_id, up, down)| {
        (
            stream_id,
            VoteAgg {
                upvotes: up.unwrap_or(0),
                downvotes: down.unwrap_or(0),
            },
        )
    })
    .collect();

    let user_votes: HashMap<i32, UserVoteRow> = if let Some(uid) = user_id {
        sqlx::query_as::<_, (i32, String, Option<String>, Option<String>)>(
            "SELECT stream_id, vote_type, quality_status, comment FROM stream_votes WHERE user_id = $1 AND stream_id = ANY($2)",
        )
        .bind(uid)
        .bind(&stream_ids)
        .fetch_all(pool)
        .await
        .unwrap_or_default()
        .into_iter()
        .map(|(stream_id, vote_type, quality_status, comment)| {
            (
                stream_id,
                UserVoteRow {
                    vote_type,
                    quality_status,
                    comment,
                },
            )
        })
        .collect()
    } else {
        HashMap::new()
    };

    let issue_aggs: HashMap<i32, i64> = sqlx::query_as::<_, (i32, i64)>(
        r#"SELECT stream_id, COUNT(*)::bigint
           FROM stream_suggestions
           WHERE stream_id = ANY($1)
             AND suggestion_type = ANY($2)
             AND status != 'rejected'
             AND (issue_triage_status IS NULL OR issue_triage_status != 'dismissed')
           GROUP BY stream_id"#,
    )
    .bind(&stream_ids)
    .bind(ISSUE_SUGGESTION_TYPES)
    .fetch_all(pool)
    .await
    .unwrap_or_default()
    .into_iter()
    .collect();

    let user_issues: HashMap<i32, bool> = if let Some(uid) = user_id {
        sqlx::query_as::<_, (i32,)>(
            r#"SELECT DISTINCT stream_id FROM stream_suggestions
               WHERE stream_id = ANY($1) AND user_id = $2 AND suggestion_type = ANY($3)"#,
        )
        .bind(&stream_ids)
        .bind(uid)
        .bind(ISSUE_SUGGESTION_TYPES)
        .fetch_all(pool)
        .await
        .unwrap_or_default()
        .into_iter()
        .map(|(stream_id,)| (stream_id, true))
        .collect()
    } else {
        HashMap::new()
    };

    let watched_counts = db::fetch_watched_counts_bulk(pool, &stream_ids)
        .await
        .unwrap_or_default();

    let mut streams = serde_json::Map::new();
    for stream_id in stream_ids {
        let votes = vote_aggs.get(&stream_id);
        let upvotes = votes.map(|v| v.upvotes).unwrap_or(0);
        let downvotes = votes.map(|v| v.downvotes).unwrap_or(0);
        let total_votes = upvotes + downvotes;
        let score = upvotes - downvotes;
        let score_percent = if total_votes > 0 {
            100 * upvotes / total_votes
        } else {
            0
        };

        let user_vote = user_votes.get(&stream_id).map(|uv| {
            let vote_int: i32 = if uv.vote_type == "up" { 1 } else { -1 };
            json!({
                "vote_type": uv.vote_type,
                "vote": vote_int,
                "quality_status": uv.quality_status,
                "comment": uv.comment,
            })
        });

        let user_vote_int = user_votes
            .get(&stream_id)
            .map(|uv| if uv.vote_type == "up" { 1 } else { -1 });

        streams.insert(
            stream_id.to_string(),
            json!({
                "stream_id": stream_id,
                "upvotes": upvotes,
                "downvotes": downvotes,
                "score": score,
                "score_percent": score_percent,
                "user_vote": user_vote,
                "rating_up": upvotes,
                "rating_down": downvotes,
                "rating_score": score,
                "rating_total": total_votes,
                "user_vote_int": user_vote_int,
                "issue_report_count": issue_aggs.get(&stream_id).copied().unwrap_or(0),
                "user_has_issue_report": user_id.map(|_| user_issues.contains_key(&stream_id)),
                "watched_count": watched_counts.get(&stream_id).copied().unwrap_or(0),
            }),
        );
    }

    (StatusCode::OK, Json(json!({ "streams": streams }))).into_response()
}
