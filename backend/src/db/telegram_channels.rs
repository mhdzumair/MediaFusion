//! Per-user Telegram scraping channel helpers.

use serde_json::{Value, json};
use sqlx::PgPool;
use std::collections::HashMap;

use crate::util::telegram_channel_id::{self, ChannelRef};

use super::types::UserId;

#[derive(Debug, Clone)]
pub struct UserChannel {
    pub id: String,
    pub name: String,
    pub enabled: bool,
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct ChannelScrapeStats {
    pub stream_count: i64,
}

#[derive(Debug)]
pub enum ChannelMutationError {
    Duplicate,
    NotFound,
    Database(sqlx::Error),
}

/// Extract enabled channel identifiers from a profile `tgc` JSON object.
pub fn channels_from_tgc(tgc: &Value) -> Vec<String> {
    if tgc.get("enabled").and_then(|v| v.as_bool()) == Some(false) {
        return vec![];
    }

    tgc.get("ch")
        .or_else(|| tgc.get("channels"))
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|c| {
                    let enabled = c.get("enabled").and_then(|v| v.as_bool()).unwrap_or(true);
                    if !enabled {
                        return None;
                    }
                    c.get("id")
                        .or_else(|| c.get("channel_id"))
                        .or_else(|| c.get("username"))
                        .and_then(|v| v.as_str())
                        .map(telegram_channel_id::normalize_stored_channel_id)
                })
                .collect()
        })
        .unwrap_or_default()
}

/// Load enabled per-user channel identifiers from profile `tgc` config.
pub async fn user_scraping_channels(pool: &PgPool, user_id: UserId) -> Vec<String> {
    let row: Option<Value> = sqlx::query_scalar(
        "SELECT config->'tgc' FROM user_profiles WHERE user_id = $1 AND is_default = true LIMIT 1",
    )
    .bind(user_id)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten();

    row.as_ref()
        .map(channels_from_tgc)
        .unwrap_or_default()
        .into_iter()
        .filter(|s| !s.is_empty())
        .collect()
}

/// List configured scraping channels for a user profile.
pub async fn list_user_channels(pool: &PgPool, user_id: UserId) -> Vec<UserChannel> {
    let row: Option<Value> = sqlx::query_scalar(
        "SELECT config->'tgc' FROM user_profiles WHERE user_id = $1 AND is_default = true LIMIT 1",
    )
    .bind(user_id)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten();

    row.as_ref()
        .and_then(|tgc| tgc.get("ch").and_then(|v| v.as_array()))
        .map(|arr| {
            arr.iter()
                .filter_map(|c| {
                    let id = c
                        .get("id")
                        .or_else(|| c.get("channel_id"))
                        .or_else(|| c.get("username"))
                        .and_then(|v| v.as_str())?;
                    Some(UserChannel {
                        id: telegram_channel_id::normalize_stored_channel_id(id),
                        name: c
                            .get("name")
                            .and_then(|v| v.as_str())
                            .unwrap_or(id)
                            .to_string(),
                        enabled: c.get("enabled").and_then(|v| v.as_bool()).unwrap_or(true),
                    })
                })
                .collect()
        })
        .unwrap_or_default()
}

/// Stream counts keyed by configured channel id.
pub async fn scrape_stats_for_channels(
    pool: &PgPool,
    channels: &[UserChannel],
) -> HashMap<String, ChannelScrapeStats> {
    if channels.is_empty() {
        return HashMap::new();
    }

    let rows: Vec<(String, Option<String>, i64)> = sqlx::query_as(
        "SELECT chat_id, chat_username, COUNT(*)::bigint FROM telegram_stream GROUP BY chat_id, chat_username",
    )
    .fetch_all(pool)
    .await
    .unwrap_or_default();

    let mut stats = HashMap::new();
    for channel in channels {
        let count = count_streams_for_channel(&rows, &channel.id);
        stats.insert(
            channel.id.clone(),
            ChannelScrapeStats {
                stream_count: count,
            },
        );
    }
    stats
}

fn count_streams_for_channel(rows: &[(String, Option<String>, i64)], channel_id: &str) -> i64 {
    match telegram_channel_id::parse_channel_ref(channel_id) {
        Some(ChannelRef::DialogId(dialog_id)) => rows
            .iter()
            .filter(|(chat_id, _, _)| chat_id.parse::<i64>().ok() == Some(dialog_id))
            .map(|(_, _, count)| count)
            .sum(),
        Some(ChannelRef::Username(username)) => rows
            .iter()
            .filter(|(_, chat_username, _)| {
                chat_username
                    .as_deref()
                    .is_some_and(|u| u.eq_ignore_ascii_case(&username))
            })
            .map(|(_, _, count)| count)
            .sum(),
        None => 0,
    }
}

async fn save_user_channels(
    pool: &PgPool,
    user_id: UserId,
    channels: &[Value],
) -> Result<(), sqlx::Error> {
    let row: Option<Value> = sqlx::query_scalar(
        "SELECT config FROM user_profiles WHERE user_id = $1 AND is_default = true LIMIT 1",
    )
    .bind(user_id)
    .fetch_optional(pool)
    .await?;

    let mut full_config = row.unwrap_or_else(|| json!({}));
    let mut tgc = full_config.get("tgc").cloned().unwrap_or_else(|| json!({}));
    tgc["ch"] = json!(channels);
    full_config["tgc"] = tgc;

    sqlx::query("UPDATE user_profiles SET config = $1 WHERE user_id = $2 AND is_default = true")
        .bind(&full_config)
        .bind(user_id)
        .execute(pool)
        .await?;

    Ok(())
}

/// Add a scraping channel to the user's profile.
pub async fn add_user_channel(
    pool: &PgPool,
    user_id: UserId,
    channel_id: &str,
    name: Option<&str>,
) -> Result<UserChannel, ChannelMutationError> {
    let channel_id = telegram_channel_id::normalize_stored_channel_id(channel_id);
    if channel_id.is_empty() {
        return Err(ChannelMutationError::NotFound);
    }

    let existing = list_user_channels(pool, user_id).await;
    if existing.iter().any(|ch| ch.id == channel_id) {
        return Err(ChannelMutationError::Duplicate);
    }

    let display_name = name.unwrap_or(&channel_id).to_string();
    let new_channel = json!({
        "id": channel_id,
        "name": display_name,
        "enabled": true,
        "priority": 1,
    });
    let mut channels: Vec<Value> = existing
        .into_iter()
        .map(|ch| {
            json!({
                "id": ch.id,
                "name": ch.name,
                "enabled": ch.enabled,
                "priority": 1,
            })
        })
        .collect();
    channels.push(new_channel);

    save_user_channels(pool, user_id, &channels)
        .await
        .map_err(ChannelMutationError::Database)?;

    Ok(UserChannel {
        id: channel_id,
        name: display_name,
        enabled: true,
    })
}

/// Remove a scraping channel from the user's profile. Returns false when not found.
pub async fn remove_user_channel(
    pool: &PgPool,
    user_id: UserId,
    channel_id: &str,
) -> Result<bool, sqlx::Error> {
    let channel_id = telegram_channel_id::normalize_stored_channel_id(channel_id);
    let existing = list_user_channels(pool, user_id).await;
    let original_len = existing.len();
    let updated: Vec<Value> = existing
        .into_iter()
        .filter(|ch| ch.id != channel_id)
        .map(|ch| {
            json!({
                "id": ch.id,
                "name": ch.name,
                "enabled": ch.enabled,
                "priority": 1,
            })
        })
        .collect();

    if updated.len() == original_len {
        return Ok(false);
    }

    save_user_channels(pool, user_id, &updated).await?;
    Ok(true)
}

/// Update enabled/name/priority for a configured channel.
pub async fn update_user_channel(
    pool: &PgPool,
    user_id: UserId,
    channel_id: &str,
    name: Option<&str>,
    enabled: Option<bool>,
    priority: Option<i64>,
) -> Result<Option<Value>, sqlx::Error> {
    let channel_id = telegram_channel_id::normalize_stored_channel_id(channel_id);
    let mut channels: Vec<Value> = list_user_channels(pool, user_id)
        .await
        .into_iter()
        .map(|ch| {
            json!({
                "id": ch.id,
                "name": ch.name,
                "enabled": ch.enabled,
                "priority": 1,
            })
        })
        .collect();

    let Some(idx) = channels.iter().position(|ch| {
        ch.get("id")
            .and_then(|v| v.as_str())
            .is_some_and(|id| id == channel_id)
    }) else {
        return Ok(None);
    };

    if let Some(name) = name {
        channels[idx]["name"] = json!(name);
    }
    if let Some(enabled) = enabled {
        channels[idx]["enabled"] = json!(enabled);
    }
    if let Some(priority) = priority {
        channels[idx]["priority"] = json!(priority);
    }

    let updated = channels[idx].clone();
    save_user_channels(pool, user_id, &channels).await?;
    Ok(Some(updated))
}
