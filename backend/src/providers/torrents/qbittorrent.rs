/// qBittorrent + WebDAV torrent streaming provider.
///
/// Adds magnet/torrent to qBittorrent, waits for download progress, then serves
/// the selected file via credentialed WebDAV URL.
use std::time::Duration;

use reqwest::Client;
use serde_json::Value;

use crate::providers::{
    ProviderError,
    file_selection::{FileEntry, select_torrent_file_index},
    usenet::webdav,
};

#[derive(Debug, Clone)]
struct QbConfig {
    qb_url: String,
    qb_user: String,
    qb_pass: String,
    webdav_url: String,
    webdav_user: String,
    webdav_pass: String,
    downloads_paths: Vec<String>,
    play_video_after: i32,
    seeding_time_limit: i32,
    seeding_ratio_limit: f64,
    category: String,
}

fn parse_config(raw: &Value) -> Result<QbConfig, ProviderError> {
    let str_field = |keys: &[&str]| -> Option<String> {
        keys.iter()
            .find_map(|k| raw.get(*k).and_then(|v| v.as_str()))
            .map(|s| s.to_string())
            .filter(|s| !s.is_empty())
    };

    let qb_url = str_field(&["qbittorrent_url", "qur", "url"]).ok_or_else(|| {
        ProviderError::api(
            "qBittorrent: no qbittorrent_url in config",
            "invalid_config.mp4",
        )
    })?;
    let qb_user = str_field(&["qbittorrent_username", "qus"]).unwrap_or_default();
    let qb_pass = str_field(&["qbittorrent_password", "qpw"]).unwrap_or_default();
    let webdav_url = str_field(&["webdav_url", "wur"]).ok_or_else(|| {
        ProviderError::api("qBittorrent: no webdav_url in config", "invalid_config.mp4")
    })?;
    let webdav_user = str_field(&["webdav_username", "wus"]).unwrap_or_default();
    let webdav_pass = str_field(&["webdav_password", "wpw"]).unwrap_or_default();

    let primary = str_field(&["webdav_downloads_path", "wdp"]).unwrap_or_else(|| "/".to_string());
    let mut downloads_paths = vec![primary];
    if let Some(extra) = raw.get("webdav_extra_paths").or_else(|| raw.get("wep"))
        && let Some(arr) = extra.as_array() {
            for v in arr {
                if let Some(s) = v.as_str() {
                    let t = s.trim();
                    if !t.is_empty() && !downloads_paths.iter().any(|p| p == t) {
                        downloads_paths.push(t.to_string());
                    }
                }
            }
        }

    let play_video_after = raw
        .get("play_video_after")
        .or_else(|| raw.get("pva"))
        .and_then(|v| v.as_i64())
        .unwrap_or(100) as i32;
    let seeding_time_limit = raw
        .get("seeding_time_limit")
        .or_else(|| raw.get("stl"))
        .and_then(|v| v.as_i64())
        .unwrap_or(1440) as i32;
    let seeding_ratio_limit = raw
        .get("seeding_ratio_limit")
        .or_else(|| raw.get("srl"))
        .and_then(|v| v.as_f64())
        .unwrap_or(1.0);
    let category = str_field(&["category", "cat"]).unwrap_or_else(|| "MediaFusion".to_string());

    Ok(QbConfig {
        qb_url: qb_url.trim_end_matches('/').to_string(),
        qb_user,
        qb_pass,
        webdav_url,
        webdav_user,
        webdav_pass,
        downloads_paths,
        play_video_after,
        seeding_time_limit,
        seeding_ratio_limit,
        category,
    })
}

async fn qb_login(http: &Client, cfg: &QbConfig) -> Result<(), ProviderError> {
    let url = format!("{}/api/v2/auth/login", cfg.qb_url);
    let resp = http
        .post(&url)
        .form(&[
            ("username", cfg.qb_user.as_str()),
            ("password", cfg.qb_pass.as_str()),
        ])
        .send()
        .await?;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    if status == reqwest::StatusCode::FORBIDDEN || text.to_lowercase().contains("fail") {
        return Err(ProviderError::api(
            "Invalid qBittorrent credentials",
            "invalid_credentials.mp4",
        ));
    }
    if !status.is_success() {
        return Err(ProviderError::api(
            format!("qBittorrent login failed (HTTP {status})"),
            "qbittorrent_error.mp4",
        ));
    }
    Ok(())
}

async fn qb_torrent_info(
    http: &Client,
    cfg: &QbConfig,
    info_hash: &str,
) -> Result<Option<f64>, ProviderError> {
    let url = format!("{}/api/v2/torrents/info?hashes={info_hash}", cfg.qb_url);
    let arr: Vec<Value> = http.get(&url).send().await?.json().await?;
    Ok(arr
        .first()
        .and_then(|t| t.get("progress").and_then(|v| v.as_f64())))
}

async fn qb_add_torrent(
    http: &Client,
    cfg: &QbConfig,
    magnet: &str,
    info_hash: &str,
    torrent_name: &str,
    torrent_file: Option<&[u8]>,
    is_private: bool,
) -> Result<(), ProviderError> {
    if is_private {
        qb_set_preferences(http, cfg, true).await?;
    }

    let url = format!("{}/api/v2/torrents/add", cfg.qb_url);
    let resp = if let Some(bytes) = torrent_file.filter(|b| !b.is_empty()) {
        let part = reqwest::multipart::Part::bytes(bytes.to_vec())
            .file_name(format!("{torrent_name}.torrent"))
            .mime_str("application/x-bittorrent")
            .map_err(|e| {
                ProviderError::api(format!("torrent multipart: {e}"), "add_torrent_failed.mp4")
            })?;
        http.post(&url)
            .multipart(
                reqwest::multipart::Form::new()
                    .part("torrents", part)
                    .text("savepath", info_hash.to_string())
                    .text("sequentialDownload", "true")
                    .text("category", cfg.category.clone())
                    .text("seedingTimeLimit", cfg.seeding_time_limit.to_string())
                    .text("ratioLimit", cfg.seeding_ratio_limit.to_string()),
            )
            .send()
            .await?
    } else {
        http.post(&url)
            .form(&[
                ("urls", magnet),
                ("savepath", info_hash),
                ("sequentialDownload", "true"),
                ("category", &cfg.category),
                ("seedingTimeLimit", &cfg.seeding_time_limit.to_string()),
                ("ratioLimit", &cfg.seeding_ratio_limit.to_string()),
            ])
            .send()
            .await?
    };

    let status = resp.status();
    let text = resp.text().await.unwrap_or_default().to_lowercase();
    if status.is_success() || is_duplicate_torrent_error(&text) {
        return Ok(());
    }
    Err(ProviderError::api(
        format!("Failed to add torrent to qBittorrent: {text}"),
        "add_torrent_failed.mp4",
    ))
}

fn is_duplicate_torrent_error(text: &str) -> bool {
    [
        "already in the list",
        "already in the download list",
        "torrent is already present",
        "already present",
        "duplicate torrent",
        "is already queued",
    ]
    .iter()
    .any(|phrase| text.contains(phrase))
}

async fn qb_set_preferences(
    http: &Client,
    cfg: &QbConfig,
    disable_dht: bool,
) -> Result<(), ProviderError> {
    if !disable_dht {
        return Ok(());
    }
    let url = format!("{}/api/v2/app/setPreferences", cfg.qb_url);
    let json = serde_json::json!({"dht": false, "pex": false, "lsd": false}).to_string();
    let resp = http
        .post(&url)
        .form(&[("json", json.as_str())])
        .send()
        .await?;
    if resp.status().is_success() {
        Ok(())
    } else {
        Err(ProviderError::api(
            "Failed to set qBittorrent preferences",
            "add_torrent_failed.mp4",
        ))
    }
}

async fn qb_add_magnet(
    http: &Client,
    cfg: &QbConfig,
    magnet: &str,
    info_hash: &str,
) -> Result<(), ProviderError> {
    qb_add_torrent(http, cfg, magnet, info_hash, info_hash, None, false).await
}

async fn wait_for_progress(
    http: &Client,
    cfg: &QbConfig,
    info_hash: &str,
) -> Result<(), ProviderError> {
    let threshold = cfg.play_video_after as f64 / 100.0;
    for _ in 0..60 {
        if let Some(progress) = qb_torrent_info(http, cfg, info_hash).await?
            && progress >= threshold {
                return Ok(());
            }
        tokio::time::sleep(Duration::from_secs(5)).await;
    }
    Err(ProviderError::api(
        "Torrent not downloaded yet",
        "torrent_not_downloaded.mp4",
    ))
}

async fn list_webdav_files_recursive(
    http: &Client,
    cfg: &QbConfig,
    root: &str,
) -> Result<Vec<FileEntry>, ProviderError> {
    let mut files = Vec::new();
    let mut stack = vec![root.to_string()];
    let mut idx = 0usize;

    while let Some(dir) = stack.pop() {
        let hrefs = webdav::list(
            http,
            &cfg.webdav_url,
            dir.trim_start_matches('/'),
            &cfg.webdav_user,
            &cfg.webdav_pass,
        )
        .await?;

        for href in hrefs {
            let name = href.rsplit('/').next().unwrap_or(&href).to_string();
            if name.is_empty() {
                continue;
            }
            let lower = name.to_lowercase();
            if lower.ends_with('/') {
                stack.push(format!("{dir}/{name}"));
            } else if super::super::usenet::is_video_name(&lower) {
                files.push(FileEntry {
                    index: idx,
                    name: href.clone(),
                    size: 0,
                });
                idx += 1;
            }
        }
    }
    Ok(files)
}

async fn find_file(
    http: &Client,
    cfg: &QbConfig,
    info_hash: &str,
    torrent_name: &str,
    filename: Option<&str>,
    season: Option<i32>,
    episode: Option<i32>,
) -> Result<String, ProviderError> {
    for root in &cfg.downloads_paths {
        let path = format!("{}/{}", root.trim_end_matches('/'), info_hash);
        let files = list_webdav_files_recursive(http, cfg, &path).await?;
        if files.is_empty() {
            continue;
        }
        let idx =
            select_torrent_file_index(&files, torrent_name, filename, season, episode, None, None)?;
        return Ok(files[idx].name.clone());
    }
    Err(ProviderError::api(
        "No matching file available for this torrent",
        "no_matching_file.mp4",
    ))
}

pub async fn validate_credentials(http: &Client, config: &Value) -> Result<(), ProviderError> {
    let cfg = parse_config(config)?;
    qb_login(http, &cfg).await?;
    webdav::list(
        http,
        &cfg.webdav_url,
        "",
        &cfg.webdav_user,
        &cfg.webdav_pass,
    )
    .await
    .map_err(|_| ProviderError::api("Invalid WebDAV credentials", "invalid_credentials.mp4"))?;
    Ok(())
}

pub async fn get_video_url(
    http: &Client,
    config: &Value,
    info_hash: &str,
    magnet_link: &str,
    torrent_name: &str,
    filename: Option<&str>,
    season: Option<i32>,
    episode: Option<i32>,
    torrent_file: Option<&[u8]>,
    is_private: bool,
) -> Result<String, ProviderError> {
    let cfg = parse_config(config)?;
    qb_login(http, &cfg).await?;

    if qb_torrent_info(http, &cfg, info_hash).await?.is_none() {
        qb_add_torrent(
            http,
            &cfg,
            magnet_link,
            info_hash,
            torrent_name,
            torrent_file,
            is_private,
        )
        .await?;
    } else if qb_torrent_info(http, &cfg, info_hash)
        .await?
        .map(|p| p * 100.0 < cfg.play_video_after as f64)
        .unwrap_or(true)
    {
        wait_for_progress(http, &cfg, info_hash).await?;
    }

    let file_path = match find_file(
        http,
        &cfg,
        info_hash,
        torrent_name,
        filename,
        season,
        episode,
    )
    .await
    {
        Ok(p) => p,
        Err(_) => {
            qb_add_magnet(http, &cfg, magnet_link, info_hash).await?;
            wait_for_progress(http, &cfg, info_hash).await?;
            find_file(
                http,
                &cfg,
                info_hash,
                torrent_name,
                filename,
                season,
                episode,
            )
            .await?
        }
    };

    Ok(webdav::url_with_creds(
        &cfg.webdav_url,
        &file_path,
        &cfg.webdav_user,
        &cfg.webdav_pass,
    ))
}

/// List info_hashes present as WebDAV download folders.
pub async fn list_downloaded_hashes(http: &Client, config: &Value) -> Vec<String> {
    let cfg = match parse_config(config) {
        Ok(c) => c,
        Err(_) => return Vec::new(),
    };
    let mut merged = std::collections::HashSet::new();
    for root in &cfg.downloads_paths {
        if let Ok(hrefs) = webdav::list(
            http,
            &cfg.webdav_url,
            root.trim_start_matches('/'),
            &cfg.webdav_user,
            &cfg.webdav_pass,
        )
        .await
        {
            for href in hrefs {
                let name = href.trim_end_matches('/');
                if name.len() == 40 && name.chars().all(|c| c.is_ascii_hexdigit()) {
                    merged.insert(name.to_string());
                }
            }
        }
    }
    merged.into_iter().collect()
}

pub async fn delete_all_torrents(http: &Client, config: &Value) -> Result<(), ProviderError> {
    let cfg = parse_config(config)?;
    qb_login(http, &cfg).await?;
    let url = format!("{}/api/v2/torrents/info?filter=completed", cfg.qb_url);
    let arr: Vec<Value> = http.get(&url).send().await?.json().await?;
    let hashes: Vec<String> = arr
        .iter()
        .filter_map(|t| t.get("hash").and_then(|v| v.as_str()).map(str::to_string))
        .collect();
    if hashes.is_empty() {
        return Ok(());
    }
    let del_url = format!("{}/api/v2/torrents/delete", cfg.qb_url);
    http.post(&del_url)
        .form(&[
            ("hashes", hashes.join("|")),
            ("deleteFiles", "true".to_string()),
        ])
        .send()
        .await?;
    Ok(())
}

/// Update cached flags by checking qBittorrent torrent progress == 1.0.
pub async fn update_cache_status(
    http: &Client,
    config: &Value,
    info_hashes: &[String],
) -> std::collections::HashMap<String, bool> {
    let cfg = match parse_config(config) {
        Ok(c) => c,
        Err(_) => return std::collections::HashMap::new(),
    };
    if qb_login(http, &cfg).await.is_err() {
        return std::collections::HashMap::new();
    }
    let joined = info_hashes.join("|");
    let url = format!("{}/api/v2/torrents/info?hashes={joined}", cfg.qb_url);
    let arr: Vec<Value> = match http.get(&url).send().await {
        Ok(r) => r.json().await.unwrap_or_default(),
        Err(_) => return std::collections::HashMap::new(),
    };
    let mut map = std::collections::HashMap::new();
    for t in arr {
        if let (Some(h), Some(p)) = (
            t.get("hash").and_then(|v| v.as_str()),
            t.get("progress").and_then(|v| v.as_f64()),
        ) {
            map.insert(h.to_lowercase(), (p - 1.0).abs() < f64::EPSILON);
        }
    }
    map
}
