/// MediaFlow `/proxy/forward` transport for debrid provider HTTP calls.
///
/// When configured, all debrid API requests are routed through the MediaFlow
/// `/proxy/forward` endpoint so that debrid services see MediaFlow's IP on
/// the TCP connection instead of the addon server's IP.
use reqwest::StatusCode;
use urlencoding::encode as urlencode;

use crate::providers::ProviderError;

/// Debrid API calls routed through `/proxy/forward` failed (MediaFlow unreachable, timeout, etc.).
const MEDIAFLOW_FORWARD_ERROR_VIDEO: &str = "mediaflow_proxy_error.mp4";
/// `/proxy/ip` lookup failed — used for CDN geo hints, not forward transport.
const MEDIAFLOW_IP_ERROR_VIDEO: &str = "mediaflow_ip_error.mp4";

pub struct MediaFlowForward {
    pub base_url: String,
    pub api_password: String,
}

impl MediaFlowForward {
    pub fn new(base_url: &str, api_password: &str) -> Self {
        Self {
            base_url: base_url.trim_end_matches('/').to_string(),
            api_password: api_password.to_string(),
        }
    }

    pub fn forward_url(&self) -> String {
        format!("{}/proxy/forward", self.base_url)
    }

    fn map_forward_request_error(&self, err: reqwest::Error) -> ProviderError {
        let reason = if err.is_timeout() {
            "forward request timed out"
        } else if err.is_connect() {
            "could not connect"
        } else {
            "forward request failed"
        };
        ProviderError::api(
            format!(
                "MediaFlow {reason} at {base}. Check that MediaFlow is running, reachable, and your proxy URL and API password are correct.",
                base = self.base_url
            ),
            MEDIAFLOW_FORWARD_ERROR_VIDEO,
        )
    }

    fn map_ip_lookup_request_error(&self, err: reqwest::Error) -> ProviderError {
        let reason = if err.is_timeout() {
            "IP lookup timed out"
        } else if err.is_connect() {
            "could not connect"
        } else {
            "IP lookup failed"
        };
        ProviderError::api(
            format!(
                "MediaFlow {reason} at {base}. Check your MediaFlow proxy URL and API password.",
                base = self.base_url
            ),
            MEDIAFLOW_IP_ERROR_VIDEO,
        )
    }

    /// Validate a direct MediaFlow `/proxy/ip` response.
    async fn check_ip_lookup_response(
        &self,
        resp: reqwest::Response,
    ) -> Result<reqwest::Response, ProviderError> {
        if resp.status().is_success() {
            return Ok(resp);
        }

        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        let preview = if text.len() > 200 {
            format!("{}…", &text[..200])
        } else {
            text
        };

        let message = if status == StatusCode::UNAUTHORIZED || status == StatusCode::FORBIDDEN {
            format!(
                "MediaFlow IP lookup rejected (HTTP {status}). Check your MediaFlow API password."
            )
        } else {
            format!(
                "MediaFlow IP lookup error (HTTP {status}) at {}: {preview}",
                self.base_url
            )
        };

        Err(ProviderError::api(message, MEDIAFLOW_IP_ERROR_VIDEO))
    }

    async fn send(
        &self,
        builder: reqwest::RequestBuilder,
    ) -> Result<reqwest::Response, ProviderError> {
        builder
            .send()
            .await
            .map_err(|e| self.map_forward_request_error(e))
    }

    /// Route a GET request through /proxy/forward.
    /// `dest` is the full destination URL (query params already embedded).
    pub async fn get(
        &self,
        http: &reqwest::Client,
        dest: &str,
        bearer: &str,
    ) -> Result<reqwest::Response, ProviderError> {
        self.send(
            http.get(self.forward_url())
                .query(&[("d", dest), ("api_password", &self.api_password)])
                .query(&[("h_authorization", format!("Bearer {}", bearer))]),
        )
        .await
    }

    /// Route a form POST through /proxy/forward.
    /// `dest` is the full destination URL (no body params). `form_body` is
    /// the URL-encoded form string.
    pub async fn post_form(
        &self,
        http: &reqwest::Client,
        dest: &str,
        bearer: &str,
        form_body: String,
    ) -> Result<reqwest::Response, ProviderError> {
        self.send(
            http.post(self.forward_url())
                .query(&[("d", dest), ("api_password", &self.api_password)])
                .query(&[("h_authorization", format!("Bearer {}", bearer))])
                .query(&[("h_content-type", "application/x-www-form-urlencoded")])
                .body(form_body),
        )
        .await
    }

    /// Route a JSON POST through /proxy/forward.
    /// `dest` is the full destination URL. `json_body` is the serialized JSON payload.
    pub async fn post_json(
        &self,
        http: &reqwest::Client,
        dest: &str,
        bearer: &str,
        json_body: String,
    ) -> Result<reqwest::Response, ProviderError> {
        self.send(
            http.post(self.forward_url())
                .query(&[("d", dest), ("api_password", &self.api_password)])
                .query(&[("h_authorization", format!("Bearer {}", bearer))])
                .query(&[("h_content-type", "application/json")])
                .body(json_body),
        )
        .await
    }

    /// Route a DELETE request through /proxy/forward.
    pub async fn delete(
        &self,
        http: &reqwest::Client,
        dest: &str,
        bearer: &str,
    ) -> Result<reqwest::Response, ProviderError> {
        self.send(
            http.delete(self.forward_url())
                .query(&[("d", dest), ("api_password", &self.api_password)])
                .query(&[("h_authorization", format!("Bearer {}", bearer))]),
        )
        .await
    }

    /// Route a GET request through /proxy/forward with a verbatim Authorization header value.
    /// Use this for non-Bearer schemes (e.g. `Basic base64(user:pass)`).
    pub async fn get_auth(
        &self,
        http: &reqwest::Client,
        dest: &str,
        authorization: &str,
    ) -> Result<reqwest::Response, ProviderError> {
        self.send(
            http.get(self.forward_url())
                .query(&[("d", dest), ("api_password", &self.api_password)])
                .query(&[("h_authorization", authorization)]),
        )
        .await
    }

    /// Route a POST request through /proxy/forward with a raw body and explicit Content-Type.
    /// Use this for multipart uploads or other binary payloads where the Content-Type boundary
    /// must be included (e.g. `multipart/form-data; boundary=abc`).
    pub async fn post_raw(
        &self,
        http: &reqwest::Client,
        dest: &str,
        bearer: &str,
        content_type: &str,
        body: Vec<u8>,
    ) -> Result<reqwest::Response, ProviderError> {
        self.send(
            http.post(self.forward_url())
                .query(&[("d", dest), ("api_password", &self.api_password)])
                .query(&[("h_authorization", format!("Bearer {}", bearer))])
                .query(&[("h_content-type", content_type)])
                .body(body),
        )
        .await
    }

    /// Route a PUT request through /proxy/forward with a raw body and explicit Content-Type.
    pub async fn put_raw(
        &self,
        http: &reqwest::Client,
        dest: &str,
        bearer: &str,
        content_type: &str,
        body: Vec<u8>,
    ) -> Result<reqwest::Response, ProviderError> {
        self.send(
            http.put(self.forward_url())
                .query(&[("d", dest), ("api_password", &self.api_password)])
                .query(&[("h_authorization", format!("Bearer {}", bearer))])
                .query(&[("h_content-type", content_type)])
                .body(body),
        )
        .await
    }

    /// Route a GET request through /proxy/forward without an Authorization header.
    /// Use this when authentication is embedded as a query parameter in `dest`.
    pub async fn get_no_auth(
        &self,
        http: &reqwest::Client,
        dest: &str,
    ) -> Result<reqwest::Response, ProviderError> {
        self.send(
            http.get(self.forward_url())
                .query(&[("d", dest), ("api_password", &self.api_password)]),
        )
        .await
    }

    /// Route a POST request through /proxy/forward without an Authorization header.
    /// Use this when authentication is embedded as a query parameter in `dest`.
    pub async fn post_form_no_auth(
        &self,
        http: &reqwest::Client,
        dest: &str,
        form_body: String,
    ) -> Result<reqwest::Response, ProviderError> {
        self.send(
            http.post(self.forward_url())
                .query(&[("d", dest), ("api_password", &self.api_password)])
                .query(&[("h_content-type", "application/x-www-form-urlencoded")])
                .body(form_body),
        )
        .await
    }

    /// Route a POST with a raw binary body through /proxy/forward without Authorization.
    pub async fn post_raw_no_auth(
        &self,
        http: &reqwest::Client,
        dest: &str,
        content_type: &str,
        body: Vec<u8>,
    ) -> Result<reqwest::Response, ProviderError> {
        self.send(
            http.post(self.forward_url())
                .query(&[("d", dest), ("api_password", &self.api_password)])
                .query(&[("h_content-type", content_type)])
                .body(body),
        )
        .await
    }

    /// Fetch MediaFlow's public IP from its /proxy/ip endpoint.
    pub async fn get_public_ip(&self, http: &reqwest::Client) -> Result<String, ProviderError> {
        let url = format!(
            "{}/proxy/ip?api_password={}",
            self.base_url,
            urlencode(&self.api_password)
        );
        let resp = http
            .get(&url)
            .send()
            .await
            .map_err(|e| self.map_ip_lookup_request_error(e))?;
        let resp = self.check_ip_lookup_response(resp).await?;
        let json: serde_json::Value = resp.json().await.map_err(|e| {
            ProviderError::api(
                format!(
                    "MediaFlow returned an invalid /proxy/ip response at {}: {e}",
                    self.base_url
                ),
                MEDIAFLOW_IP_ERROR_VIDEO,
            )
        })?;
        json.get("ip")
            .and_then(|v| v.as_str())
            .map(str::to_string)
            .ok_or_else(|| {
                ProviderError::api(
                    format!(
                        "MediaFlow /proxy/ip response missing IP at {}",
                        self.base_url
                    ),
                    MEDIAFLOW_IP_ERROR_VIDEO,
                )
            })
    }

    /// Check whether a proxy URL points to a loopback or private address.
    /// When true, the addon should call debrid directly with the user's IP
    /// instead of routing through MediaFlow.
    pub fn is_local(proxy_url: &str) -> bool {
        let Ok(parsed) = url::Url::parse(proxy_url) else {
            return false;
        };
        let Some(host) = parsed.host_str() else {
            return false;
        };
        let host = host.to_lowercase();

        if matches!(
            host.as_str(),
            "localhost" | "ip6-localhost" | "ip6-loopback"
        ) {
            return true;
        }

        if let Ok(addr) = host.parse::<std::net::IpAddr>() {
            return addr.is_loopback()
                || match addr {
                    std::net::IpAddr::V4(v4) => {
                        v4.is_private() || v4.is_link_local() || v4.is_unspecified()
                    }
                    std::net::IpAddr::V6(v6) => {
                        let oct = v6.octets();
                        v6.is_unspecified()
                            || (oct[0] & 0xfe) == 0xfc  // fc00::/7 unique-local
                            || (oct[0] == 0xfe && (oct[1] & 0xc0) == 0x80) // fe80::/10 link-local
                    }
                };
        }

        false
    }
}

/// Append key-value pairs as query params to an existing URL string.
pub fn append_query(base: &str, params: &[(&str, &str)]) -> String {
    if params.is_empty() {
        return base.to_string();
    }
    let suffix: String = params
        .iter()
        .map(|(k, v)| format!("{}={}", urlencode(k), urlencode(v)))
        .collect::<Vec<_>>()
        .join("&");
    if base.contains('?') {
        format!("{}&{}", base, suffix)
    } else {
        format!("{}?{}", base, suffix)
    }
}

/// Serialize a slice of key-value pairs as a URL-encoded form body.
pub fn encode_form_body(fields: &[(&str, &str)]) -> String {
    fields
        .iter()
        .map(|(k, v)| format!("{}={}", urlencode(k), urlencode(v)))
        .collect::<Vec<_>>()
        .join("&")
}
