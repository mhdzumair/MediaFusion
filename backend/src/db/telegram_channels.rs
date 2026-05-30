//! Per-user Telegram scraping channel helpers.

use serde_json::Value;
use sqlx::PgPool;

/// Load enabled per-user channel identifiers from profile `tgc` config.
pub async fn user_scraping_channels(pool: &PgPool, user_id: i64) -> Vec<String> {
    let row: Option<Value> = sqlx::query_scalar(
        "SELECT config->'tgc' FROM user_profiles WHERE user_id = $1 AND is_default = true LIMIT 1",
    )
    .bind(user_id)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten();

    let Some(tgc) = row else {
        return vec![];
    };

    if tgc.get("enabled").and_then(|v| v.as_bool()) == Some(false) {
        return vec![];
    }

    tgc.get("channels")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|c| {
                    let enabled = c.get("enabled").and_then(|v| v.as_bool()).unwrap_or(true);
                    if !enabled {
                        return None;
                    }
                    c.get("channel_id")
                        .or_else(|| c.get("username"))
                        .and_then(|v| v.as_str())
                        .map(|s| {
                            if s.starts_with('@') {
                                s.to_string()
                            } else {
                                format!("@{s}")
                            }
                        })
                })
                .collect()
        })
        .unwrap_or_default()
}
