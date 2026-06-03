//! Callback data encoding/decoding (64-byte Telegram limit).

use fred::prelude::KeysInterface;

use crate::state::AppState;

use super::state_store;

pub const MAX_CALLBACK_BYTES: usize = 64;

#[derive(Debug, Clone)]
pub enum CallbackAction {
    MediaType {
        user_id: i64,
        media_type: String,
    },
    Match {
        user_id: i64,
        external_id: String,
    },
    Sport {
        user_id: i64,
        category: String,
    },
    SearchTitle {
        user_id: i64,
    },
    Manual {
        user_id: i64,
    },
    MetaEdit {
        user_id: i64,
        field: String,
    },
    MetaVal {
        user_id: i64,
        field: String,
        value: String,
    },
    Confirm {
        user_id: i64,
    },
    Cancel {
        user_id: i64,
    },
    Back {
        user_id: i64,
    },
    BackReview {
        user_id: i64,
    },
    AnonSkip {
        user_id: i64,
    },
    AddPoster {
        user_id: i64,
    },
    ClearPoster {
        user_id: i64,
    },
    BatchSummary {
        user_id: i64,
    },
    BatchReview {
        user_id: i64,
        item_id: String,
    },
    BatchImport {
        user_id: i64,
    },
    BatchSkip {
        user_id: i64,
        item_id: String,
    },
    BatchSetSeries {
        user_id: i64,
    },
    LegacySelect {
        user_id: i64,
        msg_id: i64,
        external_id: String,
    },
}

impl CallbackAction {
    pub async fn encode(&self, state: &AppState) -> String {
        let raw = match self {
            Self::MediaType {
                user_id,
                media_type,
            } => format!("mtype:{user_id}:{media_type}"),
            Self::Match {
                user_id,
                external_id,
            } => format!("match:{user_id}:{external_id}"),
            Self::Sport { user_id, category } => format!("sport:{user_id}:{category}"),
            Self::SearchTitle { user_id } => format!("search_title:{user_id}"),
            Self::Manual { user_id } => format!("manual:{user_id}"),
            Self::MetaEdit { user_id, field } => format!("meta_edit:{user_id}:{field}"),
            Self::MetaVal {
                user_id,
                field,
                value,
            } => {
                format!("meta_val:{user_id}:{field}:{value}")
            }
            Self::Confirm { user_id } => format!("confirm:{user_id}"),
            Self::Cancel { user_id } => format!("cancel:{user_id}"),
            Self::Back { user_id } => format!("back:{user_id}"),
            Self::BackReview { user_id } => format!("back_review:{user_id}"),
            Self::AnonSkip { user_id } => format!("anon_skip:{user_id}"),
            Self::AddPoster { user_id } => format!("add_poster:{user_id}"),
            Self::ClearPoster { user_id } => format!("clear_poster:{user_id}"),
            Self::BatchSummary { user_id } => format!("batch_summary:{user_id}"),
            Self::BatchReview { user_id, item_id } => format!("batch_review:{user_id}:{item_id}"),
            Self::BatchImport { user_id } => format!("batch_import:{user_id}"),
            Self::BatchSkip { user_id, item_id } => format!("batch_skip:{user_id}:{item_id}"),
            Self::BatchSetSeries { user_id } => format!("batch_series:{user_id}"),
            Self::LegacySelect {
                user_id,
                msg_id,
                external_id,
            } => format!("select:{user_id}:{msg_id}:{external_id}"),
        };

        if raw.len() <= MAX_CALLBACK_BYTES {
            return raw;
        }

        state_store::cache_callback_payload(state, &raw).await
    }

    pub async fn decode(data: &str, state: &AppState) -> Option<Self> {
        if let Some(suffix) = data.strip_prefix("cache:") {
            let key = format!("telegram:search_result:{suffix}");
            let raw: Option<String> = state.redis.get(&key).await.ok()?;
            return raw.as_deref().and_then(Self::parse);
        }
        Self::parse(data)
    }

    fn parse(data: &str) -> Option<Self> {
        let parts: Vec<&str> = data.split(':').collect();
        let action = parts.first()?;
        match *action {
            "mtype" if parts.len() >= 3 => Some(Self::MediaType {
                user_id: parts[1].parse().ok()?,
                media_type: parts[2].to_string(),
            }),
            "match" if parts.len() >= 3 => Some(Self::Match {
                user_id: parts[1].parse().ok()?,
                external_id: parts[2..].join(":"),
            }),
            "sport" if parts.len() >= 3 => Some(Self::Sport {
                user_id: parts[1].parse().ok()?,
                category: parts[2].to_string(),
            }),
            "search_title" if parts.len() >= 2 => Some(Self::SearchTitle {
                user_id: parts[1].parse().ok()?,
            }),
            "manual" if parts.len() >= 2 => Some(Self::Manual {
                user_id: parts[1].parse().ok()?,
            }),
            "meta_edit" if parts.len() >= 3 => Some(Self::MetaEdit {
                user_id: parts[1].parse().ok()?,
                field: parts[2].to_string(),
            }),
            "meta_val" if parts.len() >= 4 => Some(Self::MetaVal {
                user_id: parts[1].parse().ok()?,
                field: parts[2].to_string(),
                value: parts[3..].join(":"),
            }),
            "confirm" if parts.len() >= 2 => Some(Self::Confirm {
                user_id: parts[1].parse().ok()?,
            }),
            "cancel" if parts.len() >= 2 => Some(Self::Cancel {
                user_id: parts[1].parse().ok()?,
            }),
            "back" if parts.len() >= 2 => Some(Self::Back {
                user_id: parts[1].parse().ok()?,
            }),
            "back_review" if parts.len() >= 2 => Some(Self::BackReview {
                user_id: parts[1].parse().ok()?,
            }),
            "anon_skip" if parts.len() >= 2 => Some(Self::AnonSkip {
                user_id: parts[1].parse().ok()?,
            }),
            "add_poster" if parts.len() >= 2 => Some(Self::AddPoster {
                user_id: parts[1].parse().ok()?,
            }),
            "clear_poster" if parts.len() >= 2 => Some(Self::ClearPoster {
                user_id: parts[1].parse().ok()?,
            }),
            "batch_summary" if parts.len() >= 2 => Some(Self::BatchSummary {
                user_id: parts[1].parse().ok()?,
            }),
            "batch_review" if parts.len() >= 3 => Some(Self::BatchReview {
                user_id: parts[1].parse().ok()?,
                item_id: parts[2].to_string(),
            }),
            "batch_import" if parts.len() >= 2 => Some(Self::BatchImport {
                user_id: parts[1].parse().ok()?,
            }),
            "batch_skip" if parts.len() >= 3 => Some(Self::BatchSkip {
                user_id: parts[1].parse().ok()?,
                item_id: parts[2].to_string(),
            }),
            "batch_series" if parts.len() >= 2 => Some(Self::BatchSetSeries {
                user_id: parts[1].parse().ok()?,
            }),
            "select" if parts.len() >= 4 => Some(Self::LegacySelect {
                user_id: parts[1].parse().ok()?,
                msg_id: parts[2].parse().ok()?,
                external_id: parts[3..].join(":"),
            }),
            _ => None,
        }
    }

    pub fn target_user_id(&self) -> i64 {
        match self {
            Self::MediaType { user_id, .. }
            | Self::Match { user_id, .. }
            | Self::Sport { user_id, .. }
            | Self::SearchTitle { user_id }
            | Self::Manual { user_id }
            | Self::MetaEdit { user_id, .. }
            | Self::MetaVal { user_id, .. }
            | Self::Confirm { user_id }
            | Self::Cancel { user_id }
            | Self::Back { user_id }
            | Self::BackReview { user_id }
            | Self::AnonSkip { user_id }
            | Self::AddPoster { user_id }
            | Self::ClearPoster { user_id }
            | Self::BatchSummary { user_id }
            | Self::BatchReview { user_id, .. }
            | Self::BatchImport { user_id }
            | Self::BatchSkip { user_id, .. }
            | Self::BatchSetSeries { user_id }
            | Self::LegacySelect { user_id, .. } => *user_id,
        }
    }
}

pub async fn cancel_keyboard(state: &AppState, user_id: i64) -> serde_json::Value {
    serde_json::json!({
        "inline_keyboard": [[{
            "text": "❌ Cancel",
            "callback_data": CallbackAction::Cancel { user_id }.encode(state).await,
        }]]
    })
}
