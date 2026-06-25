pub mod file_selection;
pub mod torrents;
pub mod usenet;
pub mod validator;

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

    // 5xx = gateway/upstream error (e.g. 504 with HTML body). No point trying to parse.
    if status.is_server_error() {
        let preview = if text.len() > 200 {
            &text[..200]
        } else {
            &text
        };
        tracing::debug!("{context}: server error (HTTP {status}) — body: {preview}");
        return Err(ProviderError::api(
            format!("HTTP {status}"),
            "debrid_service_down_error.mp4",
        ));
    }

    // 429 = rate limited; body is often an HTML page — catch before JSON parsing.
    if status == reqwest::StatusCode::TOO_MANY_REQUESTS {
        tracing::debug!("{context}: rate limited (HTTP 429)");
        return Err(ProviderError::api(
            "HTTP 429 Too Many Requests",
            "too_many_requests.mp4",
        ));
    }

    // Detect HTML responses (e.g. Cloudflare challenge pages) before attempting JSON parse.
    // These arrive as 200 OK with an HTML body when the service is behind a WAF/CDN.
    let trimmed = text.trim_start();
    if trimmed.starts_with("<!DOCTYPE") || trimmed.starts_with("<html") {
        let preview = if text.len() > 200 {
            &text[..200]
        } else {
            &text
        };
        tracing::debug!(
            "{context}: received HTML instead of JSON (HTTP {status}) — body: {preview}"
        );
        return Err(ProviderError::api(
            format!("HTTP {status} — received HTML (service may be rate-limited or behind a WAF)"),
            "debrid_service_down_error.mp4",
        ));
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
        // Other 4xx client errors (e.g. 405 Method Not Allowed) with non-JSON bodies
        // are known API-level failures, not parse bugs — log at debug and let the
        // caller surface it as a service error rather than double-WARNing.
        if status.is_client_error() {
            let preview = if text.len() > 200 {
                &text[..200]
            } else {
                &text
            };
            tracing::debug!("{context}: non-JSON client error (HTTP {status}) — body: {preview}");
            return ProviderError::api(format!("HTTP {status}"), "debrid_service_down_error.mp4");
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

    /// Short label for the error suitable as a structured log field.
    pub fn error_kind(&self) -> &'static str {
        match self {
            Self::Http(e) => crate::util::http::transport_error_kind(e),
            Self::Api { .. } => "api",
            Self::Json(_) => "decode",
            Self::Other(_) => "other",
        }
    }

    /// HTTP status code for non-playback (UI) responses.
    ///
    /// Expected user/account errors (wrong key, blocked, limits) → 422 so the
    /// middleware passes the detail through to the caller instead of stripping it.
    /// Transport failures → 502.  Unexpected/internal errors → 500.
    pub fn http_status(&self) -> axum::http::StatusCode {
        use axum::http::StatusCode;
        match self {
            Self::Api { video_file, .. } if *video_file != "api_error.mp4" => {
                StatusCode::UNPROCESSABLE_ENTITY
            }
            Self::Http(_) => StatusCode::BAD_GATEWAY,
            _ => StatusCode::INTERNAL_SERVER_ERROR,
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
