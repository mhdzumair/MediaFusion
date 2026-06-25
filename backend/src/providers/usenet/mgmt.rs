/// Usenet provider management operations: validate, cache update, list, delete.
use reqwest::Client;
use serde_json::Value;

use crate::providers::ProviderError;

use super::{easynews, nzbdav, torbox};

fn str_field<'a>(config: &'a Value, keys: &[&str]) -> Option<&'a str> {
    keys.iter()
        .find_map(|k| config.get(*k).and_then(|v| v.as_str()))
        .filter(|s| !s.is_empty())
}

pub async fn validate_sabnzbd(http: &Client, config: &Value) -> Result<(), ProviderError> {
    let base = str_field(config, &["url", "base_url"])
        .ok_or_else(|| ProviderError::api("SABnzbd: no url in config", "invalid_config.mp4"))?;
    let api_key = str_field(config, &["api_key", "apikey"])
        .ok_or_else(|| ProviderError::api("SABnzbd: no api_key in config", "invalid_config.mp4"))?;
    let api_url = format!("{}/api", base.trim_end_matches('/'));
    let v: Value = http
        .get(&api_url)
        .query(&[("mode", "version"), ("apikey", api_key), ("output", "json")])
        .send()
        .await?
        .json()
        .await?;
    if v.get("version").is_none() {
        return Err(ProviderError::api(
            "Failed to get SABnzbd version",
            "invalid_credentials.mp4",
        ));
    }
    if let Some(webdav_url) = str_field(config, &["webdav_url"])
        && !webdav_url.is_empty() {
            let user = str_field(config, &["webdav_username", "username"]).unwrap_or_default();
            let pass = str_field(config, &["webdav_password", "password"]).unwrap_or_default();
            super::webdav::list(http, webdav_url, "", user, pass)
                .await
                .map_err(|_| {
                    ProviderError::api("Failed to verify WebDAV", "invalid_credentials.mp4")
                })?;
        }
    Ok(())
}

pub async fn validate_nzbget(http: &Client, config: &Value) -> Result<(), ProviderError> {
    let base = str_field(config, &["url"])
        .ok_or_else(|| ProviderError::api("NZBGet: no url in config", "invalid_config.mp4"))?;
    let username = str_field(config, &["username"]).unwrap_or("nzbget");
    let password = str_field(config, &["password"]).unwrap_or("tegbzn6789");
    let rpc_url = format!("{}/jsonrpc", base.trim_end_matches('/'));
    let body = serde_json::json!({
        "method": "version",
        "params": [username, password],
        "id": 1
    });
    let v: Value = http.post(&rpc_url).json(&body).send().await?.json().await?;
    if v.get("result").is_none() {
        return Err(ProviderError::api(
            "Failed to validate NZBGet credentials",
            "invalid_credentials.mp4",
        ));
    }
    Ok(())
}

pub async fn validate_easynews(http: &Client, config: &Value) -> Result<(), ProviderError> {
    let username = str_field(config, &["username", "un", "user"]).unwrap_or_default();
    let password = str_field(config, &["password", "pw", "pass"]).unwrap_or_default();
    if username.is_empty() || password.is_empty() {
        return Err(ProviderError::api(
            "EasyNews credentials missing",
            "invalid_credentials.mp4",
        ));
    }
    match easynews::get_url(http, username, password, "test", 0, 0, None).await {
        Ok(_) => Ok(()),
        Err(e) if e.video_file() == "invalid_credentials.mp4" => Err(e),
        Err(_) => Ok(()),
    }
}

pub async fn list_downloaded_usenet_names(
    http: &Client,
    service: &str,
    provider: &crate::models::user_data::StreamingProvider,
    default_nzbdav: Option<&Value>,
) -> Vec<String> {
    match service {
        "torbox" => {
            let token = provider.token.as_deref().unwrap_or_default();
            torbox::list_usenet_downloads(http, token)
                .await
                .unwrap_or_default()
        }
        "sabnzbd" => {
            if let Some(cfg) = provider.sabnzbd_config.as_ref() {
                sabnzbd_history_names(http, cfg).await
            } else {
                Vec::new()
            }
        }
        "nzbget" => {
            if let Some(cfg) = provider.nzbget_config.as_ref() {
                nzbget_history_names(http, cfg).await
            } else {
                Vec::new()
            }
        }
        "nzbdav" => {
            if let Some(cfg) = provider.nzbdav_config.as_ref().or(default_nzbdav) {
                nzbdav::list_downloaded_names(http, cfg).await
            } else {
                Vec::new()
            }
        }
        "easynews" => Vec::new(),
        _ => Vec::new(),
    }
}

async fn sabnzbd_history_names(http: &Client, config: &Value) -> Vec<String> {
    let Some(base) = str_field(config, &["url", "base_url"]) else {
        return Vec::new();
    };
    let Some(api_key) = str_field(config, &["api_key", "apikey"]) else {
        return Vec::new();
    };
    let api_url = format!("{}/api", base.trim_end_matches('/'));
    let Ok(h) = http
        .get(&api_url)
        .query(&[
            ("mode", "history"),
            ("limit", "500"),
            ("apikey", api_key),
            ("output", "json"),
        ])
        .send()
        .await
    else {
        return Vec::new();
    };
    let Ok(h) = h.json::<Value>().await else {
        return Vec::new();
    };
    h.get("history")
        .and_then(|h| h.get("slots"))
        .and_then(|v| v.as_array())
        .map(|slots| {
            slots
                .iter()
                .filter(|s| {
                    matches!(
                        s.get("status").and_then(|v| v.as_str()),
                        Some("Completed") | Some("Moved")
                    )
                })
                .filter_map(|s| s.get("name").and_then(|v| v.as_str()).map(str::to_string))
                .collect()
        })
        .unwrap_or_default()
}

async fn nzbget_history_names(http: &Client, config: &Value) -> Vec<String> {
    let Some(base) = str_field(config, &["url"]) else {
        return Vec::new();
    };
    let username = str_field(config, &["username"]).unwrap_or("nzbget");
    let password = str_field(config, &["password"]).unwrap_or("tegbzn6789");
    let rpc_url = format!("{}/jsonrpc", base.trim_end_matches('/'));
    let body = serde_json::json!({
        "method": "history",
        "params": [username, password],
        "id": 1
    });
    let Ok(v) = http.post(&rpc_url).json(&body).send().await else {
        return Vec::new();
    };
    let Ok(v) = v.json::<Value>().await else {
        return Vec::new();
    };
    v.get("result")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter(|e| e.get("Status").and_then(|v| v.as_str()) == Some("SUCCESS"))
                .filter_map(|e| e.get("Name").and_then(|v| v.as_str()).map(str::to_string))
                .collect()
        })
        .unwrap_or_default()
}

pub async fn delete_all_usenet(
    http: &Client,
    service: &str,
    provider: &crate::models::user_data::StreamingProvider,
    default_nzbdav: Option<&Value>,
) -> Result<(), ProviderError> {
    match service {
        "torbox" => {
            let token = provider.token.as_deref().unwrap_or_default();
            torbox::delete_all_usenet(http, token).await
        }
        "sabnzbd" => {
            let cfg = provider.sabnzbd_config.as_ref().ok_or_else(|| {
                ProviderError::api("SABnzbd not configured", "invalid_config.mp4")
            })?;
            sabnzbd_delete_all(http, cfg).await
        }
        "nzbget" => {
            let cfg = provider
                .nzbget_config
                .as_ref()
                .ok_or_else(|| ProviderError::api("NZBGet not configured", "invalid_config.mp4"))?;
            nzbget_delete_all(http, cfg).await
        }
        "nzbdav" => {
            let cfg = provider
                .nzbdav_config
                .as_ref()
                .or(default_nzbdav)
                .ok_or_else(|| ProviderError::api("NzbDAV not configured", "invalid_config.mp4"))?;
            nzbdav::delete_all(http, cfg).await
        }
        "easynews" => Ok(()),
        other => Err(ProviderError::api(
            format!("Usenet delete-all not supported for '{other}'"),
            "provider_error.mp4",
        )),
    }
}

async fn sabnzbd_delete_all(http: &Client, config: &Value) -> Result<(), ProviderError> {
    let base = str_field(config, &["url", "base_url"])
        .ok_or_else(|| ProviderError::api("SABnzbd: no url", "invalid_config.mp4"))?;
    let api_key = str_field(config, &["api_key", "apikey"])
        .ok_or_else(|| ProviderError::api("SABnzbd: no api_key", "invalid_config.mp4"))?;
    let api_url = format!("{}/api", base.trim_end_matches('/'));
    let _ = http
        .get(&api_url)
        .query(&[
            ("mode", "history"),
            ("name", "delete"),
            ("value", "all"),
            ("apikey", api_key),
            ("output", "json"),
        ])
        .send()
        .await?;
    Ok(())
}

async fn nzbget_delete_all(http: &Client, config: &Value) -> Result<(), ProviderError> {
    let base = str_field(config, &["url"])
        .ok_or_else(|| ProviderError::api("NZBGet: no url", "invalid_config.mp4"))?;
    let username = str_field(config, &["username"]).unwrap_or("nzbget");
    let password = str_field(config, &["password"]).unwrap_or("tegbzn6789");
    let rpc_url = format!("{}/jsonrpc", base.trim_end_matches('/'));
    let body = serde_json::json!({
        "method": "editqueue",
        "params": [username, password, "HistoryAll", 0, ""],
        "id": 1
    });
    let _ = http.post(&rpc_url).json(&body).send().await?;
    Ok(())
}

pub async fn update_usenet_cache_status(
    http: &Client,
    service: &str,
    provider: &crate::models::user_data::StreamingProvider,
    stream_names: &[String],
    default_nzbdav: Option<&Value>,
) -> std::collections::HashMap<String, bool> {
    match service {
        "torbox" => {
            let token = provider.token.as_deref().unwrap_or_default();
            torbox::update_usenet_cache_status(http, token, stream_names).await
        }
        "sabnzbd" => {
            if let Some(cfg) = provider.sabnzbd_config.as_ref() {
                let completed: std::collections::HashSet<_> = sabnzbd_history_names(http, cfg)
                    .await
                    .into_iter()
                    .map(|n| n.to_lowercase())
                    .collect();
                stream_names
                    .iter()
                    .map(|n| (n.clone(), completed.contains(&n.to_lowercase())))
                    .collect()
            } else {
                std::collections::HashMap::new()
            }
        }
        "nzbget" => {
            if let Some(cfg) = provider.nzbget_config.as_ref() {
                let completed: std::collections::HashSet<_> = nzbget_history_names(http, cfg)
                    .await
                    .into_iter()
                    .map(|n| n.to_lowercase())
                    .collect();
                stream_names
                    .iter()
                    .map(|n| (n.clone(), completed.contains(&n.to_lowercase())))
                    .collect()
            } else {
                std::collections::HashMap::new()
            }
        }
        "nzbdav" => {
            if let Some(cfg) = provider.nzbdav_config.as_ref().or(default_nzbdav) {
                nzbdav::update_cache_status(http, cfg, stream_names).await
            } else {
                std::collections::HashMap::new()
            }
        }
        "easynews" => std::collections::HashMap::new(),
        _ => std::collections::HashMap::new(),
    }
}
