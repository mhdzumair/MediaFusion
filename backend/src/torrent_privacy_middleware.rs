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
static HEX_HASH: LazyLock<regex::Regex> =
    LazyLock::new(|| regex::Regex::new(r"(?i)[a-f0-9]{40,}").unwrap());
static BASE32_HASH: LazyLock<regex::Regex> =
    LazyLock::new(|| regex::Regex::new(r"(?i)[a-z2-7]{32,}").unwrap());

// The scope covers handler execution, so every template renderer sees the same
// policy before user-controlled modifiers run. It is request-local, not global.
tokio::task_local! {
    static PRIVACY_CONTEXT: PrivacyContext;
}

struct PrivacyContext {
    key: [u8; 32],
    auth_key: String,
}

pub(crate) fn private_template_context(value: &Value) -> Option<Value> {
    PRIVACY_CONTEXT
        .try_with(|context| {
            let mut value = value.clone();
            sanitize_with_policy(&mut value, &context.key, "template", false);
            value
        })
        .ok()
}

// Only handlers minting a final provider destination may opt out of Location
// filtering. A request/header/query parameter cannot create this extension.
#[derive(Clone, Copy)]
pub(crate) struct AllowProviderRedirect;

static AUTHENTICATED_TOKEN: LazyLock<regex::Regex> = LazyLock::new(|| {
    regex::Regex::new(r"T-[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|[A-Za-z0-9_-]+=*\.[a-fA-F0-9]{64}")
        .unwrap()
});

fn hash_ranges(text: &str) -> impl Iterator<Item = std::ops::Range<usize>> + '_ {
    // Scan both alphabets independently and merge overlapping runs. A base32
    // prefix must not consume the start of a hexadecimal hash and leave its
    // suffix visible. Redact entire runs rather than arbitrary 32/40-byte chunks.
    let mut ranges: Vec<_> = HEX_HASH
        .find_iter(text)
        .chain(BASE32_HASH.find_iter(text))
        .map(|m| m.range())
        .collect();
    ranges.sort_unstable_by_key(|r| r.start);
    let mut merged: Vec<std::ops::Range<usize>> = Vec::new();
    for range in ranges {
        if let Some(last) = merged.last_mut()
            && range.start <= last.end
        {
            last.end = last.end.max(range.end);
        } else {
            merged.push(range);
        }
    }
    merged.into_iter()
}

fn decoded(text: &str) -> Option<String> {
    let mut value = text.to_string();
    for _ in 0..16 {
        let next =
            String::from_utf8_lossy(&urlencoding::decode_binary(value.as_bytes())).into_owned();
        if next == value {
            return Some(value);
        }
        value = next;
    }
    // Refuse excessive encoding rather than silently missing an inner hash.
    None
}

pub fn contains_hash(text: &str) -> bool {
    decoded(text).is_none_or(|text| hash_ranges(&text).next().is_some())
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

fn authenticated_token(text: &str, key: &[u8; 32]) -> bool {
    torrent_ref::open(text, key).is_some()
        || PRIVACY_CONTEXT
            .try_with(|context| {
                crate::routes::auth::decode_token(text, &context.auth_key).is_some()
            })
            .unwrap_or(false)
}

fn redact_tokens(text: &str, key: &[u8; 32], known: Option<&regex::Regex>) -> String {
    let scrub = |text: &str| {
        let text = known
            .map(|r| r.replace_all(text, "[redacted]").into_owned())
            .unwrap_or_else(|| text.to_owned());
        redact(&text)
    };
    let mut result = String::new();
    let mut end = 0;
    for token in AUTHENTICATED_TOKEN.find_iter(text) {
        if authenticated_token(token.as_str(), key) {
            result.push_str(&scrub(&text[end..token.start()]));
            result.push_str(token.as_str());
            end = token.end();
        }
    }
    result.push_str(&scrub(&text[end..]));
    result
}

fn redact_private(text: &str, key: &[u8; 32], known: Option<&regex::Regex>) -> String {
    let Some(decoded) = decoded(text) else {
        return "[redacted]".into();
    };
    if decoded.to_ascii_lowercase().contains("magnet:") {
        return "[redacted]".into();
    }
    if decoded != text && redact_tokens(&decoded, key, known) != decoded {
        // The value is encoded, possibly across nested URL components. Refuse
        // the value rather than guessing an encoding that changes its meaning.
        return "[redacted]".into();
    }
    redact_tokens(text, key, known)
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
            for value in object.values_mut() {
                // Verify references rather than trusting their field names.
                sanitize_inner(value, key, api_scope, known, playable_list);
            }
            let old = std::mem::take(object);
            object.extend(
                old.into_iter()
                    .map(|(name, value)| (redact_private(&name, key, known), value)),
            );
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
                *text = redact_private(&private, key, known);
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

pub(crate) fn textual_content_type(content_type: &str) -> bool {
    let content_type = content_type
        .split(';')
        .next()
        .unwrap_or("")
        .trim()
        .to_ascii_lowercase();
    content_type.starts_with("text/")
        || content_type.ends_with("+xml")
        || content_type.ends_with("+json")
        || matches!(
            content_type.as_str(),
            "application/xml"
                | "application/json"
                | "application/javascript"
                | "application/x-nzb"
                | "application/xhtml+xml"
                | "application/json-seq"
                | "application/x-ndjson"
                | "application/ndjson"
                | "application/x-mpegurl"
                | "application/vnd.apple.mpegurl"
                | "application/x-www-form-urlencoded"
        )
}

pub async fn torrent_privacy_middleware(
    State(state): State<Arc<AppState>>,
    request: Request,
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
    let admin = crate::routes::auth_guard::require_active_role(
        &state.pool,
        request.headers(),
        &state.config.secret_key_raw,
        &["admin"],
    )
    .await
    .is_ok();
    if admin {
        let mut response = next.run(request).await;
        response
            .headers_mut()
            .insert(header::CACHE_CONTROL, "private, no-store".parse().unwrap());
        return response;
    }
    let scope = format!("api:{}", identity.map(|(id, _)| id).unwrap_or(0));
    PRIVACY_CONTEXT
        .scope(
            PrivacyContext {
                key: state.config.secret_key,
                auth_key: state.config.secret_key_raw.clone(),
            },
            private_request(state, request, next, scope),
        )
        .await
}

async fn private_request(
    state: Arc<AppState>,
    mut request: Request,
    next: Next,
    scope: String,
) -> Response {
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
        if bytes.is_empty() {
            request = Request::from_parts(parts, Body::empty());
        } else {
            let Ok(mut value) = serde_json::from_slice::<Value>(&bytes) else {
                return StatusCode::BAD_REQUEST.into_response();
            };
            restore_refs(&mut value, &state.config.secret_key, &scope);
            request = Request::from_parts(parts, Body::from(value.to_string()));
            request.headers_mut().remove(header::CONTENT_LENGTH);
        }
    }
    let mut response = next.run(request).await;
    let allow_provider_redirect = response
        .extensions()
        .get::<AllowProviderRedirect>()
        .is_some()
        && !state.config.hide_torrent_info_hashes_relay;
    // Inspect all header values, independent of body type. Drop unsafe metadata
    // (e.g. a download filename or ETag), retaining playable binary bodies. An
    // unsafe redirect/content type cannot be repaired by dropping its header.
    let unsafe_headers: Vec<_> = response
        .headers()
        .iter()
        .filter_map(|(name, value)| {
            if name == header::LOCATION && allow_provider_redirect {
                return None;
            }
            let unsafe_value = value
                .to_str()
                .map(|text| redact_private(text, &state.config.secret_key, None) != text)
                .unwrap_or(true);
            unsafe_value.then(|| name.clone())
        })
        .collect();
    for name in unsafe_headers {
        if name == header::LOCATION || name == header::CONTENT_TYPE {
            return StatusCode::FORBIDDEN.into_response();
        }
        response.headers_mut().remove(name);
    }
    let content_type = response
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .trim()
        .to_ascii_lowercase();
    if content_type.starts_with("application/x-bittorrent")
        || content_type.starts_with("text/event-stream")
    {
        // Never buffer an unbounded SSE stream. Authorized admin streams take
        // the live-role-checked exemption above; non-admin streams fail closed.
        return StatusCode::FORBIDDEN.into_response();
    }
    let is_json = content_type
        .split(';')
        .next()
        .is_some_and(|t| t.trim() == "application/json" || t.trim().ends_with("+json"));
    let is_text = textual_content_type(&content_type);
    if !is_json && !is_text {
        return response;
    }
    let (mut parts, body) = response.into_parts();
    let Ok(bytes) = to_bytes(body, MAX_JSON).await else {
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    };
    if !is_json {
        let Ok(text) = std::str::from_utf8(&bytes) else {
            return StatusCode::FORBIDDEN.into_response();
        };
        if redact_private(text, &state.config.secret_key, None) != text {
            return StatusCode::FORBIDDEN.into_response();
        }
        return Response::from_parts(parts, Body::from(bytes));
    }
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
    fn secondary_filenames_and_encoded_values_cannot_hide_identifiers() {
        let hash = "1f1914a9d7a2163f08c540bae788806bfad2c732";
        let encoded: String = hash.bytes().map(|b| format!("%{b:02X}")).collect();
        let mut value = json!({
            "files": [{"filename":format!("prefix{hash}suffix.mkv")}],
            "torrent_ref": hash,
            "torrent_refs": [hash],
            "nested": format!("https://example.test/?d={}", urlencoding::encode(&format!("https://elsewhere.test/{encoded}"))),
            "magnet":format!("%6dagnet:?xt=urn:btih:{encoded}"),
        });
        assert!(contains_hash(&encoded));
        assert_eq!(
            redact(&format!("{}{hash}abcdef", "ABCDEF".repeat(10))),
            "[redacted]"
        );
        let overlapping = format!("{}{hash}suffix.mkv", "z".repeat(40));
        let scrubbed = redact(&overlapping);
        assert!(
            !scrubbed.contains(&hash[20..]),
            "overlapping encodings must not reveal hash tails"
        );
        sanitize(&mut value, &[9; 32], "api:1");
        assert!(!decoded(&value.to_string()).unwrap().contains(hash));
        assert_eq!(value["files"][0]["filename"], "prefix[redacted]suffix.mkv");
        let safe = "https://example.test/download?token=ordinary%2Fescaped%2Bvalue";
        assert_eq!(redact_private(safe, &[9; 32], None), safe);
        // Merely looking like a reference does not grant a bypass.
        let fake = "T-fake_u5EUB7adS3iwKPh36LHVdhwnLaSHmeyf_.invalid";
        assert_ne!(redact_private(fake, &[9; 32], None), fake);
    }

    #[tokio::test]
    async fn template_modifiers_never_see_hashes_and_scope_does_not_leak() {
        let hash = "1f1914a9d7a2163f08c540bae788806bfad2c732";
        let context = json!({"stream":{"filename":format!("prefix{hash}suffix.mkv"),"name":hash}});
        let template = "{stream.filename|replace('', '-')} {stream.name|upper}";
        let original = crate::template::render(template, &context);
        assert!(
            original
                .replace('-', "")
                .to_ascii_lowercase()
                .contains(hash)
        );
        PRIVACY_CONTEXT
            .scope(
                PrivacyContext {
                    key: [9; 32],
                    auth_key: "test".into(),
                },
                async {
                    let rendered = crate::template::render(template, &context);
                    assert!(
                        !rendered
                            .replace('-', "")
                            .to_ascii_lowercase()
                            .contains(hash)
                    );
                },
            )
            .await;
        assert_eq!(crate::template::render(template, &context), original);
    }

    #[test]
    fn recognizes_base32_and_hashes_next_to_filename_separators() {
        assert_eq!(
            redact("file_ABCDEFGHIJKLMNOPQRSTUVWXYZ234567.mkv"),
            "file_[redacted].mkv"
        );
        assert!(contains_hash(&format!(
            "prefix{}suffix.mkv",
            "a".repeat(40)
        )));
        assert!(!redact(&"a".repeat(64)).contains(&"a".repeat(40)));
    }
}
