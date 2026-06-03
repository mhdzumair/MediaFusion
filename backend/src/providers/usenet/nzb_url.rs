/// NZB URL credential injection.
///
/// DB stores sanitized NZB URLs (API keys stripped at scrape time).
/// At playback, re-inject the requesting user's own Newznab API key by
/// matching the stream's source/indexer against their configured indexers.
use std::collections::HashSet;

use crate::models::user_data::UserData;

/// Query keys stripped from persisted and rebuilt NZB URLs (Python `SENSITIVE_QUERY_KEYS`).
pub const SENSITIVE_QUERY_KEYS: &[&str] = &[
    "apikey",
    "api_key",
    "token",
    "auth",
    "authorization",
    "passkey",
    "password",
    "pwd",
    "username",
    "user",
    "rsskey",
    "key",
    "secret",
];

fn normalize(value: Option<&str>) -> String {
    value.unwrap_or("").trim().to_lowercase()
}

fn is_sensitive_query_key(key: &str) -> bool {
    let lowered = key.trim().to_lowercase();
    SENSITIVE_QUERY_KEYS.contains(&lowered.as_str())
}

/// Strip embedded credentials from NZB URLs before persistence or rebuild.
pub fn sanitize_nzb_url(url: &str) -> Option<String> {
    let raw = url.trim();
    if raw.is_empty() {
        return None;
    }

    let mut parsed = url::Url::parse(raw).ok()?;

    if !parsed.username().is_empty() || parsed.password().is_some() {
        parsed.set_username("").ok()?;
        parsed.set_password(None).ok()?;
    }

    let kept: Vec<(String, String)> = parsed
        .query_pairs()
        .filter(|(k, _)| !is_sensitive_query_key(k))
        .map(|(k, v)| (k.into_owned(), v.into_owned()))
        .collect();

    parsed.query_pairs_mut().clear();
    for (k, v) in kept {
        parsed.query_pairs_mut().append_pair(&k, &v);
    }

    Some(parsed.to_string())
}

/// Return the NZB URL with the user's matching Newznab `apikey=` injected.
/// Falls back to the sanitized original URL if no indexer matches.
pub fn build_user_scoped_nzb_url(
    nzb_url: &str,
    source: Option<&str>,
    indexer: Option<&str>,
    user_data: &UserData,
) -> String {
    let Some(safe_url) = sanitize_nzb_url(nzb_url) else {
        return String::new();
    };

    let Some(idx_cfg) = &user_data.indexer_config else {
        return safe_url;
    };

    let enabled: Vec<_> = idx_cfg
        .newznab_indexers
        .iter()
        .filter(|idx| idx.enabled)
        .collect();
    if enabled.is_empty() {
        return safe_url;
    }

    let source_candidates: HashSet<String> = [normalize(source), normalize(indexer)]
        .into_iter()
        .filter(|s| !s.is_empty())
        .collect();

    let matched = enabled.iter().find(|idx| {
        let name = normalize(Some(&idx.name));
        source_candidates.contains(&name)
    });

    let matched = matched.or_else(|| {
        // When source/indexer were provided but no name matched, do not fall back to
        // host matching (avoids "my-prowlarr-indexer" matching indexer "prowlarr").
        if !source_candidates.is_empty() {
            return None;
        }
        let stream_host = host(&safe_url);
        if stream_host.is_empty() {
            return None;
        }
        enabled.iter().find(|idx| host(&idx.url) == stream_host)
    });

    let Some(idx) = matched else {
        return safe_url;
    };

    rebuild_with_indexer(&safe_url, &idx.url, idx.api_key.as_deref()).unwrap_or(safe_url)
}

/// Rebuild URL using indexer netloc and inject apikey, stripping sensitive query keys.
fn rebuild_with_indexer(
    safe_url: &str,
    indexer_url: &str,
    api_key: Option<&str>,
) -> Option<String> {
    let mut stream_parts = url::Url::parse(safe_url).ok()?;
    let indexer_parts = url::Url::parse(indexer_url.trim()).ok()?;

    if let Some(netloc) = indexer_parts.host_str() {
        stream_parts.set_host(Some(netloc)).ok()?;
        if let Some(port) = indexer_parts.port() {
            stream_parts.set_port(Some(port)).ok()?;
        } else {
            stream_parts.set_port(None).ok()?;
        }
    }
    if stream_parts.scheme().is_empty() && !indexer_parts.scheme().is_empty() {
        stream_parts.set_scheme(indexer_parts.scheme()).ok()?;
    }

    let kept: Vec<(String, String)> = stream_parts
        .query_pairs()
        .filter(|(k, _)| !is_sensitive_query_key(k))
        .map(|(k, v)| (k.into_owned(), v.into_owned()))
        .collect();

    stream_parts.query_pairs_mut().clear();
    for (k, v) in kept {
        stream_parts.query_pairs_mut().append_pair(&k, &v);
    }
    if let Some(key) = api_key.filter(|k| !k.is_empty()) {
        stream_parts.query_pairs_mut().append_pair("apikey", key);
    }

    Some(stream_parts.to_string())
}

/// Replace or append `apikey=` in a URL's query string.
pub fn inject_apikey(url: &str, api_key: &str) -> String {
    rebuild_with_indexer(url, url, Some(api_key)).unwrap_or_else(|| {
        let prefix = if let Some(q) = url.find('?') {
            let base = &url[..=q];
            let query = &url[q + 1..];
            let kept: Vec<&str> = query
                .split('&')
                .filter(|p| !p.starts_with("apikey=") && !p.starts_with("api_key="))
                .collect();
            if kept.is_empty() {
                base.to_string()
            } else {
                format!("{}{}&", base, kept.join("&"))
            }
        } else {
            format!("{url}?")
        };
        format!("{prefix}apikey={api_key}")
    })
}

pub fn host(url: &str) -> String {
    host_opt(url).unwrap_or_default()
}

pub fn host_opt(url: &str) -> Option<String> {
    url::Url::parse(url)
        .ok()
        .and_then(|u| u.host_str().map(|h| h.to_lowercase()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::user_data::{IndexerConfig, NewznabIndexer, UserData};

    #[test]
    fn sanitize_strips_apikey() {
        let url = "https://indexer.example/api?t=search&apikey=secret123&cat=2000";
        let out = sanitize_nzb_url(url).unwrap();
        assert!(!out.contains("secret123"));
        assert!(out.contains("t=search"));
    }

    #[test]
    fn exact_name_match_not_substring() {
        let user = UserData {
            indexer_config: Some(IndexerConfig {
                newznab_indexers: vec![NewznabIndexer {
                    id: "1".to_string(),
                    name: "prowlarr".to_string(),
                    url: "https://idx.example".to_string(),
                    api_key: Some("user-key".to_string()),
                    enabled: true,
                    priority: 0,
                    movie_categories: vec![],
                    tv_categories: vec![],
                    use_zyclops: false,
                    zyclops_backbones: vec![],
                }],
                ..Default::default()
            }),
            ..Default::default()
        };
        // "my-prowlarr-indexer" must NOT match "prowlarr" (exact set membership).
        let out = build_user_scoped_nzb_url(
            "https://idx.example/get?id=1&apikey=old",
            Some("my-prowlarr-indexer"),
            None,
            &user,
        );
        assert!(!out.contains("user-key"));
    }

    #[test]
    fn exact_name_match_works() {
        let user = UserData {
            indexer_config: Some(IndexerConfig {
                newznab_indexers: vec![NewznabIndexer {
                    id: "1".to_string(),
                    name: "Prowlarr".to_string(),
                    url: "https://idx.example".to_string(),
                    api_key: Some("user-key".to_string()),
                    enabled: true,
                    priority: 0,
                    movie_categories: vec![],
                    tv_categories: vec![],
                    use_zyclops: false,
                    zyclops_backbones: vec![],
                }],
                ..Default::default()
            }),
            ..Default::default()
        };
        let out = build_user_scoped_nzb_url(
            "https://other.example/get?id=1",
            Some("Prowlarr"),
            None,
            &user,
        );
        assert!(out.contains("apikey=user-key"));
        assert!(out.contains("idx.example"));
    }
}
