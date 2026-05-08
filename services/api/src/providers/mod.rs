pub mod alldebrid;
pub mod debridlink;
pub mod easydebrid;
pub mod offcloud;
pub mod pikpak;
pub mod premiumize;
pub mod realdebrid;
pub mod seedr;
pub mod stremthru;
pub mod torbox;

use thiserror::Error;

#[derive(Debug, Error)]
pub enum ProviderError {
    #[error("{message}")]
    Api {
        message: String,
        /// Filename under `/static/exceptions/` to redirect to on error.
        video_file: &'static str,
    },
    #[error("HTTP error: {0}")]
    Http(#[from] reqwest::Error),
    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),
    #[error("{0}")]
    Other(String),
}

impl ProviderError {
    pub fn api(message: impl Into<String>, video_file: &'static str) -> Self {
        Self::Api {
            message: message.into(),
            video_file,
        }
    }

    /// The error video filename to redirect to (default: api_error.mp4).
    pub fn video_file(&self) -> &'static str {
        match self {
            Self::Api { video_file, .. } => video_file,
            _ => "api_error.mp4",
        }
    }
}
