/// NzbDAV usenet playback provider.
///
/// NzbDAV exposes a SABnzbd-compatible API and built-in WebDAV on the same host.
/// Path resolution tries multiple folder layouts (Python `_nzbdav_download_path_candidates`).
use std::path::Path;
use std::time::Duration;

use serde_json::Value;

use crate::providers::{
    ProviderError,
    file_selection::{FileEntry, select_usenet_file_index},
};

use super::{sabnzbd, webdav};

fn str_field<'a>(config: &'a Value, keys: &[&str]) -> Option<&'a str> {
    keys.iter()
        .find_map(|k| config.get(*k).and_then(|v| v.as_str()))
        .filter(|s| !s.is_empty())
}

fn job_folder_guesses(download_name: &str, stream_name: &str) -> Vec<String> {
    let mut ordered = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for raw in [download_name, stream_name] {
        let name = raw.trim();
        if !name.is_empty() && seen.insert(name.to_string()) {
            ordered.push(name.to_string());
        }
    }
    ordered
}

fn download_path_candidates(category: &str, download_name: &str) -> Vec<String> {
    let layouts = [
        vec!["content", category, download_name],
        vec!["content", download_name],
        vec![category, download_name],
        vec![download_name],
    ];
    let mut roots = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for segs in layouts {
        let root = format!("/{}", segs.join("/"));
        if seen.insert(root.clone()) {
            roots.push(root);
        }
    }
    roots
}

async fn list_files_in_tree(
    http: &reqwest::Client,
    webdav_base: &str,
    webdav_user: &str,
    webdav_pass: &str,
    root_path: &str,
) -> Result<Vec<FileEntry>, ProviderError> {
    let hrefs = webdav::list(
        http,
        webdav_base,
        root_path.trim_start_matches('/'),
        webdav_user,
        webdav_pass,
    )
    .await?;
    let mut files = Vec::new();
    let mut idx = 0usize;
    for href in hrefs {
        let lower = href.to_lowercase();
        if super::is_video_name(&lower) {
            files.push(FileEntry {
                index: idx,
                name: href,
                size: 0,
            });
            idx += 1;
        }
    }
    Ok(files)
}

async fn find_file_in_downloads(
    http: &reqwest::Client,
    config: &Value,
    download_name: &str,
    stream_name: &str,
    filename: Option<&str>,
    season: i32,
    episode: i32,
    episode_air_date: Option<&str>,
) -> Result<String, ProviderError> {
    let webdav_base = str_field(config, &["url"])
        .ok_or_else(|| ProviderError::api("NzbDAV: no url in config", "invalid_config.mp4"))?;
    let webdav_user = str_field(config, &["webdav_username", "username"]).unwrap_or_default();
    let webdav_pass = str_field(config, &["webdav_password", "password"]).unwrap_or_default();
    let category = str_field(config, &["category", "cat"]).unwrap_or("MediaFusion");

    for folder_guess in job_folder_guesses(download_name, stream_name) {
        for root in download_path_candidates(category, &folder_guess) {
            let files =
                list_files_in_tree(http, webdav_base, webdav_user, webdav_pass, &root).await?;
            if files.is_empty() {
                continue;
            }
            let idx = select_usenet_file_index(
                &files,
                stream_name,
                filename,
                if season > 0 { Some(season) } else { None },
                if episode > 0 { Some(episode) } else { None },
                episode_air_date,
            )?;
            return Ok(files[idx].name.clone());
        }
    }
    Err(ProviderError::api(
        "NzbDAV: no matching video in WebDAV",
        "no_video_file_found.mp4",
    ))
}

pub async fn validate_credentials(
    http: &reqwest::Client,
    config: &Value,
) -> Result<(), ProviderError> {
    let base = str_field(config, &["url"])
        .ok_or_else(|| ProviderError::api("NzbDAV: no url in config", "invalid_config.mp4"))?;
    let api_key = str_field(config, &["api_key", "apikey"])
        .ok_or_else(|| ProviderError::api("NzbDAV: no api_key in config", "invalid_config.mp4"))?;
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
            "Failed to connect to NzbDAV API",
            "invalid_credentials.mp4",
        ));
    }

    let webdav_user = str_field(config, &["webdav_username", "username"]).unwrap_or_default();
    let webdav_pass = str_field(config, &["webdav_password", "password"]).unwrap_or_default();
    webdav::list(http, base, "", webdav_user, webdav_pass)
        .await
        .map_err(|_| {
            ProviderError::api(
                "Failed to connect to NzbDAV WebDAV",
                "invalid_credentials.mp4",
            )
        })?;
    Ok(())
}

pub async fn get_url(
    http: &reqwest::Client,
    config: &Value,
    submission_url: &str,
    fallback_url: &str,
    stream_name: &str,
    filename: Option<&str>,
    season: i32,
    episode: i32,
    episode_air_date: Option<&str>,
) -> Result<String, ProviderError> {
    let base = str_field(config, &["url"])
        .ok_or_else(|| ProviderError::api("NzbDAV: no url in config", "invalid_config.mp4"))?;
    let api_key = str_field(config, &["api_key", "apikey"])
        .ok_or_else(|| ProviderError::api("NzbDAV: no api_key in config", "invalid_config.mp4"))?;
    let webdav_user = str_field(config, &["webdav_username", "username"]).unwrap_or_default();
    let webdav_pass = str_field(config, &["webdav_password", "password"]).unwrap_or_default();
    let api_url = format!("{}/api", base.trim_end_matches('/'));

    let nzo_id = sabnzbd::submit_nzb(
        http,
        &api_url,
        api_key,
        submission_url,
        fallback_url,
        stream_name,
    )
    .await?;
    let dir_name = poll_history(http, &api_url, api_key, &nzo_id).await?;
    let file_path = find_file_in_downloads(
        http,
        config,
        &dir_name,
        stream_name,
        filename,
        season,
        episode,
        episode_air_date,
    )
    .await?;
    Ok(webdav::url_with_creds(
        base,
        &file_path,
        webdav_user,
        webdav_pass,
    ))
}

async fn poll_history(
    http: &reqwest::Client,
    api_url: &str,
    api_key: &str,
    nzo_id: &str,
) -> Result<String, ProviderError> {
    for _ in 0..60 {
        let h: Value = http
            .get(api_url)
            .query(&[
                ("mode", "history"),
                ("limit", "200"),
                ("apikey", api_key),
                ("output", "json"),
            ])
            .send()
            .await?
            .json()
            .await?;
        if let Some(slots) = h
            .get("history")
            .and_then(|h| h.get("slots"))
            .and_then(|v| v.as_array())
        {
            for slot in slots {
                if slot.get("nzo_id").and_then(|v| v.as_str()) != Some(nzo_id) {
                    continue;
                }
                let status = slot.get("status").and_then(|v| v.as_str()).unwrap_or("");
                if status == "Completed" || status == "Moved" {
                    let storage = slot.get("storage").and_then(|v| v.as_str()).unwrap_or("");
                    let dir = Path::new(storage)
                        .file_name()
                        .and_then(|n| n.to_str())
                        .unwrap_or(nzo_id)
                        .to_string();
                    return Ok(dir);
                } else if status == "Failed" {
                    return Err(ProviderError::api(
                        "NzbDAV: download failed",
                        "usenet_transfer_error.mp4",
                    ));
                }
            }
        }
        tokio::time::sleep(Duration::from_secs(5)).await;
    }
    Err(ProviderError::api(
        "NzbDAV: download timed out",
        "usenet_transfer_error.mp4",
    ))
}

pub async fn list_downloaded_names(http: &reqwest::Client, config: &Value) -> Vec<String> {
    let base = match str_field(config, &["url"]) {
        Some(u) => u,
        None => return Vec::new(),
    };
    let api_key = match str_field(config, &["api_key", "apikey"]) {
        Some(k) => k,
        None => return Vec::new(),
    };
    let api_url = format!("{}/api", base.trim_end_matches('/'));
    let h: Value = match http
        .get(&api_url)
        .query(&[
            ("mode", "history"),
            ("limit", "500"),
            ("apikey", api_key),
            ("output", "json"),
        ])
        .send()
        .await
    {
        Ok(r) => r.json().await.unwrap_or(Value::Null),
        Err(_) => return Vec::new(),
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

pub async fn delete_all(http: &reqwest::Client, config: &Value) -> Result<(), ProviderError> {
    let base = str_field(config, &["url"])
        .ok_or_else(|| ProviderError::api("NzbDAV: no url", "invalid_config.mp4"))?;
    let api_key = str_field(config, &["api_key", "apikey"])
        .ok_or_else(|| ProviderError::api("NzbDAV: no api_key", "invalid_config.mp4"))?;
    let api_url = format!("{}/api", base.trim_end_matches('/'));
    let h: Value = http
        .get(&api_url)
        .query(&[
            ("mode", "history"),
            ("limit", "500"),
            ("apikey", api_key),
            ("output", "json"),
        ])
        .send()
        .await?
        .json()
        .await?;
    if let Some(slots) = h
        .get("history")
        .and_then(|h| h.get("slots"))
        .and_then(|v| v.as_array())
    {
        for slot in slots {
            if slot.get("status").and_then(|v| v.as_str()) == Some("Completed") {
                if let Some(nzo_id) = slot.get("nzo_id").and_then(|v| v.as_str()) {
                    let _ = http
                        .get(&api_url)
                        .query(&[
                            ("mode", "history"),
                            ("name", "delete"),
                            ("value", nzo_id),
                            ("apikey", api_key),
                            ("output", "json"),
                        ])
                        .send()
                        .await;
                }
            }
        }
    }
    Ok(())
}

pub async fn update_cache_status(
    http: &reqwest::Client,
    config: &Value,
    stream_names: &[String],
) -> std::collections::HashMap<String, bool> {
    let completed: std::collections::HashSet<String> = list_downloaded_names(http, config)
        .await
        .into_iter()
        .map(|n| n.to_lowercase())
        .collect();
    stream_names
        .iter()
        .map(|n| (n.clone(), completed.contains(&n.to_lowercase())))
        .collect()
}
