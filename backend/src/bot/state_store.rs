//! Redis-backed conversation/batch/login state.

use fred::prelude::{Expiration, KeysInterface};

use crate::state::AppState;

use super::model::{BatchState, ConversationState};

const CONVERSATION_TTL_SECS: i64 = 30 * 60;
const BATCH_TTL_SECS: i64 = 24 * 60 * 60;
const LOGIN_TOKEN_TTL_SECS: i64 = 24 * 60 * 60;
const SCRAPE_JOB_TTL_SECS: i64 = 2 * 60 * 60;

fn conversation_key(user_id: i64) -> String {
    format!("telegram:conversation:{user_id}")
}

fn batch_key(user_id: i64) -> String {
    format!("telegram:batch:{user_id}")
}

pub fn login_token_key(token: &str) -> String {
    format!("telegram:login_token:{token}")
}

pub fn user_mapping_key(telegram_user_id: i64) -> String {
    format!("telegram:user_mapping:{telegram_user_id}")
}

pub fn scrape_job_key(user_id: i64) -> String {
    format!("telegram:scrape_job:{user_id}")
}

pub async fn get_conversation(state: &AppState, user_id: i64) -> Option<ConversationState> {
    let key = conversation_key(user_id);
    let raw: Option<String> = state.redis.get(&key).await.ok()?;
    raw.and_then(|s| serde_json::from_str(&s).ok())
}

pub async fn save_conversation(state: &AppState, conv: &ConversationState) {
    let key = conversation_key(conv.user_id);
    if let Ok(json) = serde_json::to_string(conv) {
        let _: Result<(), _> = state
            .redis
            .set::<(), _, _>(
                &key,
                json,
                Some(Expiration::EX(CONVERSATION_TTL_SECS)),
                None,
                false,
            )
            .await;
    }
}

pub async fn clear_conversation(state: &AppState, user_id: i64) {
    let _: Result<i64, _> = state.redis.del(&conversation_key(user_id)).await;
}

pub async fn get_batch(state: &AppState, user_id: i64) -> Option<BatchState> {
    let key = batch_key(user_id);
    let raw: Option<String> = state.redis.get(&key).await.ok()?;
    raw.and_then(|s| serde_json::from_str(&s).ok())
}

pub async fn save_batch(state: &AppState, batch: &BatchState) {
    let key = batch_key(batch.user_id);
    if let Ok(json) = serde_json::to_string(batch) {
        let _: Result<(), _> = state
            .redis
            .set::<(), _, _>(
                &key,
                json,
                Some(Expiration::EX(BATCH_TTL_SECS)),
                None,
                false,
            )
            .await;
    }
}

pub async fn clear_batch(state: &AppState, user_id: i64) {
    let _: Result<i64, _> = state.redis.del(&batch_key(user_id)).await;
}

pub async fn store_login_token(
    state: &AppState,
    token: &str,
    telegram_user_id: i64,
) -> Result<(), fred::error::Error> {
    let payload = serde_json::json!({ "telegram_user_id": telegram_user_id }).to_string();
    state
        .redis
        .set::<(), _, _>(
            &login_token_key(token),
            payload,
            Some(Expiration::EX(LOGIN_TOKEN_TTL_SECS)),
            None,
            false,
        )
        .await
}

pub async fn scrape_job_exists(state: &AppState, user_id: i64) -> bool {
    state
        .redis
        .exists::<i64, _>(&scrape_job_key(user_id))
        .await
        .unwrap_or(0)
        > 0
}

pub async fn set_scrape_job(state: &AppState, user_id: i64, payload: &str) {
    let key = scrape_job_key(user_id);
    let _: Result<(), _> = state
        .redis
        .set::<(), _, _>(
            &key,
            payload,
            Some(Expiration::EX(SCRAPE_JOB_TTL_SECS)),
            None,
            false,
        )
        .await;
}

pub async fn clear_scrape_job(state: &AppState, user_id: i64) {
    let _: Result<i64, _> = state.redis.del(&scrape_job_key(user_id)).await;
}

pub async fn cache_callback_payload(state: &AppState, payload: &str) -> String {
    let id = uuid::Uuid::new_v4().simple().to_string();
    let key = format!("telegram:search_result:{id}");
    let _: Result<(), _> = state
        .redis
        .set::<(), _, _>(&key, payload, Some(Expiration::EX(3600)), None, false)
        .await;
    format!("cache:{id}")
}
