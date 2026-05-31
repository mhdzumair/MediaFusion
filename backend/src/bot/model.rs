//! Minimal Telegram Bot API types for webhook dispatch.

use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Deserialize)]
pub struct Update {
    pub update_id: i64,
    pub message: Option<Message>,
    pub edited_message: Option<Message>,
    pub callback_query: Option<CallbackQuery>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Message {
    pub message_id: i64,
    pub from: Option<User>,
    pub chat: Chat,
    #[serde(default)]
    pub text: Option<String>,
    #[serde(default)]
    pub caption: Option<String>,
    #[serde(default)]
    pub entities: Option<Vec<MessageEntity>>,
    #[serde(default)]
    pub caption_entities: Option<Vec<MessageEntity>>,
    #[serde(default)]
    pub document: Option<Document>,
    #[serde(default)]
    pub video: Option<Video>,
    #[serde(default)]
    pub forward_from_chat: Option<Chat>,
    #[serde(default)]
    pub forward_from_message_id: Option<i64>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct User {
    pub id: i64,
    #[serde(default)]
    pub is_bot: bool,
    #[serde(default)]
    pub first_name: Option<String>,
    #[serde(default)]
    pub username: Option<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Chat {
    pub id: i64,
    #[serde(default)]
    pub r#type: Option<String>,
    #[serde(default)]
    pub username: Option<String>,
    #[serde(default)]
    pub title: Option<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct MessageEntity {
    pub r#type: String,
    pub offset: i32,
    pub length: i32,
    #[serde(default)]
    pub url: Option<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Document {
    pub file_id: String,
    #[serde(default)]
    pub file_unique_id: Option<String>,
    #[serde(default)]
    pub file_name: Option<String>,
    #[serde(default)]
    pub mime_type: Option<String>,
    #[serde(default)]
    pub file_size: Option<i64>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Video {
    pub file_id: String,
    #[serde(default)]
    pub file_unique_id: Option<String>,
    #[serde(default)]
    pub file_name: Option<String>,
    #[serde(default)]
    pub mime_type: Option<String>,
    #[serde(default)]
    pub file_size: Option<i64>,
}

#[derive(Debug, Deserialize)]
pub struct CallbackQuery {
    pub id: String,
    pub from: User,
    pub message: Option<Message>,
    pub data: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ConversationStep {
    #[default]
    Idle,
    AwaitingMediaType,
    Analyzing,
    AwaitingMatch,
    AwaitingManualImdb,
    AwaitingTitleSearch,
    AwaitingSportsCategory,
    AwaitingMetadataReview,
    AwaitingFieldEdit,
    AwaitingPosterInput,
    AwaitingEpisodeInput,
    AwaitingAnonymousName,
    AwaitingConfirm,
    Importing,
}


#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ContentType {
    Magnet,
    TorrentFile,
    TorrentUrl,
    Nzb,
    Youtube,
    Http,
    Acestream,
    Video,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BatchItemStatus {
    PendingAnalysis,
    AutoMatched,
    NeedsReview,
    NoMatch,
    Importing,
    Imported,
    Failed,
    Skipped,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConversationState {
    pub user_id: i64,
    pub chat_id: i64,
    pub step: ConversationStep,
    #[serde(default)]
    pub content_type: Option<ContentType>,
    #[serde(default)]
    pub raw_input: Value,
    #[serde(default)]
    pub media_type: Option<String>,
    #[serde(default)]
    pub sports_category: Option<String>,
    #[serde(default)]
    pub analysis_result: Option<Value>,
    #[serde(default)]
    pub matches: Option<Vec<Value>>,
    #[serde(default)]
    pub selected_match: Option<Value>,
    #[serde(default)]
    pub metadata_overrides: Value,
    #[serde(default)]
    pub editing_field: Option<String>,
    #[serde(default)]
    pub message_id: Option<i64>,
    #[serde(default)]
    pub original_message_id: Option<i64>,
    #[serde(default)]
    pub custom_poster_url: Option<String>,
    #[serde(default)]
    pub anonymous_display_name: Option<String>,
    #[serde(default)]
    pub batch_item_id: Option<String>,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

impl ConversationState {
    pub fn new(user_id: i64, chat_id: i64) -> Self {
        let now = chrono::Utc::now();
        Self {
            user_id,
            chat_id,
            step: ConversationStep::Idle,
            content_type: None,
            raw_input: Value::Null,
            media_type: None,
            sports_category: None,
            analysis_result: None,
            matches: None,
            selected_match: None,
            metadata_overrides: serde_json::json!({}),
            editing_field: None,
            message_id: None,
            original_message_id: None,
            custom_poster_url: None,
            anonymous_display_name: None,
            batch_item_id: None,
            created_at: now,
            updated_at: now,
        }
    }

    pub fn touch(&mut self) {
        self.updated_at = chrono::Utc::now();
    }

    pub fn is_expired(&self, timeout_minutes: i64) -> bool {
        (chrono::Utc::now() - self.updated_at).num_minutes() > timeout_minutes
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BatchItem {
    pub item_id: String,
    pub file_id: String,
    #[serde(default)]
    pub file_unique_id: Option<String>,
    #[serde(default)]
    pub file_name: Option<String>,
    #[serde(default)]
    pub mime_type: Option<String>,
    #[serde(default)]
    pub file_size: Option<i64>,
    pub chat_id: i64,
    #[serde(default)]
    pub original_message_id: Option<i64>,
    pub status: BatchItemStatus,
    #[serde(default)]
    pub inferred_media_type: Option<String>,
    #[serde(default)]
    pub analysis_result: Option<Value>,
    #[serde(default)]
    pub imdb_candidates: Option<Vec<Value>>,
    #[serde(default)]
    pub selected_match: Option<Value>,
    #[serde(default)]
    pub metadata_overrides: Value,
    #[serde(default)]
    pub error: Option<String>,
    pub created_at: chrono::DateTime<chrono::Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BatchSeriesContext {
    pub external_id: String,
    pub title: String,
    pub season: Option<i32>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BatchState {
    pub batch_id: String,
    pub user_id: i64,
    pub chat_id: i64,
    #[serde(default)]
    pub items: Vec<BatchItem>,
    #[serde(default)]
    pub summary_message_id: Option<i64>,
    #[serde(default)]
    pub editing_item_id: Option<String>,
    #[serde(default)]
    pub series_context: Option<BatchSeriesContext>,
    #[serde(default)]
    pub awaiting_series_input: bool,
    #[serde(default)]
    pub awaiting_season_input: bool,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

impl BatchState {
    pub fn touch(&mut self) {
        self.updated_at = chrono::Utc::now();
    }

    pub fn get_item(&self, item_id: &str) -> Option<&BatchItem> {
        self.items.iter().find(|i| i.item_id == item_id)
    }

    pub fn get_item_mut(&mut self, item_id: &str) -> Option<&mut BatchItem> {
        self.items.iter_mut().find(|i| i.item_id == item_id)
    }
}
