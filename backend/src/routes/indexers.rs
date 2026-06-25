/// Indexer test endpoints (Prowlarr / Jackett / Torznab / Newznab).
///
/// No auth required — these are connection-test utilities that call external
/// services directly.  Configuration secrets are read from AppConfig.
///
/// Routes (prefix /api/v1/profile/indexers):
///   GET    /global-status  → get_global_indexer_status
///   POST   /prowlarr/test  → test_prowlarr_connection
///   POST   /jackett/test   → test_jackett_connection
///   POST   /torznab/test   → test_torznab_endpoint
///   POST   /newznab/test   → test_newznab_indexer
use std::collections::HashMap;
use std::sync::Arc;

use axum::{
    Json,
    extract::State,
    response::{IntoResponse, Response},
};
use quick_xml::events::Event;
use quick_xml::reader::Reader;
use serde::Deserialize;

use crate::state::AppState;

// ─── Request structs ──────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct IndexerInstanceInput {
    #[serde(default)]
    pub enabled: bool,
    pub url: Option<String>,
    pub api_key: Option<String>,
    #[serde(default = "default_true")]
    pub use_global: bool,
}

fn default_true() -> bool {
    true
}

#[derive(Deserialize)]
pub struct TorznabEndpointInput {
    pub name: String,
    pub url: String,
    pub headers: Option<HashMap<String, String>>,
    #[serde(default = "default_true")]
    pub enabled: bool,
    #[serde(default)]
    pub categories: Vec<i32>,
    #[serde(default = "default_priority")]
    pub priority: i32,
}

fn default_priority() -> i32 {
    1
}

#[derive(Deserialize)]
pub struct NewznabIndexerInput {
    pub name: String,
    pub url: String,
    pub api_key: Option<String>,
    #[serde(default = "default_true")]
    pub enabled: bool,
    #[serde(default)]
    pub categories: Vec<i32>,
}

// ─── XML helpers ─────────────────────────────────────────────────────────────

/// Collect (attr_name, attr_value) pairs from an XML element's attributes using quick-xml.
fn xml_attrs(bytes: &quick_xml::events::BytesStart<'_>) -> HashMap<String, String> {
    bytes
        .attributes()
        .filter_map(|a| a.ok())
        .filter_map(|a| {
            let key = std::str::from_utf8(a.key.as_ref()).ok()?.to_string();
            let val = a
                .normalized_value(quick_xml::XmlVersion::Implicit1_0)
                .ok()?
                .to_string();
            Some((key, val))
        })
        .collect()
}

/// Parse Torznab/Newznab XML capabilities response.
/// Returns (server_title, category_count, search_available, error_code, error_desc, indexers).
///
/// `indexers` is only populated for Jackett torznab indexer discovery format.
#[allow(clippy::type_complexity)]
fn parse_caps_xml(
    xml: &str,
) -> (
    Option<String>,
    usize,
    bool,
    Option<String>,
    Option<String>,
    Vec<(String, String)>,
) {
    let mut reader = Reader::from_str(xml);
    reader.config_mut().trim_text(true);

    let mut server_title: Option<String> = None;
    let mut cat_count: usize = 0;
    let mut search_available = false;
    let mut error_code: Option<String> = None;
    let mut error_desc: Option<String> = None;
    let mut indexers: Vec<(String, String)> = Vec::new(); // (id, title) pairs

    let mut buf = Vec::new();
    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(ref e)) | Ok(Event::Empty(ref e)) => {
                let tag = std::str::from_utf8(e.name().as_ref())
                    .unwrap_or("")
                    .to_lowercase();
                let attrs = xml_attrs(e);
                match tag.as_str() {
                    "server" => {
                        if let Some(t) = attrs.get("title") {
                            server_title = Some(t.clone());
                        }
                    }
                    "category" => {
                        cat_count += 1;
                    }
                    "error" => {
                        error_code = attrs.get("code").cloned();
                        error_desc = attrs.get("description").cloned();
                    }
                    "indexer" => {
                        // Jackett torznab multi-indexer discovery format
                        if let Some(id) = attrs.get("id") {
                            indexers.push((id.clone(), String::new()));
                        }
                    }
                    "title" => {
                        // Will be captured as text in Text event for the indexer title
                    }
                    s if ["movie-search", "tv-search", "search"].contains(&s)
                        && attrs.get("available").map(|v| v.as_str()) == Some("yes") =>
                    {
                        search_available = true;
                    }
                    _ => {}
                }
            }
            Ok(Event::Text(ref e)) => {
                // Capture title text for the last indexer pushed
                if let Some(last) = indexers.last_mut()
                    && last.1.is_empty()
                    && let Ok(text) = e.decode()
                {
                    let s = text.trim().to_string();
                    if !s.is_empty() {
                        last.1 = s;
                    }
                }
            }
            Ok(Event::Eof) => break,
            Err(_) => break,
            _ => {}
        }
        buf.clear();
    }

    (
        server_title,
        cat_count,
        search_available,
        error_code,
        error_desc,
        indexers,
    )
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

/// GET /api/v1/profile/indexers/global-status
pub async fn get_global_indexer_status(State(state): State<Arc<AppState>>) -> Response {
    Json(serde_json::json!({
        "prowlarr_available": state.config.prowlarr_url.is_some() && state.config.prowlarr_api_key.is_some(),
        "jackett_available": state.config.jackett_url.is_some() && state.config.jackett_api_key.is_some(),
    }))
    .into_response()
}

/// POST /api/v1/profile/indexers/prowlarr/test
pub async fn test_prowlarr_connection(
    State(state): State<Arc<AppState>>,
    Json(config): Json<IndexerInstanceInput>,
) -> Response {
    let (url, api_key) = if config.use_global {
        (
            state.config.prowlarr_url.clone(),
            state.config.prowlarr_api_key.clone(),
        )
    } else {
        (config.url.clone(), config.api_key.clone())
    };

    let (url, api_key) = match (url, api_key) {
        (Some(u), Some(k)) => (u, k),
        _ => {
            return Json(serde_json::json!({
                "success": false,
                "message": "URL and API key are required",
            }))
            .into_response();
        }
    };

    let indexers_url = format!("{url}/api/v1/indexer");
    let status_url = format!("{url}/api/v1/indexerstatus");

    let indexers_resp = state
        .http
        .get(&indexers_url)
        .header("X-Api-Key", &api_key)
        .send()
        .await;

    let indexers: Vec<serde_json::Value> = match indexers_resp {
        Err(e) => {
            return Json(serde_json::json!({
                "success": false,
                "message": format!("Connection failed: {e}"),
            }))
            .into_response();
        }
        Ok(resp) if !resp.status().is_success() => {
            return Json(serde_json::json!({
                "success": false,
                "message": format!("HTTP error: {}", resp.status().as_u16()),
            }))
            .into_response();
        }
        Ok(resp) => resp.json().await.unwrap_or_default(),
    };

    // Fetch indexer status
    let _status_map: HashMap<i64, serde_json::Value> = state
        .http
        .get(&status_url)
        .header("X-Api-Key", &api_key)
        .send()
        .await
        .ok()
        .and_then(|r| {
            if r.status().is_success() {
                // We can't use .await inside map, so we block here with a spawned task or use sync
                // Instead use try_json via a helper
                None // Will handle in async block below
            } else {
                None
            }
        })
        .unwrap_or_default();

    // Re-fetch status async properly
    let status_map: HashMap<i64, serde_json::Value> = match state
        .http
        .get(&status_url)
        .header("X-Api-Key", &api_key)
        .send()
        .await
    {
        Ok(r) if r.status().is_success() => r
            .json::<Vec<serde_json::Value>>()
            .await
            .unwrap_or_default()
            .into_iter()
            .filter_map(|s| {
                let id = s.get("indexerId")?.as_i64()?;
                Some((id, s))
            })
            .collect(),
        _ => HashMap::new(),
    };

    let mut health_list: Vec<serde_json::Value> = Vec::new();
    let mut healthy_count: i64 = 0;

    for indexer in &indexers {
        let id = indexer.get("id").and_then(|v| v.as_i64()).unwrap_or(0);
        let name = indexer
            .get("name")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown")
            .to_string();
        let is_enabled = indexer
            .get("enable")
            .and_then(|v| v.as_bool())
            .unwrap_or(true);
        let priority = indexer
            .get("priority")
            .and_then(|v| v.as_i64())
            .unwrap_or(25);

        let status_info = status_map.get(&id);
        let disabled_till = status_info
            .and_then(|s| s.get("disabledTill"))
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty());
        let most_recent_failure = status_info
            .and_then(|s| s.get("mostRecentFailure"))
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty());

        let (health_status, error_msg): (&str, Option<String>) = if !is_enabled {
            ("disabled", Some("Disabled by user".to_string()))
        } else if let Some(dt) = disabled_till {
            ("unhealthy", Some(format!("Disabled until {dt}")))
        } else if let Some(mrf) = most_recent_failure {
            ("warning", Some(mrf.to_string()))
        } else {
            healthy_count += 1;
            ("healthy", None)
        };

        health_list.push(serde_json::json!({
            "name": name,
            "id": id,
            "enabled": is_enabled,
            "status": health_status,
            "error_message": error_msg,
            "priority": priority,
        }));
    }

    // Sort by priority asc then name
    health_list.sort_by(|a, b| {
        let pa = a.get("priority").and_then(|v| v.as_i64()).unwrap_or(999);
        let pb = b.get("priority").and_then(|v| v.as_i64()).unwrap_or(999);
        let na = a.get("name").and_then(|v| v.as_str()).unwrap_or("");
        let nb = b.get("name").and_then(|v| v.as_str()).unwrap_or("");
        pa.cmp(&pb).then(na.cmp(nb))
    });

    let healthy_names: Vec<&str> = health_list
        .iter()
        .filter(|h| h.get("status").and_then(|v| v.as_str()) == Some("healthy"))
        .filter_map(|h| h.get("name").and_then(|v| v.as_str()))
        .collect();

    Json(serde_json::json!({
        "success": true,
        "message": format!("Connected successfully. {healthy_count}/{} indexers healthy.", indexers.len()),
        "indexer_count": healthy_count,
        "indexer_names": healthy_names,
        "indexers": health_list,
    }))
    .into_response()
}

/// POST /api/v1/profile/indexers/jackett/test
pub async fn test_jackett_connection(
    State(state): State<Arc<AppState>>,
    Json(config): Json<IndexerInstanceInput>,
) -> Response {
    let (url, api_key) = if config.use_global {
        (
            state.config.jackett_url.clone(),
            state.config.jackett_api_key.clone(),
        )
    } else {
        (config.url.clone(), config.api_key.clone())
    };

    let (url, api_key) = match (url, api_key) {
        (Some(u), Some(k)) => (u, k),
        _ => {
            return Json(serde_json::json!({
                "success": false,
                "message": "URL and API key are required",
            }))
            .into_response();
        }
    };

    let base = url.trim_end_matches('/');
    let torznab_url = format!("{base}/api/v2.0/indexers/!status:failing/results/torznab/api");

    match state
        .http
        .get(&torznab_url)
        .query(&[
            ("apikey", api_key.as_str()),
            ("t", "indexers"),
            ("configured", "true"),
        ])
        .send()
        .await
    {
        Err(e) => Json(serde_json::json!({
            "success": false,
            "message": format!("Connection failed: {e}"),
        }))
        .into_response(),
        Ok(r) if !r.status().is_success() => Json(serde_json::json!({
            "success": false,
            "message": format!("HTTP error: {}", r.status().as_u16()),
        }))
        .into_response(),
        Ok(r) => {
            let xml_text = r.text().await.unwrap_or_default();
            let (_, _, _, _, _, xml_indexers) = parse_caps_xml(&xml_text);

            let mut health_list: Vec<serde_json::Value> = xml_indexers
                .iter()
                .map(|(id, title)| {
                    let display = if title.is_empty() { id } else { title };
                    serde_json::json!({
                        "name": display,
                        "id": id,
                        "enabled": true,
                        "status": "healthy",
                        "error_message": null,
                    })
                })
                .collect();

            if !health_list.is_empty() {
                health_list.sort_by(|a, b| {
                    let na = a.get("name").and_then(|v| v.as_str()).unwrap_or("");
                    let nb = b.get("name").and_then(|v| v.as_str()).unwrap_or("");
                    na.cmp(nb)
                });
                let count = health_list.len() as i64;
                let names: Vec<&str> = health_list
                    .iter()
                    .filter_map(|h| h.get("name").and_then(|v| v.as_str()))
                    .collect();
                return Json(serde_json::json!({
                    "success": true,
                    "message": format!("Connected successfully. {count} indexer(s) reachable (non-failing)."),
                    "indexer_count": count,
                    "indexer_names": names,
                    "indexers": health_list,
                }))
                .into_response();
            }

            // Fallback: smoke-test search
            let search_url = format!("{base}/api/v2.0/indexers/all/results");
            match state
                .http
                .get(&search_url)
                .query(&[
                    ("apikey", api_key.as_str()),
                    ("Query", "test"),
                    ("Category[]", "2000"),
                ])
                .send()
                .await
            {
                Err(e) => Json(serde_json::json!({
                    "success": false,
                    "message": format!("Connection failed: {e}"),
                }))
                .into_response(),
                Ok(sr) if !sr.status().is_success() => Json(serde_json::json!({
                    "success": false,
                    "message": format!("HTTP error: {}", sr.status().as_u16()),
                }))
                .into_response(),
                Ok(sr) => {
                    let payload: serde_json::Value = sr.json().await.unwrap_or_default();
                    let results = payload
                        .get("Results")
                        .and_then(|v| v.as_array())
                        .cloned()
                        .unwrap_or_default();
                    let mut by_tracker: HashMap<String, serde_json::Value> = HashMap::new();
                    for item in &results {
                        if let Some(tracker) = item.get("Tracker").and_then(|v| v.as_str()) {
                            let tid = item
                                .get("TrackerId")
                                .and_then(|v| v.as_str())
                                .unwrap_or(tracker);
                            by_tracker.entry(tracker.to_string()).or_insert_with(|| {
                                serde_json::json!({
                                    "name": tracker,
                                    "id": tid,
                                    "enabled": true,
                                    "status": "healthy",
                                    "error_message": null,
                                })
                            });
                        }
                    }
                    let mut fallback: Vec<serde_json::Value> = by_tracker.into_values().collect();
                    fallback.sort_by(|a, b| {
                        let na = a.get("name").and_then(|v| v.as_str()).unwrap_or("");
                        let nb = b.get("name").and_then(|v| v.as_str()).unwrap_or("");
                        na.cmp(nb)
                    });
                    let count = fallback.len() as i64;
                    let names: Vec<&str> = fallback
                        .iter()
                        .filter_map(|h| h.get("name").and_then(|v| v.as_str()))
                        .collect();
                    let hint = if count > 0 {
                        format!("{count} indexer(s) returned results for the probe query.")
                    } else {
                        "API key accepted; no indexers returned results for the probe query."
                            .to_string()
                    };
                    Json(serde_json::json!({
                        "success": true,
                        "message": format!("Connected successfully. {hint}"),
                        "indexer_count": count,
                        "indexer_names": names,
                        "indexers": fallback,
                    }))
                    .into_response()
                }
            }
        }
    }
}

/// POST /api/v1/profile/indexers/torznab/test
pub async fn test_torznab_endpoint(
    State(state): State<Arc<AppState>>,
    Json(endpoint): Json<TorznabEndpointInput>,
) -> Response {
    if endpoint.url.is_empty() {
        return Json(serde_json::json!({
            "success": false,
            "message": "URL is required",
        }))
        .into_response();
    }

    let sep = if endpoint.url.contains('?') { "&" } else { "?" };
    let test_url = format!("{}{}t=caps", endpoint.url, sep);

    let mut req = state.http.get(&test_url);
    if let Some(ref headers) = endpoint.headers {
        for (k, v) in headers {
            req = req.header(k.as_str(), v.as_str());
        }
    }

    match req.send().await {
        Err(e) => Json(serde_json::json!({
            "success": false,
            "message": format!("Connection failed: {e}"),
        }))
        .into_response(),
        Ok(r) if !r.status().is_success() => Json(serde_json::json!({
            "success": false,
            "message": format!("HTTP error: {}", r.status().as_u16()),
        }))
        .into_response(),
        Ok(r) => {
            let xml_text = r.text().await.unwrap_or_default();
            let (server_title, cat_count, _, error_code, error_desc, _) = parse_caps_xml(&xml_text);

            if error_code.is_some() || error_desc.is_some() {
                return Json(serde_json::json!({
                    "success": false,
                    "message": format!("Invalid response (not valid Torznab XML)"),
                }))
                .into_response();
            }

            let title = server_title.unwrap_or_else(|| "Unknown".to_string());
            Json(serde_json::json!({
                "success": true,
                "message": format!("Connected to {title}. {cat_count} categories available."),
                "indexer_count": 1,
                "indexer_names": [title],
            }))
            .into_response()
        }
    }
}

/// POST /api/v1/profile/indexers/newznab/test
pub async fn test_newznab_indexer(
    State(state): State<Arc<AppState>>,
    Json(indexer): Json<NewznabIndexerInput>,
) -> Response {
    if indexer.url.is_empty() {
        return Json(serde_json::json!({
            "success": false,
            "message": "URL is required",
        }))
        .into_response();
    }

    let base = indexer.url.trim_end_matches('/');
    let test_url = if base.ends_with("/api") {
        base.to_string()
    } else {
        format!("{base}/api")
    };

    let mut req = state.http.get(&test_url).query(&[("t", "caps")]);
    if let Some(ref key) = indexer.api_key {
        let trimmed = key.trim();
        if !trimmed.is_empty() {
            req = req.query(&[("apikey", trimmed)]);
        }
    }

    match req.send().await {
        Err(e) => Json(serde_json::json!({
            "success": false,
            "message": format!("Connection failed: {e}"),
        }))
        .into_response(),
        Ok(r) if !r.status().is_success() => {
            let status_code = r.status().as_u16();
            let body_text = r.text().await.unwrap_or_default();
            let (_, _, _, _, error_desc, _) = parse_caps_xml(&body_text);
            let msg = error_desc
                .map(|d| format!("API error: {d}"))
                .unwrap_or_else(|| format!("HTTP error: {status_code}"));
            Json(serde_json::json!({
                "success": false,
                "message": msg,
            }))
            .into_response()
        }
        Ok(r) => {
            let xml_text = r.text().await.unwrap_or_default();
            let (server_title, cat_count, search_available, error_code, error_desc, _) =
                parse_caps_xml(&xml_text);

            if error_code.is_some() || error_desc.is_some() {
                let code = error_code.as_deref().unwrap_or("unknown");
                let desc = error_desc.as_deref().unwrap_or("Unknown error");
                return Json(serde_json::json!({
                    "success": false,
                    "message": format!("API error {code}: {desc}"),
                }))
                .into_response();
            }

            let title = server_title.unwrap_or_else(|| indexer.name.clone());
            let mut parts = vec![format!("Connected to {title}")];
            if cat_count > 0 {
                parts.push(format!("{cat_count} categories available"));
            }
            if search_available {
                parts.push("Search supported".to_string());
            }

            Json(serde_json::json!({
                "success": true,
                "message": format!("{}.", parts.join(". ")),
                "indexer_count": 1,
                "indexer_names": [title],
            }))
            .into_response()
        }
    }
}

// ─── CRUD stubs for mod.rs compatibility ─────────────────────────────────────

fn indexer_not_found() -> Response {
    (
        axum::http::StatusCode::NOT_FOUND,
        Json(serde_json::json!({"detail": "Use user profile settings to configure indexers"})),
    )
        .into_response()
}

pub async fn list_indexers(State(state): State<Arc<AppState>>) -> Response {
    let prowlarr_available =
        state.config.prowlarr_url.is_some() && state.config.prowlarr_api_key.is_some();
    let jackett_available = state.config.jackett_url.is_some();
    let zilean_available = !state.config.zilean_url.is_empty();
    let torrentio_available = !state.config.torrentio_url.is_empty();
    let mediafusion_available = !state.config.mediafusion_url.is_empty();

    Json(serde_json::json!({
        "prowlarr_available": prowlarr_available,
        "jackett_available": jackett_available,
        "zilean_available": zilean_available,
        "torrentio_available": torrentio_available,
        "mediafusion_available": mediafusion_available,
    }))
    .into_response()
}

pub async fn create_indexer(
    State(_state): State<Arc<AppState>>,
    Json(_body): Json<serde_json::Value>,
) -> Response {
    indexer_not_found()
}

pub async fn get_indexer(
    State(_state): State<Arc<AppState>>,
    axum::extract::Path(_id): axum::extract::Path<i64>,
) -> Response {
    indexer_not_found()
}

pub async fn update_indexer(
    State(_state): State<Arc<AppState>>,
    axum::extract::Path(_id): axum::extract::Path<i64>,
    Json(_body): Json<serde_json::Value>,
) -> Response {
    indexer_not_found()
}

pub async fn delete_indexer(
    State(_state): State<Arc<AppState>>,
    axum::extract::Path(_id): axum::extract::Path<i64>,
) -> Response {
    indexer_not_found()
}

pub async fn test_indexer(
    State(_state): State<Arc<AppState>>,
    axum::extract::Path(_id): axum::extract::Path<i64>,
) -> Response {
    indexer_not_found()
}
