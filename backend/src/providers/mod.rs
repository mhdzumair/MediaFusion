pub mod torrents;
pub mod usenet;

use thiserror::Error;

/// Read a response as text, then parse as JSON.
/// On any failure (HTTP error, read error, parse error) logs a WARN with
/// the raw response body/status so callers can diagnose what the provider
/// actually returned, then returns the original error.
///
/// For 401/403 responses whose body is not valid JSON (e.g. an HTML error page
/// from a reverse proxy), returns a typed `invalid_token.mp4` error rather than
/// the generic `api_error.mp4` that a bare parse failure would produce.
pub async fn response_json(
    resp: reqwest::Response,
    context: &str,
) -> Result<serde_json::Value, ProviderError> {
    let status = resp.status();
    let text = resp.text().await.map_err(|e| {
        tracing::warn!("{context}: failed to read response body (HTTP {status}): {e}");
        ProviderError::Http(e)
    })?;
    // Some endpoints return 200 with an empty body instead of 204.
    // Treat empty-body 2xx as null so callers handle it the same as a real 204.
    if text.trim().is_empty() {
        return if status.is_success() {
            Ok(serde_json::Value::Null)
        } else {
            let video = if status == reqwest::StatusCode::UNAUTHORIZED
                || status == reqwest::StatusCode::FORBIDDEN
            {
                "invalid_token.mp4"
            } else {
                "api_error.mp4"
            };
            Err(ProviderError::api(
                format!("HTTP {status} with empty response"),
                video,
            ))
        };
    }

    serde_json::from_str(&text).map_err(|e| {
        // Auth failures with non-JSON bodies (HTML proxy error pages) should show
        // the credentials error video, not the generic api_error.
        if status == reqwest::StatusCode::UNAUTHORIZED || status == reqwest::StatusCode::FORBIDDEN {
            return ProviderError::api(
                format!("Authentication failed (HTTP {status})"),
                "invalid_token.mp4",
            );
        }
        // Log up to 500 chars so HTML error pages are visible without flooding logs.
        let preview = if text.len() > 500 {
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
            // Transport failures from reqwest (DNS, connection reset, TLS, etc.)
            Self::Http(_) => "debrid_service_down_error.mp4",
            _ => "api_error.mp4",
        }
    }

    /// Whether this error is an unexpected operational failure (vs. user/account config).
    ///
    /// Unexpected errors are logged at WARN; expected user-facing errors at DEBUG.
    pub fn is_unexpected(&self) -> bool {
        match self {
            Self::Api { video_file, .. } => *video_file == "api_error.mp4",
            Self::Http(_) | Self::Json(_) => false,
            Self::Other(_) => true,
        }
    }

    /// Log a provider error at WARN (unexpected) or DEBUG (expected user/account issue).
    pub fn log(&self, message: &str) {
        if self.is_unexpected() {
            tracing::warn!("{message}: {self}");
        } else {
            tracing::debug!("{message}: {self}");
        }
    }
}
