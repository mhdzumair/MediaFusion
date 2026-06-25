//! Fetch magnet/download URLs from public indexer detail pages.
//!
//! Mirrors Python `workers/scrapers/torrent_info.py`.

use std::collections::HashMap;
use std::sync::OnceLock;
use std::time::Duration;

use regex::Regex;
use reqwest::Client;
use scraper::{Html, Selector};

use crate::parser;

#[derive(Debug, Default, Clone)]
pub struct TorrentInfoPage {
    pub magnet_url: Option<String>,
    pub download_url: Option<String>,
    pub info_hash: Option<String>,
}

pub async fn get_torrent_info(
    http: &Client,
    url: &str,
    indexer: &str,
    timeout: Duration,
) -> TorrentInfoPage {
    let fetch_url = pre_process_url(indexer, url);
    let html = match http.get(&fetch_url).timeout(timeout).send().await {
        Ok(resp) if resp.status().is_success() => resp.text().await.unwrap_or_default(),
        Ok(resp) => {
            tracing::debug!("torrent_info: HTTP {} for {fetch_url}", resp.status());
            return TorrentInfoPage::default();
        }
        Err(e) => {
            tracing::debug!(
                error_kind = crate::util::http::transport_error_kind(&e),
                "torrent_info: fetch failed for {fetch_url}: {e}"
            );
            return TorrentInfoPage::default();
        }
    };

    parse_detail_page(indexer, &html, &fetch_url)
}

fn pre_process_url(indexer: &str, url: &str) -> String {
    if indexer.eq_ignore_ascii_case("TheRARBG") {
        url.replace("?format=json", "")
    } else {
        url.to_string()
    }
}

fn parser_for(indexer: &str) -> fn(&Html, &str) -> TorrentInfoPage {
    parsers()
        .get(indexer)
        .copied()
        .unwrap_or(parse_common_torrents)
}

fn parse_detail_page(indexer: &str, html: &str, url: &str) -> TorrentInfoPage {
    let doc = Html::parse_document(html);
    parser_for(indexer)(&doc, url)
}

fn parse_1337x(doc: &Html, _url: &str) -> TorrentInfoPage {
    let mut info = TorrentInfoPage::default();
    if let Some(magnet) = first_href(doc, "a[href^='magnet:?']") {
        info.magnet_url = Some(magnet);
    }
    if let Some(hash) = doc
        .select(&Selector::parse("div.infohash-box span").expect("infohash-box span"))
        .next()
        .map(|el| el.text().collect::<String>().trim().to_string())
        .filter(|h| h.len() == 40)
    {
        info.info_hash = Some(hash);
    }
    info
}

fn parse_torrent_downloads(doc: &Html, _url: &str) -> TorrentInfoPage {
    let mut info = TorrentInfoPage::default();
    if let Some(magnet) = first_href(doc, "a[href^='magnet:?']") {
        info.magnet_url = Some(magnet);
    }
    if let Some(wrapper) = doc
        .select(&Selector::parse("div#main_wrapper").expect("main_wrapper"))
        .next()
    {
        let wrapper_text = wrapper.text().collect::<String>();
        if let Some(re) = INFO_HASH_RE.get()
            && let Some(m) = re.find(&wrapper_text)
        {
            info.info_hash = Some(m.as_str().to_string());
        }
    }
    info
}

fn parse_torrentdownloads(doc: &Html, url: &str) -> TorrentInfoPage {
    let mut info = TorrentInfoPage::default();
    if let Some(hash) = doc
        .select(&Selector::parse("td").expect("td"))
        .find(|el| {
            let text = el.text().collect::<String>();
            INFO_HASH_RE
                .get()
                .is_some_and(|re| re.is_match(text.trim()))
        })
        .map(|el| el.text().collect::<String>().trim().to_string())
    {
        info.info_hash = Some(hash);
    }
    if let Some(href) = first_href(doc, "a[href*='/td.php?']") {
        info.download_url = Some(resolve_url(url, &href));
    } else if let Some(magnet) = first_href(doc, "a[href^='magnet:?']") {
        info.magnet_url = Some(magnet);
    }
    info
}

fn parse_badass_torrents(doc: &Html, url: &str) -> TorrentInfoPage {
    let mut info = TorrentInfoPage::default();
    if let Some(hash) = doc
        .select(&Selector::parse("td").expect("td"))
        .find(|el| {
            let text = el.text().collect::<String>();
            INFO_HASH_RE
                .get()
                .is_some_and(|re| re.is_match(text.trim()))
        })
        .map(|el| el.text().collect::<String>().trim().to_string())
    {
        info.info_hash = Some(hash);
    }
    if let Some(magnet) = first_href(doc, "a[href^='magnet:?']") {
        info.magnet_url = Some(magnet);
    }
    for el in doc.select(&Selector::parse("a").expect("a")) {
        let text = el.text().collect::<String>();
        if text.trim() == "Torrent Download" {
            if let Some(href) = el.value().attr("href") {
                info.download_url = Some(resolve_url(url, href));
            }
            break;
        }
    }
    info
}

fn parse_itorrent(doc: &Html, url: &str) -> TorrentInfoPage {
    let mut info = TorrentInfoPage::default();
    if let Some(magnet) = first_href(doc, "a[href^='magnet:?']") {
        info.info_hash = parser::extract_info_hash(&magnet).map(|h| h.to_string());
    }
    if let Some(href) = first_href(doc, "a.jq-download") {
        info.download_url = Some(resolve_url(url, &href));
    }
    info
}

fn parse_gktorrent(doc: &Html, url: &str) -> TorrentInfoPage {
    let mut info = TorrentInfoPage::default();
    if let Some(magnet) = first_href(doc, "a[href^='magnet:?']") {
        info.info_hash = parser::extract_info_hash(&magnet).map(|h| h.to_string());
    }
    let download_sel = Selector::parse("div.btn-download a").expect("div.btn-download a");
    if let Some(href) = doc
        .select(&download_sel)
        .next()
        .and_then(|el| el.value().attr("href"))
    {
        info.download_url = Some(resolve_url(url, href));
    }
    info
}

fn parse_common_torrents(doc: &Html, _url: &str) -> TorrentInfoPage {
    let mut info = TorrentInfoPage::default();
    if let Some(magnet) = first_href(doc, "a[href^='magnet:?']") {
        info.magnet_url = Some(magnet.clone());
        info.info_hash = parser::extract_info_hash(&magnet).map(|h| h.to_string());
    }
    info
}

fn first_href(doc: &Html, selector: &str) -> Option<String> {
    let sel = Selector::parse(selector).ok()?;
    doc.select(&sel)
        .next()
        .and_then(|el| el.value().attr("href"))
        .map(|href| href.to_string())
}

fn resolve_url(base: &str, href: &str) -> String {
    if href.starts_with("http://") || href.starts_with("https://") {
        return href.to_string();
    }
    if let Ok(abs) = reqwest::Url::parse(base).and_then(|b| b.join(href)) {
        return abs.to_string();
    }
    href.to_string()
}

type TorrentInfoParser = fn(&Html, &str) -> TorrentInfoPage;

static INFO_HASH_RE: OnceLock<Regex> = OnceLock::new();
static PARSERS: OnceLock<HashMap<&'static str, TorrentInfoParser>> = OnceLock::new();

fn parsers() -> &'static HashMap<&'static str, TorrentInfoParser> {
    PARSERS.get_or_init(|| {
        HashMap::from([
            ("1337x", parse_1337x as TorrentInfoParser),
            ("TheRARBG", parse_common_torrents),
            ("Torrent Downloads", parse_torrent_downloads),
            ("Badass Torrents", parse_badass_torrents),
            ("iTorrent", parse_itorrent),
            ("GkTorrent", parse_gktorrent),
            ("TorrentDownloads", parse_torrentdownloads),
        ])
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn registry_includes_requested_indexers() {
        let map = parsers();
        assert!(map.contains_key("Badass Torrents"));
        assert!(map.contains_key("iTorrent"));
        assert!(map.contains_key("GkTorrent"));
    }

    #[test]
    fn parse_common_extracts_magnet_hash() {
        let html = r#"<html><body><a href="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567">mag</a></body></html>"#;
        let doc = Html::parse_document(html);
        let info = parse_common_torrents(&doc, "https://example.com");
        assert!(info.magnet_url.is_some());
        assert_eq!(
            info.info_hash.as_deref(),
            Some("0123456789abcdef0123456789abcdef01234567")
        );
    }
}
