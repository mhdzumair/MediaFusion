//! Telegram content-contribution bot (Bot HTTP API).

mod analyze;
mod api;
mod batch;
mod callback;
mod channels;
mod commands;
mod content_exists;
mod detect;
mod dialog_picker;
mod disabled_content;
mod dispatch;
mod forwarded;
mod import;
mod login;
mod matches;
mod metadata;
mod model;
mod notifications;
mod session_setup;
mod state_store;
pub mod telegram_moderation;
mod text;
mod wizard;

pub use api::BotApi;
pub use dispatch::{dispatch_update, register_commands};
pub use model::Update;
pub use notifications::register_notification_handlers;
pub use notifications::{
    notify_if_enabled, send_block_notification, send_content_received_notification,
    send_image_update_notification, send_migration_notification,
};
pub use session_setup::{
    handle_drop_session_command, handle_session_command, notify_session_connected,
};
pub use state_store::{clear_scrape_job, user_mapping_key};
