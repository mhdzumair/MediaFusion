//! HighWay streaming provider.
//!
//! HighWay (<https://high-way.me>) keeps torrents in a shared cache and exposes
//! them through a bearer-token transfer API
//! (OpenAPI: <https://cloud.high-way.me/docs/openapi/highway-transfers.yaml>):
//!
//! - `GET ?stats=1` → account info (credential check)
//! - `GET ?service=torrent&check=<h1>,<h2>` → cache check, up to 100 hashes
//! - `POST magnet=<magnet>` → known content is linked at once (`completed`),
//!   anything else waits in a queue (`queued`, 32-hex queue id)
//! - `GET ?id=<id>&service=torrent` → transfer with `files[]`, each carrying a
//!   signed `download_url` (valid ~24 h, range requests)
//! - `DELETE ?id=<id>&service=torrent` → removes the transfer from the account
//!
//! Uncached torrents take minutes to load, so playback does not block: like
//! Torrin it starts the download and answers with the "downloading" video.

use serde_json::Value;

use crate::parser::episode_detector::is_video_file;
use crate::providers::torrents::transport::MediaFlowForward;
use crate::providers::{ProviderError, file_selection::select_debrid_file_index, response_json};

use super::DownloadedTorrent;

const BASE_URL: &str = "https://cloud.high-way.me/center-api/transfers.php";
const CHECK_CHUNK: usize = 100;

// ─── HTTP helpers ─────────────────────────────────────────────────────────────

/// Map HighWay's JSON errors (`{"error": code, "message": text}`) and HTTP status.
async fn hw_json(resp: reqwest::Response, context: &str) -> Result<Value, ProviderError> {
    let status = resp.status();
    let body = response_json(resp, context).await?;
    if status.is_success() {
        return Ok(body);
    }
    let code = body.get("error").and_then(|v| v.as_str()).unwrap_or("");
    let message = body
        .get("message")
        .and_then(|v| v.as_str())
        .unwrap_or("Unknown HighWay error");
    let video = match status.as_u16() {
        401 | 403 => "invalid_token.mp4",
        404 => "torrent_not_downloaded.mp4",
        409 if code == "cloud_storage_limit_reached" => "not_enough_space.mp4",
        429 => "too_many_requests.mp4",
        _ => "api_error.mp4",
    };
    Err(ProviderError::api(
        format!("HighWay error {code} (HTTP {status}): {message}"),
        video,
    ))
}

async fn hw_get(
    http: &reqwest::Client,
    base: &str,
    token: &str,
    query: &[(&str, &str)],
) -> Result<Value, ProviderError> {
    let resp = http
        .get(base)
        .bearer_auth(token)
        .query(query)
        .send()
        .await?;
    hw_json(resp, "highway get").await
}

// ─── Public validation ────────────────────────────────────────────────────────

pub async fn validate_credentials(
    http: &reqwest::Client,
    token: &str,
) -> Result<(), ProviderError> {
    validate_credentials_at(http, BASE_URL, token).await
}

async fn validate_credentials_at(
    http: &reqwest::Client,
    base: &str,
    token: &str,
) -> Result<(), ProviderError> {
    hw_get(http, base, token, &[("stats", "1")]).await?;
    Ok(())
}

// ─── Cache check ──────────────────────────────────────────────────────────────

/// Return the lower-case hashes that are fully loaded in HighWay's cache.
pub async fn check_cached(http: &reqwest::Client, token: &str, hashes: &[String]) -> Vec<String> {
    check_cached_at(http, BASE_URL, token, hashes).await
}

async fn check_cached_at(
    http: &reqwest::Client,
    base: &str,
    token: &str,
    hashes: &[String],
) -> Vec<String> {
    let mut cached = Vec::new();
    for chunk in hashes.chunks(CHECK_CHUNK) {
        let joined = chunk
            .iter()
            .map(|h| h.to_lowercase())
            .collect::<Vec<_>>()
            .join(",");
        let body = match hw_get(
            http,
            base,
            token,
            &[("service", "torrent"), ("check", joined.as_str())],
        )
        .await
        {
            Ok(body) => body,
            Err(e) => {
                tracing::warn!("HighWay cache check failed: {e}");
                continue;
            }
        };
        if let Some(results) = body.get("results").and_then(|v| v.as_object()) {
            for (hash, result) in results {
                if result.get("ready").and_then(|v| v.as_bool()) == Some(true) {
                    cached.push(hash.to_lowercase());
                }
            }
        }
    }
    cached
}

// ─── Transfers ────────────────────────────────────────────────────────────────

async fn list_transfers(
    http: &reqwest::Client,
    base: &str,
    token: &str,
) -> Result<Vec<Value>, ProviderError> {
    let body = hw_get(http, base, token, &[("service", "torrent")]).await?;
    Ok(body
        .get("transfers")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default())
}

/// Return all completed torrents in the user's HighWay account.
pub async fn list_downloaded_torrents(
    http: &reqwest::Client,
    token: &str,
) -> Result<Vec<DownloadedTorrent>, ProviderError> {
    list_downloaded_torrents_at(http, BASE_URL, token).await
}

async fn list_downloaded_torrents_at(
    http: &reqwest::Client,
    base: &str,
    token: &str,
) -> Result<Vec<DownloadedTorrent>, ProviderError> {
    let items = list_transfers(http, base, token).await?;
    let mut results = Vec::with_capacity(items.len());
    for item in items {
        if item.get("state").and_then(|v| v.as_str()) != Some("completed") {
            continue;
        }
        let Some(info_hash) = item.get("hash").and_then(|v| v.as_str()) else {
            continue;
        };
        let info_hash = info_hash.to_lowercase();
        results.push(DownloadedTorrent {
            id: item
                .get("id")
                .and_then(|v| v.as_str())
                .unwrap_or_default()
                .to_string(),
            name: item
                .get("name")
                .and_then(|v| v.as_str())
                .unwrap_or(&info_hash)
                .to_string(),
            size: item.get("size").and_then(|v| v.as_i64()).unwrap_or(0),
            info_hash,
            raw: Value::Null,
        });
    }
    Ok(results)
}

/// Remove every torrent from the user's HighWay account (only the account's
/// link to the content; the shared cache stays).
pub async fn delete_all_torrents(http: &reqwest::Client, token: &str) -> Result<(), ProviderError> {
    delete_all_torrents_at(http, BASE_URL, token).await
}

async fn delete_all_torrents_at(
    http: &reqwest::Client,
    base: &str,
    token: &str,
) -> Result<(), ProviderError> {
    for item in list_transfers(http, base, token).await? {
        let Some(id) = item.get("id").and_then(|v| v.as_str()) else {
            continue;
        };
        let resp = http
            .delete(base)
            .bearer_auth(token)
            .query(&[("id", id), ("service", "torrent")])
            .send()
            .await?;
        hw_json(resp, "highway delete").await?;
    }
    Ok(())
}

// ─── Playback ─────────────────────────────────────────────────────────────────

/// Pick the file to play. `file_index` is the BitTorrent file index, which
/// HighWay reports per file as `index`; the selector works on list positions.
/// Unfinished files (`partial`, named `*.part`) take part in the selection under
/// their final name, so a request for an episode that is still loading answers
/// "downloading" instead of falling back to another episode.
fn select_file(
    files: &[Value],
    release_name: &str,
    filename: Option<&str>,
    file_index: Option<i32>,
    season: Option<i32>,
    episode: Option<i32>,
) -> Result<usize, ProviderError> {
    let pairs: Vec<(String, i64)> = files
        .iter()
        .map(|f| {
            let path = f.get("path").and_then(|v| v.as_str()).unwrap_or_default();
            let path = if is_partial(f) {
                path.strip_suffix(".part").unwrap_or(path)
            } else {
                path
            };
            (
                path.to_string(),
                f.get("size").and_then(|v| v.as_i64()).unwrap_or(0),
            )
        })
        .collect();
    // Without a video file the selector falls back to position zero (e.g. an .nfo).
    if !pairs.iter().any(|(path, _)| {
        let name = path.rsplit('/').next().unwrap_or(path);
        is_video_file(name)
    }) {
        return Err(ProviderError::api(
            "No video file in HighWay transfer",
            "no_video_file_found.mp4",
        ));
    }
    let position = file_index.and_then(|wanted| {
        files
            .iter()
            .position(|f| f.get("index").and_then(|v| v.as_i64()) == Some(i64::from(wanted)))
            .and_then(|p| i32::try_from(p).ok())
    });
    let chosen = select_debrid_file_index(
        &pairs,
        release_name,
        filename,
        position,
        season,
        episode,
        None,
    );
    match files.get(chosen) {
        Some(file) if is_partial(file) => Err(ProviderError::api(
            "HighWay is still downloading this file. Play again in a few minutes.",
            "torrent_downloading.mp4",
        )),
        Some(_) => Ok(chosen),
        None => Err(ProviderError::api(
            "No files in HighWay transfer",
            "no_video_file_found.mp4",
        )),
    }
}

fn is_partial(file: &Value) -> bool {
    file.get("partial").and_then(|v| v.as_bool()) == Some(true)
}

/// Resolve a direct video URL from HighWay for the given torrent.
#[allow(clippy::too_many_arguments)]
pub async fn get_video_url(
    http: &reqwest::Client,
    token: &str,
    info_hash: &str,
    announce_list: &[String],
    filename: Option<&str>,
    file_index: Option<i32>,
    season: Option<i32>,
    episode: Option<i32>,
    _user_ip: Option<&str>,
    _forward: Option<&MediaFlowForward>,
) -> Result<String, ProviderError> {
    get_video_url_at(
        http,
        BASE_URL,
        token,
        info_hash,
        announce_list,
        filename,
        file_index,
        season,
        episode,
    )
    .await
}

#[allow(clippy::too_many_arguments)]
async fn get_video_url_at(
    http: &reqwest::Client,
    base: &str,
    token: &str,
    info_hash: &str,
    announce_list: &[String],
    filename: Option<&str>,
    file_index: Option<i32>,
    season: Option<i32>,
    episode: Option<i32>,
) -> Result<String, ProviderError> {
    let mut magnet = format!("magnet:?xt=urn:btih:{info_hash}");
    for tracker in announce_list {
        magnet.push_str("&tr=");
        magnet.push_str(&urlencoding::encode(tracker));
    }
    let resp = http
        .post(base)
        .bearer_auth(token)
        .form(&[("magnet", magnet.as_str())])
        .send()
        .await?;
    let added = hw_json(resp, "highway add magnet").await?;
    let state = added.get("state").and_then(|v| v.as_str()).unwrap_or("");
    let id = added
        .get("id")
        .and_then(|v| v.as_str())
        .ok_or_else(|| ProviderError::api("Missing id in HighWay add response", "api_error.mp4"))?
        .to_string();

    if state != "completed" {
        if state == "error" {
            return Err(ProviderError::api(
                "HighWay could not load this torrent",
                "transfer_error.mp4",
            ));
        }
        return Err(ProviderError::api(
            format!(
                "HighWay is downloading this title (state: {state}). Play again in a few minutes."
            ),
            "torrent_downloading.mp4",
        ));
    }

    let transfer = hw_get(
        http,
        base,
        token,
        &[("id", id.as_str()), ("service", "torrent")],
    )
    .await?;
    let files: Vec<Value> = transfer
        .get("files")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    let release_name = transfer.get("name").and_then(|v| v.as_str()).unwrap_or("");
    let chosen = select_file(&files, release_name, filename, file_index, season, episode)?;

    files[chosen]
        .get("download_url")
        .and_then(|v| v.as_str())
        .filter(|url| !url.is_empty())
        .map(str::to_string)
        .ok_or_else(|| {
            ProviderError::api(
                "Selected file has no download link on HighWay",
                "api_error.mp4",
            )
        })
}

#[cfg(test)]
mod tests {
    use serde_json::json;
    use wiremock::{
        Mock, MockServer, ResponseTemplate,
        matchers::{body_string_contains, header, method, query_param},
    };

    const HASH: &str = "0123456789abcdef0123456789abcdef01234567";

    fn http() -> reqwest::Client {
        crate::util::http::build_debrid(None, 30)
    }

    fn transfer() -> serde_json::Value {
        json!({
            "id": "11", "service": "torrent", "name": "Show.S01.1080p", "state": "completed",
            "progress": 100, "size": 3000, "hash": HASH,
            "files": [
                {"id": "a1", "index": 0, "path": "Show.S01.1080p/info.nfo", "size": 10, "partial": false,
                 "download_url": "https://dl.example/nfo"},
                {"id": "a2", "index": 1, "path": "Show.S01.1080p/Show.S01E01.1080p.mkv", "size": 1000, "partial": false,
                 "download_url": "https://dl.example/e01"},
                {"id": "a3", "index": 2, "path": "Show.S01.1080p/Show.S01E02.1080p.mkv", "size": 1000, "partial": false,
                 "download_url": "https://dl.example/e02"},
                {"id": "a4", "index": 3, "path": "Show.S01.1080p/Show.S01E03.1080p.mkv.part", "size": 990, "partial": true,
                 "download_url": "https://dl.example/e03"}
            ]
        })
    }

    #[tokio::test]
    async fn cached_torrent_resolves_the_requested_episode() {
        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(header("authorization", "Bearer tok"))
            .and(body_string_contains(HASH))
            .respond_with(
                ResponseTemplate::new(200)
                    .set_body_json(json!({"id": "11", "service": "torrent", "state": "completed"})),
            )
            .expect(2)
            .mount(&server)
            .await;
        Mock::given(method("GET"))
            .and(query_param("id", "11"))
            .and(query_param("service", "torrent"))
            .respond_with(ResponseTemplate::new(200).set_body_json(transfer()))
            .expect(2)
            .mount(&server)
            .await;

        let url = super::get_video_url_at(
            &http(),
            &server.uri(),
            "tok",
            HASH,
            &[],
            None,
            None,
            Some(1),
            Some(2),
        )
        .await
        .unwrap();
        assert_eq!(url, "https://dl.example/e02");

        // BitTorrent index 1 → episode 1, not list position 1
        let url = super::get_video_url_at(
            &http(),
            &server.uri(),
            "tok",
            HASH,
            &[],
            None,
            Some(1),
            None,
            None,
        )
        .await
        .unwrap();
        assert_eq!(url, "https://dl.example/e01");
    }

    async fn completed_server(transfer: serde_json::Value) -> MockServer {
        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .respond_with(
                ResponseTemplate::new(200)
                    .set_body_json(json!({"id": "11", "service": "torrent", "state": "completed"})),
            )
            .mount(&server)
            .await;
        Mock::given(method("GET"))
            .and(query_param("id", "11"))
            .respond_with(ResponseTemplate::new(200).set_body_json(transfer))
            .mount(&server)
            .await;
        server
    }

    fn video_file(err: crate::providers::ProviderError) -> &'static str {
        match err {
            crate::providers::ProviderError::Api { video_file, .. } => video_file,
            other => panic!("unexpected error: {other:?}"),
        }
    }

    #[tokio::test]
    async fn partial_episode_answers_downloading_instead_of_another_episode() {
        let server = completed_server(transfer()).await;

        let by_episode = super::get_video_url_at(
            &http(),
            &server.uri(),
            "tok",
            HASH,
            &[],
            None,
            None,
            Some(1),
            Some(3),
        )
        .await
        .unwrap_err();
        assert_eq!(video_file(by_episode), "torrent_downloading.mp4");

        let by_index = super::get_video_url_at(
            &http(),
            &server.uri(),
            "tok",
            HASH,
            &[],
            None,
            Some(3),
            None,
            None,
        )
        .await
        .unwrap_err();
        assert_eq!(video_file(by_index), "torrent_downloading.mp4");
    }

    #[tokio::test]
    async fn transfer_without_video_is_rejected() {
        let server = completed_server(json!({
            "id": "11", "service": "torrent", "name": "Docs", "state": "completed", "progress": 100,
            "size": 10, "hash": HASH,
            "files": [{"id": "a1", "index": 0, "path": "Docs/info.nfo", "size": 10, "partial": false,
                       "download_url": "https://dl.example/nfo"}]
        }))
        .await;

        let err = super::get_video_url_at(
            &http(),
            &server.uri(),
            "tok",
            HASH,
            &[],
            None,
            None,
            None,
            None,
        )
        .await
        .unwrap_err();
        assert_eq!(video_file(err), "no_video_file_found.mp4");
    }

    #[tokio::test]
    async fn uncached_torrent_answers_with_downloading_video() {
        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .respond_with(ResponseTemplate::new(200).set_body_json(
                json!({"id": "0123456789abcdef0123456789abcdef", "service": "torrent", "state": "queued"}),
            ))
            .mount(&server)
            .await;

        let err = super::get_video_url_at(
            &http(),
            &server.uri(),
            "tok",
            HASH,
            &[],
            None,
            None,
            None,
            None,
        )
        .await
        .unwrap_err();
        match err {
            crate::providers::ProviderError::Api { video_file, .. } => {
                assert_eq!(video_file, "torrent_downloading.mp4")
            }
            other => panic!("unexpected error: {other:?}"),
        }
    }

    #[tokio::test]
    async fn invalid_token_maps_to_credentials_video() {
        let server = MockServer::start().await;
        Mock::given(method("GET"))
            .and(query_param("stats", "1"))
            .respond_with(
                ResponseTemplate::new(401)
                    .set_body_json(json!({"error": "unauthorized", "message": "Invalid token"})),
            )
            .mount(&server)
            .await;

        let err = super::validate_credentials_at(&http(), &server.uri(), "bad")
            .await
            .unwrap_err();
        match err {
            crate::providers::ProviderError::Api { video_file, .. } => {
                assert_eq!(video_file, "invalid_token.mp4")
            }
            other => panic!("unexpected error: {other:?}"),
        }
    }

    #[tokio::test]
    async fn check_cached_returns_only_ready_hashes() {
        let server = MockServer::start().await;
        let other = "fedcba9876543210fedcba9876543210fedcba98";
        Mock::given(method("GET"))
            .and(query_param("service", "torrent"))
            .respond_with(ResponseTemplate::new(200).set_body_json(json!({
                "service": "torrent",
                "results": {
                    HASH: {"known": true, "ready": true, "id": null, "name": "x", "size": 1, "progress": 100},
                    other: {"known": true, "ready": false, "id": null, "name": "y", "size": 1, "progress": 40}
                }
            })))
            .expect(1)
            .mount(&server)
            .await;

        let cached = super::check_cached_at(
            &http(),
            &server.uri(),
            "tok",
            &[HASH.to_uppercase(), other.to_string()],
        )
        .await;
        assert_eq!(cached, vec![HASH.to_string()]);
    }

    #[tokio::test]
    async fn list_keeps_completed_torrents_only() {
        let server = MockServer::start().await;
        Mock::given(method("GET"))
            .and(query_param("service", "torrent"))
            .respond_with(ResponseTemplate::new(200).set_body_json(json!({
                "total": 2,
                "transfers": [
                    {"id": "11", "service": "torrent", "name": "Done", "state": "completed", "progress": 100, "size": 5, "hash": HASH},
                    {"id": "0123456789abcdef0123456789abcdef", "service": "torrent", "name": "Waiting", "state": "queued",
                     "progress": 0, "size": 0, "hash": null}
                ]
            })))
            .mount(&server)
            .await;

        let list = super::list_downloaded_torrents_at(&http(), &server.uri(), "tok")
            .await
            .unwrap();
        assert_eq!(list.len(), 1);
        assert_eq!(list[0].id, "11");
        assert_eq!(list[0].info_hash, HASH);
        assert_eq!(list[0].size, 5);
    }
}
