pub mod torrents;
pub mod usenet;

use thiserror::Error;

/// Read a response as text, then parse as JSON.
/// On any failure (HTTP error, read error, parse error) logs a WARN with
/// the raw response body/status so callers can diagnose what the provider
/// actually returned, then returns the original error.
pub async fn response_json(
    resp: reqwest::Response,
    context: &str,
) -> Result<serde_json::Value, ProviderError> {
    let status = resp.status();
    let text = resp.text().await.map_err(|e| {
        tracing::warn!("{context}: failed to read response body (HTTP {status}): {e}");
        ProviderError::Http(e)
    })?;
    serde_json::from_str(&text).map_err(|e| {
        // Truncate very long bodies (e.g. full HTML error pages) in the log.
        let preview: &str = if text.len() > 500 {
            &text[..500]
        } else {
            &text
        };
        tracing::warn!("{context}: JSON decode failed (HTTP {status}): {e} — body: {preview}");
        ProviderError::Other(format!("JSON decode failed (HTTP {status}): {e}"))
    })
}

/// Shared error type used by both torrent and usenet provider modules.
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
            Self::Http(e) if e.is_timeout() || e.is_connect() => "debrid_service_down_error.mp4",
            _ => "api_error.mp4",
        }
    }
}
