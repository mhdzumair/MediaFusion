/// NZB URL credential injection.
///
/// DB stores sanitized NZB URLs (API keys stripped at scrape time).
/// At playback, re-inject the requesting user's own Newznab API key by
/// matching the stream's source/indexer against their configured indexers.
use crate::models::user_data::UserData;

/// Return the NZB URL with the user's matching Newznab `apikey=` injected.
/// Falls back to the original URL if no indexer matches.
pub fn build_user_scoped_nzb_url(
    nzb_url: &str,
    source: Option<&str>,
    indexer: Option<&str>,
    user_data: &UserData,
) -> String {
    if nzb_url.is_empty() {
        return String::new();
    }

    let Some(idx_cfg) = &user_data.indexer_config else {
        return nzb_url.to_string();
    };

    let nzb_host = host(nzb_url);

    let matched = idx_cfg.newznab_indexers.iter().find(|idx| {
        if !idx.enabled {
            return false;
        }
        let cname = idx.name.to_lowercase();
        let src = source.unwrap_or("").to_lowercase();
        let idxr = indexer.unwrap_or("").to_lowercase();

        // Bidirectional name-substring match against source/indexer fields
        if (!src.is_empty() && (src.contains(&cname) || cname.contains(&src)))
            || (!idxr.is_empty() && (idxr.contains(&cname) || cname.contains(&idxr)))
        {
            return true;
        }

        // Hostname match
        if let Some(ih) = host_opt(&idx.url) {
            if !nzb_host.is_empty()
                && (nzb_host == ih
                    || nzb_host.ends_with(&format!(".{ih}"))
                    || ih.ends_with(&format!(".{nzb_host}")))
            {
                return true;
            }
        }

        false
    });

    let Some(idx) = matched else {
        return nzb_url.to_string();
    };
    let Some(api_key) = idx.api_key.as_deref().filter(|k| !k.is_empty()) else {
        return nzb_url.to_string();
    };

    inject_apikey(nzb_url, api_key)
}

/// Replace or append `apikey=` in a URL's query string.
pub fn inject_apikey(url: &str, api_key: &str) -> String {
    let prefix = if let Some(q) = url.find('?') {
        let base = &url[..=q]; // includes '?'
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
}

pub fn host(url: &str) -> String {
    host_opt(url).unwrap_or_default()
}

pub fn host_opt(url: &str) -> Option<String> {
    let rest = url
        .strip_prefix("https://")
        .or_else(|| url.strip_prefix("http://"))?;
    // Strip optional userinfo (user:pass@)
    let rest = rest.find('@').map(|i| &rest[i + 1..]).unwrap_or(rest);
    let host = rest.split('/').next()?.split(':').next()?; // strip port
    Some(host.to_lowercase())
}
