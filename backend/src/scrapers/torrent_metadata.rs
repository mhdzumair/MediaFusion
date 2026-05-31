//! Shared `.torrent` download, parse, and privacy-type helpers for scrapers.

use std::time::Duration;

use lava_torrent::torrent::v1::Torrent;
use reqwest::Client;

use crate::db::TorrentType;

/// Maximum `.torrent` blob size stored in Postgres (2 MiB).
pub const MAX_TORRENT_FILE_BYTES: usize = 2 * 1024 * 1024;

/// Parsed metadata from a `.torrent` file.
#[derive(Debug, Clone)]
pub struct ParsedTorrent {
    pub info_hash: String,
    pub name: String,
    pub total_size: i64,
    pub announce_list: Vec<String>,
    pub raw_bytes: Vec<u8>,
}

/// Providers allowed to surface non-public torrent streams in the catalog.
pub const SUPPORTED_PRIVATE_TRACKER_PROVIDERS: &[&str] = &["debridlink", "qbittorrent", "torbox"];

pub fn is_probable_torrent_bytes(data: &[u8]) -> bool {
    data.first() == Some(&b'd')
}

pub fn parse_torrent_type_str(s: &str) -> TorrentType {
    match s.trim().to_lowercase().replace('-', "").as_str() {
        "semiprivate" => TorrentType::SemiPrivate,
        "private" => TorrentType::Private,
        "webseed" => TorrentType::WebSeed,
        _ => TorrentType::Public,
    }
}

/// Map Prowlarr indexer flags / privacy string to `TorrentType`.
pub fn prowlarr_torrent_type(indexer_flags: &[String], indexer_privacy: &str) -> TorrentType {
    for flag in indexer_flags {
        if flag.eq_ignore_ascii_case("semiPrivate") {
            return TorrentType::SemiPrivate;
        }
        if flag.eq_ignore_ascii_case("private") {
            return TorrentType::Private;
        }
    }
    if indexer_flags
        .iter()
        .any(|f| f.eq_ignore_ascii_case("freeleech"))
    {
        return TorrentType::Public;
    }
    if let Some(flag) = indexer_flags.first() {
        return parse_torrent_type_str(flag);
    }
    parse_torrent_type_str(indexer_privacy)
}

/// Map Jackett `TrackerType` field to `TorrentType`.
pub fn jackett_torrent_type(tracker_type: Option<&str>) -> TorrentType {
    parse_torrent_type_str(tracker_type.unwrap_or("public"))
}

pub fn is_private_torrent_type(t: TorrentType) -> bool {
    matches!(t, TorrentType::Private | TorrentType::SemiPrivate)
}

/// Whether raw `.torrent` bytes should be persisted for this type.
pub fn should_persist_torrent_file(t: TorrentType) -> bool {
    is_private_torrent_type(t)
}

pub fn torrent_file_for_storage(
    torrent_type: TorrentType,
    bytes: Option<Vec<u8>>,
) -> Option<Vec<u8>> {
    if !should_persist_torrent_file(torrent_type) {
        return None;
    }
    let bytes = bytes?;
    if bytes.is_empty() || bytes.len() > MAX_TORRENT_FILE_BYTES {
        return None;
    }
    Some(bytes)
}

/// Pick the URL to fetch for torrent metadata (Python `get_download_url` parity).
pub fn resolve_download_url(
    torrent_type: TorrentType,
    guid: Option<&str>,
    magnet_url: Option<&str>,
    download_url: Option<&str>,
) -> Option<String> {
    let download = download_url.filter(|u| !u.is_empty());
    let magnet = magnet_url.filter(|u| !u.is_empty());
    let guid = guid.filter(|u| !u.is_empty());

    if is_private_torrent_type(torrent_type) {
        if let Some(d) = download {
            return Some(d.to_string());
        }
    }

    if let Some(g) = guid.filter(|g| g.starts_with("magnet:")) {
        return Some(g.to_string());
    }

    if let Some(m) = magnet.filter(|m| m.starts_with("magnet:")) {
        return Some(m.to_string());
    }

    if let Some(d) = download {
        if d.starts_with("magnet:") {
            return Some(d.to_string());
        }
    }

    magnet
        .or(guid)
        .map(str::to_string)
        .or_else(|| download.map(str::to_string))
}

pub fn announce_list_from_magnet(magnet: &str) -> Vec<String> {
    magnet
        .split('&')
        .filter_map(|part| {
            let part = part.trim_start_matches('?');
            part.strip_prefix("tr=")
                .map(|v| urlencoding::decode(v).unwrap_or_default().into_owned())
        })
        .filter(|u| !u.is_empty())
        .collect()
}

pub async fn download_torrent_bytes(
    http: &Client,
    url: &str,
    timeout: Duration,
) -> Option<Vec<u8>> {
    if url.starts_with("magnet:") {
        return None;
    }
    let bytes = http
        .get(url)
        .timeout(timeout)
        .send()
        .await
        .ok()?
        .bytes()
        .await
        .ok()?
        .to_vec();
    if is_probable_torrent_bytes(&bytes) {
        Some(bytes)
    } else {
        None
    }
}

pub fn parse_torrent_bytes(bytes: &[u8]) -> Option<ParsedTorrent> {
    if !is_probable_torrent_bytes(bytes) {
        return None;
    }
    let torrent = Torrent::read_from_bytes(bytes).ok()?;
    let info_hash = torrent.info_hash();
    if info_hash.len() != 40 || !info_hash.chars().all(|c| c.is_ascii_hexdigit()) {
        return None;
    }

    let mut announce_list = Vec::new();
    if let Some(announce) = &torrent.announce {
        if !announce.is_empty() {
            announce_list.push(announce.clone());
        }
    }
    if let Some(list) = &torrent.announce_list {
        for tier in list {
            for url in tier {
                if !url.is_empty() && !announce_list.contains(url) {
                    announce_list.push(url.clone());
                }
            }
        }
    }

    Some(ParsedTorrent {
        info_hash: info_hash.to_lowercase(),
        name: torrent.name.clone(),
        total_size: torrent.length,
        announce_list,
        raw_bytes: bytes.to_vec(),
    })
}

pub fn provider_supports_private_trackers(service: &str) -> bool {
    SUPPORTED_PRIVATE_TRACKER_PROVIDERS.contains(&service)
}

/// Whether a torrent row should be shown for the given provider (catalog filter parity).
pub fn private_torrent_visible_for_provider(
    torrent_type: TorrentType,
    provider_service: &str,
    has_torrent_providers: bool,
) -> bool {
    if !is_private_torrent_type(torrent_type) {
        return true;
    }
    if !has_torrent_providers {
        return false;
    }
    provider_supports_private_trackers(provider_service)
}

pub fn torrent_type_from_json_value(t: &serde_json::Value) -> TorrentType {
    t.get("torrent_type")
        .and_then(|v| v.as_str())
        .map(parse_torrent_type_str)
        .unwrap_or(TorrentType::Public)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolve_download_url_prefers_download_for_private() {
        let url = resolve_download_url(
            TorrentType::Private,
            Some("magnet:?xt=urn:btih:abc"),
            Some("magnet:?xt=urn:btih:abc"),
            Some("https://indexer/torrent/1"),
        );
        assert_eq!(url.as_deref(), Some("https://indexer/torrent/1"));
    }

    #[test]
    fn resolve_download_url_prefers_magnet_for_public() {
        let url = resolve_download_url(
            TorrentType::Public,
            Some("magnet:?xt=urn:btih:deadbeef"),
            Some("magnet:?xt=urn:btih:deadbeef"),
            Some("https://indexer/torrent/1"),
        );
        assert_eq!(url.as_deref(), Some("magnet:?xt=urn:btih:deadbeef"));
    }

    #[test]
    fn should_persist_file_only_for_private_types() {
        assert!(!should_persist_torrent_file(TorrentType::Public));
        assert!(should_persist_torrent_file(TorrentType::Private));
        assert!(should_persist_torrent_file(TorrentType::SemiPrivate));
    }

    #[test]
    fn torrent_file_for_storage_rejects_oversized() {
        let huge = vec![0u8; MAX_TORRENT_FILE_BYTES + 1];
        assert!(torrent_file_for_storage(TorrentType::Private, Some(huge)).is_none());
        let ok = vec![0u8; 64];
        assert_eq!(
            torrent_file_for_storage(TorrentType::Private, Some(ok.clone())),
            Some(ok)
        );
        assert!(torrent_file_for_storage(TorrentType::Public, Some(vec![1, 2, 3])).is_none());
    }

    #[test]
    fn private_torrent_catalog_filter() {
        assert!(private_torrent_visible_for_provider(
            TorrentType::Public,
            "realdebrid",
            true
        ));
        assert!(!private_torrent_visible_for_provider(
            TorrentType::Private,
            "realdebrid",
            true
        ));
        assert!(private_torrent_visible_for_provider(
            TorrentType::Private,
            "torbox",
            true
        ));
        assert!(!private_torrent_visible_for_provider(
            TorrentType::Private,
            "torbox",
            false
        ));
    }

    #[test]
    fn resolve_download_url_private_falls_back_to_magnet() {
        let url = resolve_download_url(
            TorrentType::Private,
            Some("magnet:?xt=urn:btih:deadbeef"),
            Some("magnet:?xt=urn:btih:deadbeef"),
            None,
        );
        assert_eq!(url.as_deref(), Some("magnet:?xt=urn:btih:deadbeef"));
    }

    #[test]
    fn prowlarr_torrent_type_prefers_private_over_freeleech() {
        assert_eq!(
            prowlarr_torrent_type(&["freeleech".into(), "private".into()], "public"),
            TorrentType::Private
        );
    }

    #[test]
    fn parse_torrent_type_str_maps_variants() {
        assert_eq!(
            parse_torrent_type_str("semiPrivate"),
            TorrentType::SemiPrivate
        );
        assert_eq!(parse_torrent_type_str("private"), TorrentType::Private);
        assert_eq!(parse_torrent_type_str("webseed"), TorrentType::WebSeed);
        assert_eq!(parse_torrent_type_str("public"), TorrentType::Public);
    }
}
