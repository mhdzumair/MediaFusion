//! Telegram content-contribution bot (Bot HTTP API).

mod analyze;
mod api;
mod batch;
mod callback;
mod commands;
mod detect;
mod dispatch;
mod forwarded;
mod import;
mod login;
mod matches;
mod model;
pub mod state_store;
mod text;
mod wizard;

pub use api::BotApi;
pub use dispatch::{dispatch_update, register_commands};
pub use model::Update;
pub use state_store::clear_scrape_job;
