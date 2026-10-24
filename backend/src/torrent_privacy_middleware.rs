//! Last-mile privacy protection, including cached responses and secondary APIs.
use crate::{
    crypto::torrent_ref::{self, TorrentRef},
    state::AppState,
};
use axum::{
    body::{Body, to_bytes},
    extract::{Request, State},
    http::{StatusCode, header},
    middleware::Next,
    response::{IntoResponse, Response},
};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    sync::{Arc, LazyLock},
};

const MAX_JSON: usize = 32 * 1024 * 1024;
static HASH: LazyLock<regex::Regex> =
    LazyLock::new(|| regex::Regex::new(r"(?i)[a-f0-9]{40}|[a-z2-7]{32}").unwrap());

fn hash_ranges(text: &str) -> impl Iterator<Item = std::ops::Range<usize>> + '_ {
    HASH.find_iter(text)
        .filter(|m| {
            !text[..m.start()]
                .bytes()
                .next_back()
                .is_some_and(|c| c.is_ascii_alphanumeric())
                && !text[m.end()..]
                    .bytes()
                    .next()
                    .is_some_and(|c| c.is_ascii_alphanumeric())
        })
        .map(|m| m.range())
}

pub fn contains_hash(text: &str) -> bool {
    hash_ranges(text).next().is_some()
}

fn redact(text: &str) -> String {
    let mut result = String::new();
    let mut end = 0;
    for range in hash_ranges(text) {
        result.push_str(&text[end..range.start]);
        result.push_str("[redacted]");
        end = range.end;
    }
    result.push_str(&text[end..]);
    result
}

/// Rewrite playback URLs before scrubbing filenames. The original selection stays
/// encrypted in the reference, including hashes embedded in physical filenames.
fn private_url(text: &str, key: &[u8; 32], depth: usize) -> Option<String> {
    if depth > 5 {
        return None;
    }
    let mut url = url::Url::parse(text).ok()?;
    let mut changed = false;
    let segments: Vec<String> = url
        .path_segments()?
        .map(|s| urlencoding::decode(s).ok().map(|s| s.into_owned()))
        .collect::<Option<_>>()?;
    if let Some(start) = segments
        .windows(4)
        .position(|s| s[0] == "streaming_provider" && s[2] == "playback")
    {
        let secret = segments.get(start + 1)?;
        let provider = segments.get(start + 3)?;
        let hash = segments.get(start + 4)?;
        if !hash.starts_with("T-") {
            let tail = &segments[start + 5..];
            let (season, episode, filename) = if tail.len() >= 2
                && tail[0].parse::<i32>().is_ok()
                && tail[1].parse::<i32>().is_ok()
            {
                (
                    tail[0].parse().ok(),
                    tail[1].parse().ok(),
                    tail.get(2).cloned(),
                )
            } else {
                (None, None, tail.first().cloned())
            };
            changed = true;
            let token = torrent_ref::seal(
                &TorrentRef {
                    hash: hash.clone(),
                    season,
                    episode,
                    filename,
                    scope: torrent_ref::scope(secret, provider),
                },
                key,
            )?;
            let mut path = url.path_segments_mut().ok()?;
            path.clear()
                .extend(segments[..start + 4].iter().map(String::as_str))
                .push(&token);
        }
    }
    let pairs: Vec<(String, String)> = url
        .query_pairs()
        .map(|(k, v)| {
            let value = private_url(&v, key, depth + 1).unwrap_or_else(|| v.to_string());
            changed |= value != v;
            (k.into_owned(), value)
        })
        .collect();
    if !pairs.is_empty() {
        url.query_pairs_mut().clear().extend_pairs(pairs);
    }
    Some(if changed {
        url.to_string()
    } else {
        text.into()
    })
}

fn hash_key(key: &str) -> bool {
    let compact = key.replace(['_', '-'], "").to_ascii_lowercase();
    compact.contains("torrentfile")
        || compact.contains("infohash")
        || matches!(
            compact.as_str(),
            "magnet" | "magnetlink" | "magneturl" | "torrentfile"
        )
}

pub fn sanitize(value: &mut Value, key: &[u8; 32], api_scope: &str) {
    sanitize_with_policy(value, key, api_scope, true);
}

fn sanitize_with_policy(value: &mut Value, key: &[u8; 32], api_scope: &str, playable_list: bool) {
    fn collect(value: &Value, hashes: &mut Vec<String>) {
        match value {
            Value::Object(object) => {
                for (name, value) in object {
                    if hash_key(name)
                        && let Some(hash) = value.as_str()
                        && ((hash.len() == 40 && hash.bytes().all(|c| c.is_ascii_hexdigit()))
                            || (hash.len() == 32
                                && hash.bytes().all(|c| {
                                    c.is_ascii_alphabetic() || (b'2'..=b'7').contains(&c)
                                })))
                    {
                        hashes.push(regex::escape(hash));
                    }
                    collect(value, hashes);
                }
            }
            Value::Array(items) => {
                for item in items {
                    collect(item, hashes);
                }
            }
            Value::String(text) => {
                // Formatted Stremio rows have no info_hash field; their raw
                // playback path still identifies hashes used in custom text.
                for tail in text.split("/playback/").skip(1) {
                    if let Some(hash) = tail.split('/').nth(1)
                        && hash.len() == 40
                        && hash.bytes().all(|c| c.is_ascii_hexdigit())
                    {
                        hashes.push(regex::escape(hash));
                    }
                }
            }
            _ => {}
        }
    }
    let mut hashes = vec![];
    collect(value, &mut hashes);
    hashes.sort();
    hashes.dedup();
    let known = if hashes.is_empty() {
        None
    } else {
        regex::Regex::new(&format!("(?i){}", hashes.join("|"))).ok()
    };
    sanitize_inner(value, key, api_scope, known.as_ref(), playable_list);
}

fn sanitize_inner(
    value: &mut Value,
    key: &[u8; 32],
    api_scope: &str,
    known: Option<&regex::Regex>,
    playable_list: bool,
) {
    match value {
        Value::Object(object) => {
            // Preserve watchlist/import operations using confidential references.
            if let Some(hash) = object.get("info_hash").and_then(Value::as_str)
                && let Some(reference) = torrent_ref::seal(
                    &TorrentRef {
                        hash: hash.into(),
                        filename: None,
                        season: None,
                        episode: None,
                        scope: api_scope.into(),
                    },
                    key,
                )
            {
                object.insert("torrent_ref".into(), json!(reference));
            }
            if let Some(hashes) = object.get("info_hashes").and_then(Value::as_array) {
                let refs: Vec<Value> = hashes
                    .iter()
                    .filter_map(|hash| {
                        torrent_ref::seal(
                            &TorrentRef {
                                hash: hash.as_str()?.into(),
                                filename: None,
                                season: None,
                                episode: None,
                                scope: api_scope.into(),
                            },
                            key,
                        )
                        .map(Value::String)
                    })
                    .collect();
                object.insert("torrent_refs".into(), Value::Array(refs));
            }
            object.retain(|name, _| !hash_key(name));
            // Only playable lists lose P2P rows; metadata/management lists remain usable.
            if playable_list && let Some(Value::Array(streams)) = object.get_mut("streams") {
                streams.retain(|item| {
                    let torrent = item.get("infoHash").is_some()
                        || (item.get("stream_type").and_then(Value::as_str) == Some("torrent")
                            && item.get("url").is_some());
                    !torrent
                        || item
                            .get("url")
                            .and_then(Value::as_str)
                            .is_some_and(|url| !url.is_empty() && !url.starts_with("magnet:"))
                });
            }

            // Stremio treats title as an alias of description, not a separate field.
            if object.contains_key("name")
                && object.contains_key("description")
                && (object.contains_key("url") || object.contains_key("behaviorHints"))
            {
                object.remove("title");
            }
            for (name, value) in object.iter_mut() {
                if name != "torrent_ref" && name != "torrent_refs" {
                    sanitize_inner(value, key, api_scope, known, playable_list);
                }
            }
            let old = std::mem::take(object);
            object.extend(old.into_iter().map(|(name, value)| (redact(&name), value)));
        }
        Value::Array(items) => {
            for item in items {
                sanitize_inner(item, key, api_scope, known, playable_list);
            }
        }
        Value::String(text) => {
            if text.starts_with("magnet:") {
                text.clear();
            } else {
                let private = private_url(text, key, 0).unwrap_or_else(|| text.clone());
                let private = known
                    .map(|known| known.replace_all(&private, "[redacted]").into_owned())
                    .unwrap_or(private);
                *text = redact(&private);
            }
        }
        _ => {}
    }
}

fn restore_refs(value: &mut Value, key: &[u8; 32], scope: &str) {
    match value {
        Value::String(text) => {
            if let Some(reference) = torrent_ref::open(text, key).filter(|r| r.scope == scope) {
                *text = reference.hash;
            }
        }
        Value::Array(items) => {
            for item in items {
                restore_refs(item, key, scope);
            }
        }
        Value::Object(object) => {
            let mut restored = BTreeMap::new();
            for (name, mut value) in std::mem::take(object) {
                restore_refs(&mut value, key, scope);
                let name = torrent_ref::open(&name, key)
                    .filter(|r| r.scope == scope)
                    .map(|r| r.hash)
                    .unwrap_or(name);
                restored.insert(name, value);
            }
            object.extend(restored);
        }
        _ => {}
    }
}

pub async fn torrent_privacy_middleware(
    State(state): State<Arc<AppState>>,
    mut request: Request,
    next: Next,
) -> Response {
    if !state.config.hide_torrent_info_hashes {
        return next.run(request).await;
    }
    let identity = crate::routes::auth_guard::decode_access_token(
        request.headers(),
        &state.config.secret_key_raw,
    )
    .ok();
    let admin = if let Some((id, ref role)) = identity {
        role == "admin"
            && crate::db::get_user_role(&state.pool, id).await == Some(crate::db::UserRole::Admin)
    } else {
        false
    };
    if admin {
        let mut response = next.run(request).await;
        response
            .headers_mut()
            .insert(header::CACHE_CONTROL, "private, no-store".parse().unwrap());
        return response;
    }
    let scope = format!("api:{}", identity.map(|(id, _)| id).unwrap_or(0));
    let path = request.uri().path();
    let playable_list = path.starts_with("/api/v1/stream/")
        || (path.starts_with("/api/v1/catalog/") && path.ends_with("/streams"))
        || (!path.starts_with("/api/") && path.contains("/stream/"));
    // Torznab XML contains magnets and .torrent enclosures and has no private
    // playback equivalent. Keep it admin-only while hash privacy is enabled.
    if request.uri().path() == "/torznab" || request.uri().path().starts_with("/torznab/") {
        return StatusCode::FORBIDDEN.into_response();
    }
    if request.uri().path().starts_with("/api/v1/watchlist/")
        && request
            .headers()
            .get(header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok())
            .is_some_and(|v| v.starts_with("application/json"))
    {
        let (parts, body) = request.into_parts();
        let Ok(bytes) = to_bytes(body, MAX_JSON).await else {
            return StatusCode::PAYLOAD_TOO_LARGE.into_response();
        };
        let Ok(mut value) = serde_json::from_slice::<Value>(&bytes) else {
            return StatusCode::BAD_REQUEST.into_response();
        };
        restore_refs(&mut value, &state.config.secret_key, &scope);
        request = Request::from_parts(parts, Body::from(value.to_string()));
        request.headers_mut().remove(header::CONTENT_LENGTH);
    }
    let response = next.run(request).await;
    if response
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .is_some_and(|v| v.starts_with("application/x-bittorrent"))
        || response
            .headers()
            .get(header::LOCATION)
            .and_then(|v| v.to_str().ok())
            .is_some_and(|v| contains_hash(v) || v.starts_with("magnet:"))
    {
        return StatusCode::FORBIDDEN.into_response();
    }
    let is_json = response
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .is_some_and(|v| v.starts_with("application/json"));
    if !is_json {
        return response;
    }
    let (mut parts, body) = response.into_parts();
    let Ok(bytes) = to_bytes(body, MAX_JSON).await else {
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    };
    let Ok(mut value) = serde_json::from_slice::<Value>(&bytes) else {
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    };
    sanitize_with_policy(&mut value, &state.config.secret_key, &scope, playable_list);
    parts.headers.remove(header::CONTENT_LENGTH);
    parts.headers.remove(header::ETAG);
    parts
        .headers
        .insert(header::CACHE_CONTROL, "private, no-store".parse().unwrap());
    Response::from_parts(parts, Body::from(value.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn scrubs_secondary_fields_and_wraps_nested_playback_without_losing_selection() {
        let hash = "1f1914a9d7a2163f08c540bae788806bfad2c732";
        let raw = format!(
            "https://example.test/streaming_provider/U-profile/playback/realdebrid/{hash}/1/2/{hash}.mkv"
        );
        let mut value = json!({"info_hash":hash,"magnet_link":format!("magnet:?xt=urn:btih:{hash}"), "torrent_file":"base64", "streams":[{"infoHash":hash,"name":"p2p"}, {"name":"cloud","url":raw,"title":"duplicate","description":format!("prefix{hash}suffix"),"behaviorHints":{"bingeGroup":hash,"filename":format!("{hash}.mkv")}}], "items":[{"id":42,"stream_type":"torrent","info_hash":hash}],"nested":format!("https://mf.test/proxy/stream?d={}",urlencoding::encode(&raw))});
        sanitize(&mut value, &[9; 32], "api:1");
        assert!(!value.to_string().contains(hash));
        assert!(value.get("info_hash").is_none());
        assert_eq!(value["items"][0]["id"], 42);
        assert!(value.get("torrent_file").is_none());
        assert_eq!(value["streams"].as_array().unwrap().len(), 1);
        assert!(value["streams"][0].get("title").is_none());
        let url = url::Url::parse(value["streams"][0]["url"].as_str().unwrap()).unwrap();
        let reference =
            torrent_ref::open(url.path_segments().unwrap().next_back().unwrap(), &[9; 32]).unwrap();
        assert_eq!(reference.hash, hash);
        assert_eq!(reference.filename.unwrap(), format!("{hash}.mkv"));
        assert_eq!(reference.season, Some(1));
        assert_eq!(
            reference.scope,
            torrent_ref::scope("U-profile", "realdebrid")
        );
        let mut operation = json!({"info_hash":value["torrent_ref"],"overrides":{value["torrent_ref"].as_str().unwrap(): {"title":"movie"}}});
        restore_refs(&mut operation, &[9; 32], "api:1");
        assert_eq!(operation["info_hash"], hash);
        assert!(operation["overrides"].get(hash).is_some());
    }
    #[test]
    fn metadata_lists_and_watchlist_operations_survive_privacy() {
        let hash = "1f1914a9d7a2163f08c540bae788806bfad2c732";
        let mut metadata = json!({"streams":[{"id":42,"stream_type":"torrent","url":null,"info_hash":hash}],"info_hashes":[hash]});
        sanitize_with_policy(&mut metadata, &[9; 32], "api:1", false);
        assert_eq!(metadata["streams"][0]["id"], 42);
        assert!(metadata.get("info_hashes").is_none());
        let operation = metadata["torrent_refs"][0].as_str().unwrap();
        assert_eq!(torrent_ref::open(operation, &[9; 32]).unwrap().hash, hash);
        assert!(!metadata.to_string().contains(hash));
        let mut stremio = json!({"streams":[{"name":"RD","description":format!("prefix{hash}suffix"),"url":format!("https://example.test/streaming_provider/U-test/playback/realdebrid/{hash}")}]});
        sanitize(&mut stremio, &[9; 32], "api:0");
        assert!(!stremio.to_string().contains(hash));
    }

    #[test]
    fn recognizes_base32_and_hashes_next_to_filename_separators() {
        assert_eq!(
            redact("file_ABCDEFGHIJKLMNOPQRSTUVWXYZ234567.mkv"),
            "file_[redacted].mkv"
        );
        assert_eq!(
            redact(&"a".repeat(64)),
            "a".repeat(64),
            "do not corrupt authentication signatures"
        );
    }
}
