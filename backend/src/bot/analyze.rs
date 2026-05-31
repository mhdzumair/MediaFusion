//! Per-content-type analysis (reuses import route internals).

use serde_json::{json, Value};

use crate::{
    parser,
    routes::content::{acestream_import, http_import, nzb_import, torrent_import, youtube_import},
    state::AppState,
};

use super::{api::BotApi, model::ContentType};

pub async fn run_analysis(
    state: &AppState,
    api: &BotApi,
    content_type: ContentType,
    raw_input: &Value,
    media_type: &str,
) -> Value {
    match content_type {
        ContentType::Magnet => {
            let magnet = raw_input.as_str().unwrap_or("");
            torrent_import::analyze_magnet_for_bot(state, magnet, media_type).await
        }
        ContentType::TorrentFile => analyze_torrent_file(state, api, raw_input, media_type).await,
        ContentType::TorrentUrl => analyze_torrent_url(state, raw_input, media_type).await,
        ContentType::Video => analyze_video(raw_input, media_type),
        ContentType::Youtube => {
            let url = raw_input.get("url").and_then(|v| v.as_str()).unwrap_or("");
            youtube_import::analyze_youtube_for_bot(state, url, media_type).await
        }
        ContentType::Http => {
            let url = raw_input.as_str().unwrap_or("");
            http_import::analyze_http_for_bot(url)
        }
        ContentType::Nzb => {
            let url = raw_input.as_str().unwrap_or("");
            nzb_import::analyze_nzb_url_for_bot(state, url, media_type).await
        }
        ContentType::Acestream => {
            let id = raw_input.as_str().unwrap_or("");
            acestream_import::analyze_acestream_for_bot(state, id, media_type).await
        }
    }
}

async fn analyze_torrent_file(
    state: &AppState,
    api: &BotApi,
    raw_input: &Value,
    media_type: &str,
) -> Value {
    let file_id = match raw_input.get("file_id").and_then(|v| v.as_str()) {
        Some(id) => id,
        None => return json!({"success": false, "error": "No file ID provided."}),
    };

    let file_info = match api.get_file(file_id).await {
        Ok(v) => v,
        Err(e) => {
            return json!({"success": false, "error": format!("Failed to get file info: {e}")});
        }
    };
    let file_path = match file_info.get("file_path").and_then(|v| v.as_str()) {
        Some(p) => p,
        None => return json!({"success": false, "error": "No file path in response."}),
    };

    let bytes = match api.download_file(file_path).await {
        Ok(b) => b,
        Err(e) => {
            return json!({"success": false, "error": format!("Failed to download torrent: {e}")});
        }
    };

    match torrent_import::analyze_torrent_bytes(state, &bytes, media_type).await {
        Ok(mut result) => {
            if let Some(obj) = result.as_object_mut() {
                obj.insert(
                    "torrent_content_b64".to_string(),
                    json!(base64::Engine::encode(
                        &base64::engine::general_purpose::STANDARD,
                        &bytes
                    )),
                );
            }
            result
        }
        Err(e) => json!({"success": false, "error": e}),
    }
}

async fn analyze_torrent_url(state: &AppState, raw_input: &Value, media_type: &str) -> Value {
    let url = raw_input.as_str().unwrap_or("");
    let bytes = match state.http.get(url).send().await {
        Ok(resp) if resp.status().is_success() => match resp.bytes().await {
            Ok(b) => b.to_vec(),
            Err(e) => {
                return json!({"success": false, "error": format!("Download failed: {e}")});
            }
        },
        Ok(resp) => {
            return json!({
                "success": false,
                "error": format!("Failed to fetch torrent file (HTTP {}).", resp.status())
            });
        }
        Err(e) => {
            return json!({"success": false, "error": format!("Fetch failed: {e}")});
        }
    };

    if bytes.first() != Some(&b'd') {
        return json!({"success": false, "error": "URL did not return valid torrent file content."});
    }

    match torrent_import::analyze_torrent_bytes(state, &bytes, media_type).await {
        Ok(result) => result,
        Err(e) => json!({"success": false, "error": e}),
    }
}

fn analyze_video(raw_input: &Value, media_type: &str) -> Value {
    let file_name = raw_input
        .get("file_name")
        .and_then(|v| v.as_str())
        .unwrap_or("video");
    let parsed = if parser::is_sports_title(file_name) {
        parser::parse_sports_title(file_name)
    } else {
        parser::parse_title(file_name)
    };
    json!({
        "success": true,
        "file_name": file_name,
        "parsed_title": parsed.title,
        "year": parsed.year,
        "resolution": parsed.resolution,
        "quality": parsed.quality,
        "codec": parsed.codec,
        "media_type": media_type,
        "matches": [],
    })
}
