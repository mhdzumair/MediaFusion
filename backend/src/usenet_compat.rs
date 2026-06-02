//! Usenet stream ↔ provider compatibility (shared by stream route and preference filter).

use serde_json::Value;

use crate::models::user_data::{StreamingProvider, UserData};

/// Whether a usenet row can be played through the given provider.
pub fn is_usenet_stream_compatible(
    row: &Value,
    provider: &StreamingProvider,
    user_data: &UserData,
    allow_public_usenet: bool,
) -> bool {
    const EXCLUSIVE_SOURCES: &[(&str, &str)] = &[("easynews", "easynews"), ("torbox", "torbox")];

    let svc = provider.service.as_str();
    let nzb_url = row.get("nzb_url").and_then(|v| v.as_str()).unwrap_or("");

    if nzb_url.is_empty() {
        return true;
    }

    let source = row
        .get("source")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_lowercase();
    let indexer = row
        .get("indexer")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_lowercase();
    let nzb_host = extract_hostname(nzb_url).unwrap_or_default().to_lowercase();
    let candidates = [source.as_str(), indexer.as_str()];

    let exclusive_owner: Option<&str> = EXCLUSIVE_SOURCES.iter().find_map(|(owner, marker)| {
        let in_source = candidates
            .iter()
            .any(|c| !c.is_empty() && c.contains(marker));
        let in_host = !nzb_host.is_empty() && nzb_host.contains(marker);
        if in_source || in_host {
            Some(*owner)
        } else {
            None
        }
    });
    if let Some(owner) = exclusive_owner {
        return svc == owner;
    }

    if svc == "easynews" {
        return false;
    }

    usenet_indexer_match(&candidates, &nzb_host, user_data, allow_public_usenet)
}

fn usenet_indexer_match(
    candidates: &[&str],
    nzb_host: &str,
    user_data: &UserData,
    allow_public_usenet: bool,
) -> bool {
    const PUBLIC_USENET_KEYS: &[&str] = &["binsearch", "nzbindex"];

    let enabled: Vec<&crate::models::user_data::NewznabIndexer> = user_data
        .indexer_config
        .as_ref()
        .map(|ic| ic.newznab_indexers.iter().filter(|ix| ix.enabled).collect())
        .unwrap_or_default();

    if enabled.is_empty() {
        if allow_public_usenet {
            return candidates
                .iter()
                .any(|c| PUBLIC_USENET_KEYS.iter().any(|k| c.contains(k)))
                || PUBLIC_USENET_KEYS.iter().any(|k| nzb_host.contains(k));
        }
        return false;
    }

    let name_match = enabled.iter().any(|ix| {
        let n = ix.name.to_lowercase();
        candidates
            .iter()
            .any(|c| !c.is_empty() && (c.contains(n.as_str()) || n.contains(c.trim())))
    });
    if name_match {
        return true;
    }

    if !nzb_host.is_empty() {
        let host_match = enabled.iter().any(|ix| {
            extract_hostname(&ix.url)
                .map(|h| h.to_lowercase())
                .as_deref()
                == Some(nzb_host)
        });
        if host_match {
            return true;
        }
    }

    if allow_public_usenet {
        return candidates
            .iter()
            .any(|c| PUBLIC_USENET_KEYS.iter().any(|k| c.contains(k)))
            || PUBLIC_USENET_KEYS.iter().any(|k| nzb_host.contains(k));
    }

    false
}

pub fn extract_hostname(url: &str) -> Option<String> {
    if url.is_empty() {
        return None;
    }
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
        Some(host.to_string())
    }
}
