//! Usenet stream ↔ provider compatibility (shared by stream route and preference filter).
//!
//! Rules:
//! - **Exclusive sources** (Easynews, TorBox Search) only play on their owning provider.
//! - **Easynews** only accepts Easynews-scraped streams (playback re-searches Easynews by
//!   title; it cannot consume external NZB URLs).
//! - **TorBox** accepts its own search results plus the user's Newznab indexers and
//!   operator-enabled public indexers (TorBox submits NZB URLs).
//! - **Downloader providers** (SABnzbd, NZBGet, NzbDAV, Stremio NNTP) accept user Newznab
//!   indexers and public scrapers only — not Easynews/TorBox-exclusive streams.

use std::collections::HashSet;

use serde_json::Value;

use crate::models::user_data::{StreamingProvider, UserData};

const EXCLUSIVE_SOURCE_MARKERS: &[(&str, &[&str])] =
    &[("easynews", &["easynews"]), ("torbox", &["torbox"])];

const STRICT_INDEXER_BOUND_PROVIDERS: &[&str] = &["sabnzbd", "nzbget", "nzbdav", "stremio_nntp"];

const PUBLIC_USENET_SOURCE_KEYS: &[&str] = &["binsearch", "nzbindex"];
const PUBLIC_USENET_HOSTS: &[&str] = &[
    "binsearch.info",
    "www.binsearch.info",
    "nzbindex.com",
    "www.nzbindex.com",
];

fn normalize(value: Option<&str>) -> String {
    value.unwrap_or("").trim().to_lowercase()
}

fn stream_source_candidates(row: &Value) -> HashSet<String> {
    let source = normalize(row.get("source").and_then(|v| v.as_str()));
    let indexer = normalize(row.get("indexer").and_then(|v| v.as_str()));
    [source, indexer]
        .into_iter()
        .filter(|s| !s.is_empty())
        .collect()
}

fn matches_markers(candidates: &HashSet<String>, host: &str, markers: &[&str]) -> bool {
    candidates
        .iter()
        .any(|c| markers.iter().any(|m| c.contains(m)))
        || (!host.is_empty() && markers.iter().any(|m| host.contains(m)))
}

fn exclusive_source_owner(candidates: &HashSet<String>, host: &str) -> Option<&'static str> {
    EXCLUSIVE_SOURCE_MARKERS
        .iter()
        .find_map(|(owner, markers)| {
            if matches_markers(candidates, host, markers) {
                Some(*owner)
            } else {
                None
            }
        })
}

fn enabled_newznab_signatures(user_data: &UserData) -> (HashSet<String>, HashSet<String>) {
    let mut names = HashSet::new();
    let mut hosts = HashSet::new();

    let Some(indexer_config) = user_data.indexer_config.as_ref() else {
        return (names, hosts);
    };

    for indexer in indexer_config
        .newznab_indexers
        .iter()
        .filter(|ix| ix.enabled)
    {
        let name = normalize(Some(&indexer.name));
        if !name.is_empty() {
            names.insert(name);
        }
        if let Some(host) = extract_hostname(&indexer.url) {
            hosts.insert(host.to_lowercase());
        }
    }

    (names, hosts)
}

fn stream_matches_public_usenet(
    candidates: &HashSet<String>,
    host: &str,
    allow_public_usenet: bool,
) -> bool {
    if !allow_public_usenet {
        return false;
    }

    if candidates
        .iter()
        .any(|c| PUBLIC_USENET_SOURCE_KEYS.iter().any(|k| c.contains(k)))
    {
        return true;
    }

    !host.is_empty()
        && PUBLIC_USENET_HOSTS
            .iter()
            .any(|h| host == *h || host.ends_with(&format!(".{h}")))
}

fn indexer_or_public_match(
    candidates: &HashSet<String>,
    host: &str,
    user_data: &UserData,
    allow_public_usenet: bool,
) -> bool {
    let (allowed_names, allowed_hosts) = enabled_newznab_signatures(user_data);

    if candidates.iter().any(|c| {
        allowed_names
            .iter()
            .any(|n| c.contains(n.as_str()) || n.contains(c.as_str()))
    }) {
        return true;
    }

    if !host.is_empty() && allowed_hosts.contains(host) {
        return true;
    }

    stream_matches_public_usenet(candidates, host, allow_public_usenet)
}

/// Whether a usenet row can be played through the given provider.
pub fn is_usenet_stream_compatible(
    row: &Value,
    provider: &StreamingProvider,
    user_data: &UserData,
    allow_public_usenet: bool,
) -> bool {
    let svc = provider.service.as_str();
    let candidates = stream_source_candidates(row);
    let host = extract_hostname(row.get("nzb_url").and_then(|v| v.as_str()).unwrap_or(""))
        .unwrap_or_default()
        .to_lowercase();

    // Easynews/TorBox Search streams must not leak to other providers (even without nzb_url).
    if let Some(owner) = exclusive_source_owner(&candidates, &host) {
        return svc == owner;
    }

    match svc {
        // Easynews playback searches the Easynews catalog by title — external NZBs cannot play.
        "easynews" => matches_markers(&candidates, &host, &["easynews"]),
        // TorBox can submit arbitrary NZB URLs in addition to its own search results.
        "torbox" => {
            matches_markers(&candidates, &host, &["torbox"])
                || indexer_or_public_match(&candidates, &host, user_data, allow_public_usenet)
        }
        svc if STRICT_INDEXER_BOUND_PROVIDERS.contains(&svc) => {
            let nzb_url = row.get("nzb_url").and_then(|v| v.as_str()).unwrap_or("");
            if nzb_url.is_empty() {
                return true;
            }
            indexer_or_public_match(&candidates, &host, user_data, allow_public_usenet)
        }
        _ => {
            let nzb_url = row.get("nzb_url").and_then(|v| v.as_str()).unwrap_or("");
            nzb_url.is_empty()
                || indexer_or_public_match(&candidates, &host, user_data, allow_public_usenet)
        }
    }
}

pub fn extract_hostname(url: &str) -> Option<String> {
    if url.is_empty() {
        return None;
    }
    url::Url::parse(url)
        .ok()
        .and_then(|u| u.host_str().map(|h| h.to_lowercase()))
        .or_else(|| manual_extract_hostname(url))
}

fn manual_extract_hostname(url: &str) -> Option<String> {
    let after_scheme = if let Some(pos) = url.find("://") {
        &url[pos + 3..]
    } else {
        url
    };
    let after_auth = if let Some(at) = after_scheme.rfind('@') {
        &after_scheme[at + 1..]
    } else {
        after_scheme
    };
    let host_port = after_auth.split(['/', '?', '#']).next()?;
    let host = if let Some(colon) = host_port.rfind(':') {
        if host_port[colon + 1..].chars().all(|c| c.is_ascii_digit()) {
            &host_port[..colon]
        } else {
            host_port
        }
    } else {
        host_port
    };
    if host.is_empty() {
        None
    } else {
        Some(host.to_lowercase())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::user_data::{IndexerConfig, NewznabIndexer, UserData};
    use serde_json::json;

    fn torbox_row(nzb_url: &str) -> Value {
        json!({
            "source": "TorBox Search",
            "indexer": "TorBox Search",
            "nzb_url": nzb_url,
            "name": "Example 1080p",
        })
    }

    fn easynews_row() -> Value {
        json!({
            "source": "Easynews",
            "indexer": "Easynews",
            "nzb_url": "https://members.easynews.com/dl/abc",
            "name": "Example 1080p",
        })
    }

    fn public_row() -> Value {
        json!({
            "source": "NZBIndex",
            "indexer": "NZBIndex",
            "nzb_url": "https://www.nzbindex.com/search/?q=test",
            "name": "Example",
        })
    }

    fn newznab_row(name: &str, url: &str) -> Value {
        json!({
            "source": name,
            "indexer": name,
            "nzb_url": url,
            "name": "Example 1080p",
        })
    }

    fn provider(service: &str) -> StreamingProvider {
        StreamingProvider {
            service: service.to_string(),
            enabled: true,
            ..Default::default()
        }
    }

    fn user_with_newznab(name: &str, url: &str) -> UserData {
        UserData {
            indexer_config: Some(IndexerConfig {
                newznab_indexers: vec![NewznabIndexer {
                    id: "1".into(),
                    name: name.into(),
                    url: url.into(),
                    enabled: true,
                    ..Default::default()
                }],
                ..Default::default()
            }),
            ..Default::default()
        }
    }

    #[test]
    fn easynews_stream_not_compatible_with_torbox() {
        assert!(!is_usenet_stream_compatible(
            &easynews_row(),
            &provider("torbox"),
            &UserData::default(),
            true,
        ));
    }

    #[test]
    fn torbox_stream_not_compatible_with_easynews() {
        assert!(!is_usenet_stream_compatible(
            &torbox_row(""),
            &provider("easynews"),
            &UserData::default(),
            true,
        ));
    }

    #[test]
    fn torbox_stream_compatible_with_torbox_even_without_nzb_url() {
        assert!(is_usenet_stream_compatible(
            &torbox_row(""),
            &provider("torbox"),
            &UserData::default(),
            true,
        ));
    }

    #[test]
    fn public_usenet_compatible_with_torbox() {
        assert!(is_usenet_stream_compatible(
            &public_row(),
            &provider("torbox"),
            &UserData::default(),
            true,
        ));
    }

    #[test]
    fn public_usenet_not_compatible_with_easynews() {
        assert!(!is_usenet_stream_compatible(
            &public_row(),
            &provider("easynews"),
            &UserData::default(),
            true,
        ));
    }

    #[test]
    fn newznab_compatible_with_torbox_when_configured() {
        let row = newznab_row("DrunkenSlug", "https://drunkenslug.com/api?t=get&id=abc");
        assert!(is_usenet_stream_compatible(
            &row,
            &provider("torbox"),
            &user_with_newznab("DrunkenSlug", "https://drunkenslug.com/api"),
            true,
        ));
    }

    #[test]
    fn newznab_not_compatible_with_easynews() {
        let row = newznab_row("DrunkenSlug", "https://drunkenslug.com/api?t=get&id=abc");
        assert!(!is_usenet_stream_compatible(
            &row,
            &provider("easynews"),
            &user_with_newznab("DrunkenSlug", "https://drunkenslug.com/api"),
            true,
        ));
    }

    #[test]
    fn public_usenet_compatible_with_downloader_provider() {
        assert!(is_usenet_stream_compatible(
            &public_row(),
            &provider("sabnzbd"),
            &UserData::default(),
            true,
        ));
    }

    #[test]
    fn torbox_stream_not_compatible_with_sabnzbd() {
        assert!(!is_usenet_stream_compatible(
            &torbox_row("https://api.torbox.app/nzb/1"),
            &provider("sabnzbd"),
            &UserData::default(),
            true,
        ));
    }
}
