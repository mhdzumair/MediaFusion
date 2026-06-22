/// Provider credential validation dispatcher.
///
/// Mirrors Python `workers/providers/validator.py` + `mapper.VALIDATE_CREDENTIALS_FUNCTIONS`.
use reqwest::Client;
use serde_json::Value;

use crate::models::user_data::UserData;

#[derive(Debug, Clone)]
pub struct ValidationResult {
    pub ok: bool,
    pub message: Option<String>,
}

impl ValidationResult {
    pub fn success() -> Self {
        Self {
            ok: true,
            message: None,
        }
    }

    pub fn error(message: impl Into<String>) -> Self {
        Self {
            ok: false,
            message: Some(message.into()),
        }
    }
}

fn provider_token(p: &crate::models::user_data::StreamingProvider) -> Option<&str> {
    p.token.as_deref().filter(|t| !t.is_empty())
}

async fn validate_one(
    http: &Client,
    provider: &crate::models::user_data::StreamingProvider,
    user_ip: Option<&str>,
    default_nzbdav: Option<&Value>,
) -> ValidationResult {
    match provider.service.as_str() {
        "realdebrid" => {
            let token = match provider_token(provider) {
                Some(t) => t,
                None => return ValidationResult::error("Real-Debrid token is missing"),
            };
            match super::torrents::realdebrid::resolve_bearer(http, token).await {
                Ok(bearer) => {
                    let url = "https://api.real-debrid.com/rest/1.0/user";
                    let mut req = http.get(url).bearer_auth(&bearer);
                    if let Some(ip) = user_ip {
                        req = req.query(&[("ip", ip)]);
                    }
                    match req.send().await {
                        Ok(r) if r.status().is_success() => ValidationResult::success(),
                        Ok(r) => ValidationResult::error(format!(
                            "Failed to verify Real-Debrid credential (HTTP {})",
                            r.status()
                        )),
                        Err(e) => ValidationResult::error(format!(
                            "Failed to verify Real-Debrid credential: {e}"
                        )),
                    }
                }
                Err(e) => {
                    ValidationResult::error(format!("Failed to verify Real-Debrid credential: {e}"))
                }
            }
        }
        "torbox" => {
            let token = match provider_token(provider) {
                Some(t) => t,
                None => return ValidationResult::error("TorBox token is missing"),
            };
            match http
                .get("https://api.torbox.app/v1/api/user/me")
                .bearer_auth(token)
                .send()
                .await
            {
                Ok(r) if r.status().is_success() => ValidationResult::success(),
                Ok(r) => ValidationResult::error(format!(
                    "Failed to validate TorBox credentials (HTTP {})",
                    r.status()
                )),
                Err(e) => {
                    ValidationResult::error(format!("Failed to validate TorBox credentials: {e}"))
                }
            }
        }
        "alldebrid" => {
            let token = match provider_token(provider) {
                Some(t) => t,
                None => return ValidationResult::error("AllDebrid token is missing"),
            };
            match http
                .get("https://api.alldebrid.com/v4/user")
                .query(&[("agent", "mediafusion"), ("apikey", token)])
                .send()
                .await
            {
                Ok(r) if r.status().is_success() => ValidationResult::success(),
                Ok(r) => ValidationResult::error(format!(
                    "Failed to validate AllDebrid credentials (HTTP {})",
                    r.status()
                )),
                Err(e) => ValidationResult::error(format!(
                    "Failed to validate AllDebrid credentials: {e}"
                )),
            }
        }
        "premiumize" => {
            let token = match provider_token(provider) {
                Some(t) => t,
                None => return ValidationResult::error("Premiumize token is missing"),
            };
            match http
                .get("https://www.premiumize.me/api/account/info")
                .query(&[("apikey", token)])
                .send()
                .await
            {
                Ok(r) if r.status().is_success() => ValidationResult::success(),
                Ok(r) => ValidationResult::error(format!(
                    "Failed to validate Premiumize credentials (HTTP {})",
                    r.status()
                )),
                Err(e) => ValidationResult::error(format!(
                    "Failed to validate Premiumize credentials: {e}"
                )),
            }
        }
        "debridlink" => {
            let token = match provider_token(provider) {
                Some(t) => t,
                None => return ValidationResult::error("DebridLink token is missing"),
            };
            match http
                .get("https://debrid-link.com/api/v2/user")
                .bearer_auth(token)
                .send()
                .await
            {
                Ok(r) if r.status().is_success() => ValidationResult::success(),
                Ok(r) => ValidationResult::error(format!(
                    "Failed to validate DebridLink credentials (HTTP {})",
                    r.status()
                )),
                Err(e) => ValidationResult::error(format!(
                    "Failed to validate DebridLink credentials: {e}"
                )),
            }
        }
        "offcloud" => {
            let token = match provider_token(provider) {
                Some(t) => t,
                None => return ValidationResult::error("OffCloud token is missing"),
            };
            match http
                .get("https://offcloud.com/api/account/info")
                .bearer_auth(token)
                .send()
                .await
            {
                Ok(r) if r.status().is_success() => ValidationResult::success(),
                Ok(r) if r.status() == reqwest::StatusCode::UNAUTHORIZED => {
                    ValidationResult::error("OffCloud API key is invalid or has expired")
                }
                Ok(r) => ValidationResult::error(format!(
                    "Failed to validate OffCloud credentials (HTTP {})",
                    r.status()
                )),
                Err(e) => {
                    ValidationResult::error(format!("Failed to validate OffCloud credentials: {e}"))
                }
            }
        }
        "seedr" => {
            let token = match provider_token(provider) {
                Some(t) => t,
                None => return ValidationResult::error("Seedr token is missing"),
            };
            match http
                .get("https://v2.seedr.cc/api/v0.1/p/tasks")
                .bearer_auth(token)
                .send()
                .await
            {
                Ok(r) if r.status().is_success() => ValidationResult::success(),
                Ok(r) if r.status() == reqwest::StatusCode::UNAUTHORIZED => {
                    ValidationResult::error("Seedr token is expired or invalid")
                }
                Ok(r) => ValidationResult::error(format!(
                    "Failed to validate Seedr credentials (HTTP {})",
                    r.status()
                )),
                Err(e) => {
                    ValidationResult::error(format!("Failed to validate Seedr credentials: {e}"))
                }
            }
        }
        "stremthru" => {
            let token = match provider_token(provider) {
                Some(t) => t,
                None => return ValidationResult::error("StremThru token is missing"),
            };
            match super::torrents::stremthru::validate_credentials(http, token).await {
                Ok(()) => ValidationResult::success(),
                Err(e) => ValidationResult::error(format!(
                    "Failed to validate StremThru credentials: {e}"
                )),
            }
        }
        "torrin" => {
            let token = match provider_token(provider) {
                Some(t) => t,
                None => return ValidationResult::error("Torrin token is missing"),
            };
            match super::torrents::torrin::validate_credentials(http, token).await {
                Ok(()) => ValidationResult::success(),
                Err(e) => {
                    ValidationResult::error(format!("Failed to validate Torrin credentials: {e}"))
                }
            }
        }
        "easydebrid" => {
            let token = match provider_token(provider) {
                Some(t) => t,
                None => return ValidationResult::error("EasyDebrid token is missing"),
            };
            match http
                .get("https://easydebrid.com/api/v1/user")
                .bearer_auth(token)
                .send()
                .await
            {
                Ok(r) if r.status().is_success() => ValidationResult::success(),
                Ok(r) => ValidationResult::error(format!(
                    "Failed to validate EasyDebrid credentials (HTTP {})",
                    r.status()
                )),
                Err(e) => ValidationResult::error(format!(
                    "Failed to validate EasyDebrid credentials: {e}"
                )),
            }
        }
        "debrider" => {
            let token = match provider_token(provider) {
                Some(t) => t,
                None => return ValidationResult::error("Debrider token is missing"),
            };
            match super::torrents::debrider::validate_credentials(http, token, user_ip).await {
                Ok(()) => ValidationResult::success(),
                Err(e) => {
                    ValidationResult::error(format!("Failed to validate Debrider credentials: {e}"))
                }
            }
        }
        "pikpak" => {
            let email = provider.email.as_deref().unwrap_or_default();
            let password = provider.password.as_deref().unwrap_or_default();
            if email.is_empty() || password.is_empty() {
                return ValidationResult::error("PikPak email and password are required");
            }
            match super::torrents::pikpak::login(http, email, password).await {
                Ok(_) => ValidationResult::success(),
                Err(e) => {
                    ValidationResult::error(format!("Failed to validate PikPak credentials: {e}"))
                }
            }
        }
        "qbittorrent" => {
            let cfg = match provider.qbittorrent_config.as_ref() {
                Some(c) => c,
                None => return ValidationResult::error("qBittorrent configuration is missing"),
            };
            match super::torrents::qbittorrent::validate_credentials(http, cfg).await {
                Ok(()) => ValidationResult::success(),
                Err(e) => {
                    ValidationResult::error(format!("Failed to verify qBittorrent/WebDAV: {e}"))
                }
            }
        }
        "sabnzbd" => {
            let cfg = match provider.sabnzbd_config.as_ref() {
                Some(c) => c,
                None => return ValidationResult::error("SABnzbd configuration is missing"),
            };
            match super::usenet::mgmt::validate_sabnzbd(http, cfg).await {
                Ok(()) => ValidationResult::success(),
                Err(e) => {
                    ValidationResult::error(format!("Failed to validate SABnzbd credentials: {e}"))
                }
            }
        }
        "nzbget" => {
            let cfg = match provider.nzbget_config.as_ref() {
                Some(c) => c,
                None => return ValidationResult::error("NZBGet configuration is missing"),
            };
            match super::usenet::mgmt::validate_nzbget(http, cfg).await {
                Ok(()) => ValidationResult::success(),
                Err(e) => {
                    ValidationResult::error(format!("Failed to validate NZBGet credentials: {e}"))
                }
            }
        }
        "nzbdav" => {
            let cfg = provider.nzbdav_config.as_ref().or(default_nzbdav);
            let cfg = match cfg {
                Some(c) => c,
                None => return ValidationResult::error("NzbDAV configuration is missing"),
            };
            match super::usenet::nzbdav::validate_credentials(http, cfg).await {
                Ok(()) => ValidationResult::success(),
                Err(e) => {
                    ValidationResult::error(format!("Failed to validate NzbDAV credentials: {e}"))
                }
            }
        }
        "easynews" => {
            let cfg = match provider.easynews_config.as_ref() {
                Some(c) => c,
                None => return ValidationResult::error("EasyNews configuration is missing"),
            };
            match super::usenet::mgmt::validate_easynews(http, cfg).await {
                Ok(()) => ValidationResult::success(),
                Err(e) => {
                    ValidationResult::error(format!("Failed to validate EasyNews credentials: {e}"))
                }
            }
        }
        "p2p" | "stremio_nntp" => ValidationResult::success(),
        other => {
            tracing::debug!("validate_provider_credentials: skip unsupported service '{other}'");
            ValidationResult::success()
        }
    }
}

pub async fn validate_provider_credentials(
    http: &Client,
    http_no_proxy: Option<&Client>,
    excluded_services: &[String],
    user_data: &UserData,
    user_ip: Option<&str>,
    default_nzbdav: Option<&Value>,
) -> ValidationResult {
    for provider in user_data.get_active_providers(default_nzbdav) {
        let client = if let Some(c) = http_no_proxy {
            if excluded_services.iter().any(|e| e == &provider.service) {
                c
            } else {
                http
            }
        } else {
            http
        };
        let result = validate_one(client, &provider, user_ip, default_nzbdav).await;
        if !result.ok {
            return result;
        }
    }
    ValidationResult::success()
}

pub fn validation_error_response(result: &ValidationResult) -> Option<String> {
    if result.ok {
        None
    } else {
        Some(
            result
                .message
                .clone()
                .unwrap_or_else(|| "Streaming provider credential validation failed".to_string()),
        )
    }
}

pub fn client_ip_from_headers(headers: &axum::http::HeaderMap) -> Option<String> {
    headers
        .get("x-forwarded-for")
        .and_then(|v| v.to_str().ok())
        .and_then(|s| s.split(',').next())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .or_else(|| {
            headers
                .get("x-real-ip")
                .and_then(|v| v.to_str().ok())
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty())
        })
}
