//! Telegram content-contribution bot (Bot HTTP API).

mod analyze;
mod api;
mod batch;
mod callback;
mod commands;
mod content_exists;
mod detect;
mod disabled_content;
mod dispatch;
mod forwarded;
mod import;
mod login;
mod matches;
mod metadata;
mod model;
mod notifications;
mod state_store;
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
pub use state_store::{clear_scrape_job, user_mapping_key};
