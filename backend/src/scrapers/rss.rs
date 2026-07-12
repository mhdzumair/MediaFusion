/// On-demand RSS feed scraper — called from `POST /api/v1/user-rss/feeds/{id}/scrape`.
///
/// Fetches the feed URL, parses items using the feed's `parsing_patterns`,
/// extracts info_hash (from magnet, direct field, or 40-hex substring),
/// finds or creates a media entry in the DB, then upserts the torrent stream.
use std::collections::HashSet;

use regex::Regex;
use serde_json::Value;
use sqlx::PgPool;
use tracing::{debug, info, warn};

use crate::db::{MediaType, TorrentType};
use crate::parser::{self, ParsedTitle};
use crate::scrapers::torrent_metadata::{
    self, parse_torrent_bytes, should_persist_torrent_file, torrent_file_for_storage,
};
use crate::state::KeywordFilterCache;

// ─── RSS XML parsing ──────────────────────────────────────────────────────────

/// A single parsed RSS item (all fields optional because patterns vary widely).
#[derive(Debug, Default)]
pub struct RssItem {
    pub title: Option<String>,
    pub link: Option<String>,
    pub description: Option<String>,
    pub enclosure_url: Option<String>,
    pub enclosure_length: Option<i64>,
    pub guid: Option<String>,
    /// Raw XML attributes/children collected as key→value for pattern extraction.
    pub extras: std::collections::HashMap<String, String>,
}

/// Strip XML namespace prefix (`{uri}local`) so Atom feeds (Reddit, etc.) match RSS tags.
fn local_tag_name(raw: &[u8]) -> String {
    let name = String::from_utf8_lossy(raw);
    name.rsplit('}')
        .next()
        .unwrap_or(name.as_ref())
        .to_lowercase()
}

/// Parse an RSS/Atom XML string into a list of flat items.
pub fn parse_rss_xml(xml: &str) -> Vec<RssItem> {
    use quick_xml::Reader;
    use quick_xml::events::Event;

    let mut reader = Reader::from_str(xml);
    reader.config_mut().trim_text(true);

    let mut items: Vec<RssItem> = Vec::new();
    let mut current: Option<RssItem> = None;
    let mut current_tag = String::new();
    let mut buf = Vec::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(ref e)) => {
                let name = local_tag_name(e.name().as_ref());
                match name.as_str() {
                    "item" | "entry" => {
                        current = Some(RssItem::default());
                    }
                    _ => {
                        if current.is_some() {
                            if name == "enclosure"
                                && let Some(item) = current.as_mut()
                            {
                                for attr in e.attributes().flatten() {
                                    let k = local_tag_name(attr.key.as_ref());
                                    let v = String::from_utf8_lossy(&attr.value).to_string();
                                    match k.as_str() {
                                        "url" => item.enclosure_url = Some(v),
                                        "length" => item.enclosure_length = v.parse().ok(),
                                        _ => {}
                                    }
                                }
                            } else if name == "link"
                                && let Some(item) = current.as_mut()
                            {
                                for attr in e.attributes().flatten() {
                                    if local_tag_name(attr.key.as_ref()) == "href" {
                                        let href = String::from_utf8_lossy(&attr.value).to_string();
                                        if item.link.is_none() {
                                            item.link = Some(href);
                                        }
                                    }
                                }
                            }
                            current_tag = name;
                        }
                    }
                }
            }
            Ok(Event::Empty(ref e)) if current.is_some() => {
                let name = local_tag_name(e.name().as_ref());
                if name == "enclosure"
                    && let Some(item) = current.as_mut()
                {
                    for attr in e.attributes().flatten() {
                        let k = local_tag_name(attr.key.as_ref());
                        let v = String::from_utf8_lossy(&attr.value).to_string();
                        match k.as_str() {
                            "url" => item.enclosure_url = Some(v),
                            "length" => item.enclosure_length = v.parse().ok(),
                            _ => {}
                        }
                    }
                } else if name == "link"
                    && let Some(item) = current.as_mut()
                {
                    for attr in e.attributes().flatten() {
                        if local_tag_name(attr.key.as_ref()) == "href" {
                            let href = String::from_utf8_lossy(&attr.value).to_string();
                            if item.link.is_none() {
                                item.link = Some(href);
                            }
                        }
                    }
                }
            }
            Ok(Event::Text(ref e)) => {
                if let Some(item) = current.as_mut() {
                    let text = e.decode().unwrap_or_default().to_string();
                    if !text.trim().is_empty() {
                        match current_tag.as_str() {
                            "title" => item.title = Some(text.clone()),
                            "link" => {
                                if item.link.is_none() {
                                    item.link = Some(text.clone());
                                }
                            }
                            "description" | "summary" | "content" => {
                                if let Some(existing) = &mut item.description {
                                    existing.push_str(&text);
                                } else {
                                    item.description = Some(text.clone());
                                }
                            }
                            "guid" | "id" => item.guid = Some(text.clone()),
                            other => {
                                item.extras
                                    .entry(other.to_string())
                                    .or_insert_with(|| text.clone());
                            }
                        }
                    }
                }
            }
            Ok(Event::End(ref e)) => {
                let name = local_tag_name(e.name().as_ref());
                if (name == "item" || name == "entry")
                    && let Some(item) = current.take()
                {
                    items.push(item);
                }
                if current.is_some() {
                    current_tag.clear();
                }
            }
            Ok(Event::Eof) => break,
            Err(_) => break,
            _ => {}
        }
        buf.clear();
    }
    items
}

// ─── Pattern-based field extraction ──────────────────────────────────────────

/// Extract a value from an RSS item using the field path specified in `parsing_patterns`.
/// Supports: direct field name, "extras.field_name", and falls back to scanning all text fields.
fn extract_field<'a>(item: &'a RssItem, field_path: Option<&str>) -> Option<&'a str> {
    let path = match field_path {
        Some(p) if !p.is_empty() => p,
        _ => return None,
    };
    match path {
        "title" => item.title.as_deref(),
        "link" => item.link.as_deref(),
        "description" | "summary" | "content" => item.description.as_deref(),
        "enclosure" | "enclosure_url" => item.enclosure_url.as_deref(),
        "guid" | "id" => item.guid.as_deref(),
        other => item.extras.get(other).map(|s| s.as_str()),
    }
}

/// Apply a regex to text and return the first capture group (or full match).
fn apply_regex(text: &str, pattern: &str) -> Option<String> {
    Regex::new(pattern).ok().and_then(|re| {
        re.captures(text).map(|c| {
            c.get(1)
                .or_else(|| c.get(0))
                .map(|m| m.as_str().to_string())
                .unwrap_or_default()
        })
    })
}

/// Download a .torrent file and extract metadata.
/// If `credential_params` is provided (a JSON object), its key/value pairs are appended
/// as query parameters to `url` — used for feeds that embed auth in their URL
/// (e.g. `?username=xxx&passkey=xxx`) but whose item download links lack those creds.
async fn torrent_from_url(
    http: &reqwest::Client,
    url: &str,
    credential_params: Option<&Value>,
) -> Option<torrent_metadata::ParsedTorrent> {
    let effective_url: std::borrow::Cow<str> = if let Some(creds) = credential_params {
        if let Some(obj) = creds.as_object().filter(|o| !o.is_empty()) {
            let sep = if url.contains('?') { '&' } else { '?' };
            let qs: String = obj
                .iter()
                .filter_map(|(k, v)| {
                    v.as_str().map(|val| {
                        format!("{}={}", urlencoding::encode(k), urlencoding::encode(val))
                    })
                })
                .collect::<Vec<_>>()
                .join("&");
            std::borrow::Cow::Owned(format!("{url}{sep}{qs}"))
        } else {
            std::borrow::Cow::Borrowed(url)
        }
    } else {
        std::borrow::Cow::Borrowed(url)
    };

    let bytes = torrent_metadata::download_torrent_bytes(
        http,
        &effective_url,
        std::time::Duration::from_secs(15),
    )
    .await?;
    parse_torrent_bytes(&bytes)
}

/// Parsed torrent metadata extracted from an RSS item.
struct ExtractedTorrent {
    info_hash: String,
    torrent_file: Option<Vec<u8>>,
    announce_list: Vec<String>,
}

async fn extract_torrent_metadata(
    http: &reqwest::Client,
    item: &RssItem,
    patterns: &Value,
    feed_torrent_type: TorrentType,
    credential_params: Option<&Value>,
) -> Option<ExtractedTorrent> {
    if let Some(hash) = extract_info_hash(item, patterns) {
        let magnet_candidates = [
            item.link.as_deref(),
            item.enclosure_url.as_deref(),
            item.description.as_deref(),
        ];
        let announce_list = magnet_candidates
            .iter()
            .flatten()
            .find(|c| c.contains("magnet:"))
            .map(|m| torrent_metadata::announce_list_from_magnet(m))
            .unwrap_or_default();
        return Some(ExtractedTorrent {
            info_hash: hash,
            torrent_file: None,
            announce_list,
        });
    }

    let has_credential_params = credential_params
        .and_then(|v| v.as_object())
        .map(|o| !o.is_empty())
        .unwrap_or(false);

    let torrent_url = item
        .link
        .as_deref()
        .or(item.enclosure_url.as_deref())
        .filter(|u| {
            let l = u.to_lowercase();
            // Standard torrent URL patterns, plus any link when credentials are configured
            // (the feed owner set credentials because the link requires auth to serve a torrent).
            l.ends_with(".torrent")
                || l.contains("/torrent")
                || l.contains("torrent?")
                || has_credential_params
        })?;

    let parsed = torrent_from_url(http, torrent_url, credential_params).await?;
    let torrent_file = if should_persist_torrent_file(feed_torrent_type) {
        torrent_file_for_storage(feed_torrent_type, Some(parsed.raw_bytes.clone()))
    } else {
        None
    };
    Some(ExtractedTorrent {
        info_hash: parsed.info_hash,
        torrent_file,
        announce_list: parsed.announce_list,
    })
}

/// Extract the info_hash from an item using the feed's parsing_patterns.
/// Priority: `info_hash` field → `info_hash_regex` on `info_hash_source` → magnet in `magnet`/`link`/`enclosure_url`/`description`.
fn extract_info_hash(item: &RssItem, patterns: &Value) -> Option<String> {
    // 1. Direct info_hash field
    let direct_field = patterns.get("info_hash").and_then(|v| v.as_str());
    if let Some(val) = extract_field(item, direct_field)
        && let Some(h) = parser::extract_info_hash(val)
    {
        return Some(h);
    }

    // 2. info_hash_regex applied to info_hash_source
    let ih_regex = patterns.get("info_hash_regex").and_then(|v| v.as_str());
    if let Some(regex) = ih_regex {
        let source_path = patterns.get("info_hash_source").and_then(|v| v.as_str());
        let source_text = extract_field(item, source_path)
            .or(item.description.as_deref())
            .or(item.link.as_deref())
            .unwrap_or("");
        if let Some(m) = apply_regex(source_text, regex)
            && let Some(h) = parser::extract_info_hash(&m)
        {
            return Some(h);
        }
    }

    // 3. Magnet link from `magnet` field, then common locations
    let magnet_field = patterns.get("magnet").and_then(|v| v.as_str());
    let magnet_candidates = [
        extract_field(item, magnet_field),
        item.link.as_deref(),
        item.enclosure_url.as_deref(),
        item.description.as_deref(),
    ];
    for candidate in magnet_candidates.iter().flatten() {
        if (candidate.contains("magnet:") || candidate.contains("btih:"))
            && let Some(h) = parser::extract_info_hash(candidate)
        {
            return Some(h);
        }
    }

    // 4. magnet_regex
    let m_regex = patterns.get("magnet_regex").and_then(|v| v.as_str());
    if let Some(regex) = m_regex {
        let source_text = item.description.as_deref().unwrap_or("");
        if let Some(m) = apply_regex(source_text, regex)
            && let Some(h) = parser::extract_info_hash(&m)
        {
            return Some(h);
        }
    }

    // 5. Last resort: scan description/link for any 40-hex string
    for text in [item.description.as_deref(), item.link.as_deref()]
        .iter()
        .flatten()
    {
        if let Some(h) = parser::extract_info_hash(text) {
            return Some(h);
        }
    }

    None
}

/// Extract the title from an item (from patterns.title field, or fall back to item.title).
fn extract_title(item: &RssItem, patterns: &Value) -> Option<String> {
    let field = patterns.get("title").and_then(|v| v.as_str());
    extract_field(item, field)
        .map(|s| s.to_string())
        .or_else(|| item.title.clone())
}

/// Extract size in bytes from the item. Tries patterns.size field, enclosure length, then regex in description.
fn extract_size(item: &RssItem, patterns: &Value) -> i64 {
    // From parsing pattern field
    let size_field = patterns.get("size").and_then(|v| v.as_str());
    if let Some(val) = extract_field(item, size_field) {
        if let Ok(n) = val.parse::<i64>() {
            return n;
        }
        // Try to parse "500 MB" style
        if let Some(b) = parse_size_str(val) {
            return b;
        }
    }
    // From enclosure length
    if let Some(len) = item.enclosure_length {
        return len;
    }
    // From description using regex for common patterns like "1.5 GB" or "1500 MB"
    if let Some(desc) = item.description.as_deref()
        && let Some(b) = extract_size_from_text(desc)
    {
        return b;
    }
    0
}

fn parse_size_str(s: &str) -> Option<i64> {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let re = RE.get_or_init(|| Regex::new(r"(?i)([\d.]+)\s*(TB|GB|MB|KB|B)").unwrap());
    re.captures(s).and_then(|c| {
        let num: f64 = c[1].parse().ok()?;
        let mult: f64 = match c[2].to_uppercase().as_str() {
            "TB" => 1024.0_f64.powi(4),
            "GB" => 1024.0_f64.powi(3),
            "MB" => 1024.0_f64.powi(2),
            "KB" => 1024.0,
            _ => 1.0,
        };
        Some((num * mult) as i64)
    })
}

fn extract_size_from_text(text: &str) -> Option<i64> {
    parse_size_str(text)
}

/// Extract seeders as i32.
fn extract_seeders(item: &RssItem, patterns: &Value) -> Option<i32> {
    let field = patterns.get("seeders").and_then(|v| v.as_str());
    extract_field(item, field)
        .and_then(|v| v.trim().parse::<i32>().ok())
        .or_else(|| {
            // Try extras["seeders"]
            item.extras.get("seeders").and_then(|v| v.parse().ok())
        })
}

// ─── Media find-or-create ─────────────────────────────────────────────────────

use crate::scrapers::media_resolve::MediaEntry;

/// Delegate to the shared media resolution module. When `strict` is true,
/// unmatched titles are skipped rather than becoming junk stub media rows.
async fn find_or_create_media(
    pool: &PgPool,
    http: &reqwest::Client,
    title: &str,
    year: Option<i32>,
    is_series: bool,
    catalog_ids: &[&str],
    tmdb_api_key: Option<&str>,
    cinemeta_fallback_enabled: bool,
    strict: bool,
) -> Option<MediaEntry> {
    if strict {
        crate::scrapers::media_resolve::find_or_create_media_strict(
            pool,
            http,
            title,
            year,
            is_series,
            catalog_ids,
            tmdb_api_key,
            cinemeta_fallback_enabled,
            &[],
            "tmdb",
        )
        .await
    } else {
        crate::scrapers::media_resolve::find_or_create_media(
            pool,
            http,
            title,
            year,
            is_series,
            catalog_ids,
            tmdb_api_key,
            cinemeta_fallback_enabled,
        )
        .await
    }
}

// ─── Stream upsert ────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum RssStreamUpsert {
    Processed,
    Skipped(&'static str),
}

/// Ensure an existing torrent stream is linked to `media_id`. Returns true when a new link row is created.
async fn ensure_stream_media_link(pool: &PgPool, stream_id: i32, media_id: i32) -> bool {
    let already_linked: bool = sqlx::query_scalar(
        "SELECT EXISTS(SELECT 1 FROM stream_media_link WHERE stream_id = $1 AND media_id = $2)",
    )
    .bind(stream_id)
    .bind(media_id)
    .fetch_one(pool)
    .await
    .unwrap_or(false);

    if already_linked {
        return false;
    }

    crate::db::link_stream_to_media(
        pool,
        crate::db::StreamId(stream_id),
        crate::db::MediaId(media_id),
    )
    .await
    .is_ok()
}

fn is_sports_content_type(content_type: &str) -> bool {
    content_type.trim().eq_ignore_ascii_case("sports")
}

fn effective_catalog_id(catalog_id: Option<&str>) -> Option<&str> {
    catalog_id.filter(|c| !c.trim().is_empty() && !c.eq_ignore_ascii_case("auto"))
}

/// Upsert a single torrent stream for the given media entry.
#[allow(clippy::too_many_arguments)]
async fn upsert_rss_stream(
    pool: &PgPool,
    info_hash: &str,
    name: &str,
    source: &str,
    seeders: Option<i32>,
    size: i64,
    media_id: i32,
    is_series: bool,
    parsed: &ParsedTitle,
    torrent_type: TorrentType,
    torrent_file: Option<Vec<u8>>,
    announce_list: &[String],
) -> RssStreamUpsert {
    let mut season = parsed.seasons.first().copied();
    let mut episode = parsed.episodes.first().copied();
    if is_series && (season.is_none() || episode.is_none()) {
        // PTT missed season/episode (e.g. absolute-numbered anime releases, or
        // a naming convention its patterns don't cover) — fall back to the
        // dedicated episode detector before giving up. Absolute numbering has
        // no season concept, so default to season 1 rather than dropping the
        // episode number entirely.
        if let Some(ep) = crate::parser::episode_detector::detect_episode(name, 1) {
            season = season.or(Some(ep.season));
            episode = episode.or(Some(ep.episode));
        } else if episode.is_some() {
            season = season.or(Some(1));
        }
    }
    let media_type = if is_series {
        MediaType::Series
    } else {
        MediaType::Movie
    };

    let mut files = Vec::new();
    if is_series && let (Some(s), Some(e)) = (season, episode) {
        files.push(crate::db::StreamFileStoreInput {
            file_index: 0,
            filename: name.to_string(),
            size: Some(size),
            season_number: s,
            episode_number: e,
        });
    }

    let stream = crate::db::TorrentStoreInput {
        base: crate::db::StreamStoreBase::from_parsed(name.to_string(), source.to_string(), parsed)
            .scraper_defaults(),
        info_hash: info_hash.to_string(),
        total_size: size,
        seeders,
        torrent_type,
        torrent_file,
        announce_list: announce_list.to_vec(),
        files,
    };

    let opts = crate::db::StoreStreamOpts::scraper(crate::db::MediaId(media_id), media_type)
        .with_episode(season, episode, None);

    match crate::db::store_torrent_stream(pool, &stream, &opts).await {
        Ok(r) if r.was_inserted() => RssStreamUpsert::Processed,
        Ok(r) => {
            if ensure_stream_media_link(pool, r.stream_id().0, media_id).await {
                RssStreamUpsert::Processed
            } else {
                RssStreamUpsert::Skipped("stream_already_linked")
            }
        }
        Err(_) => RssStreamUpsert::Skipped("stream_store_error"),
    }
}

// ─── Feed metrics update ──────────────────────────────────────────────────────

async fn update_feed_metrics(
    pool: &PgPool,
    feed_id: i32,
    items_found: i64,
    items_processed: i64,
    items_skipped: i64,
    errors: i64,
    duration_secs: f64,
    skip_reasons: &std::collections::HashMap<String, i64>,
) {
    let metrics = serde_json::json!({
        "total_items_found": items_found,
        "total_items_processed": items_processed,
        "total_items_skipped": items_skipped,
        "total_errors": errors,
        "items_processed_last_run": items_processed,
        "items_skipped_last_run": items_skipped,
        "errors_last_run": errors,
        "last_scrape_duration": duration_secs,
        "skip_reasons": skip_reasons,
    });

    let _ = sqlx::query(
        "UPDATE rss_feed SET metrics = $1::jsonb, last_scraped_at = NOW(), updated_at = NOW() WHERE id = $2"
    )
    .bind(metrics)
    .bind(feed_id)
    .execute(pool)
    .await;
}

// ─── Public wrappers for route handlers ──────────────────────────────────────

pub fn extract_info_hash_pub(item: &RssItem, patterns: &Value) -> Option<String> {
    extract_info_hash(item, patterns)
}

pub fn extract_size_pub(item: &RssItem, patterns: &Value) -> i64 {
    extract_size(item, patterns)
}

// ─── Public entry point ───────────────────────────────────────────────────────

pub struct ScrapeResult {
    pub items_found: i64,
    pub items_processed: i64,
    pub items_skipped: i64,
    pub errors: i64,
}

/// Fetch and process a single RSS feed. Returns scrape statistics.
#[allow(clippy::too_many_arguments)]
pub async fn scrape_feed(
    pool: &PgPool,
    http: &reqwest::Client,
    feed_id: i32,
    feed_url: &str,
    feed_name: &str,
    feed_source: Option<&str>,
    parsing_patterns: Option<&Value>,
    _filters: Option<&Value>,
    _auto_detect_catalog: bool,
    feed_torrent_type: TorrentType,
    tmdb_api_key: Option<&str>,
    cinemeta_fallback_enabled: bool,
    keyword_filters: &KeywordFilterCache,
    credential_params: Option<&Value>,
    content_type: &str,
    catalog_id: Option<&str>,
    media_resolve_mode: &str,
) -> ScrapeResult {
    let strict_media_resolve = media_resolve_mode != "create_stub";
    let start = std::time::Instant::now();
    let empty_patterns = Value::Object(serde_json::Map::new());
    let patterns = parsing_patterns.unwrap_or(&empty_patterns);

    // Fetch the feed
    let xml = match http
        .get(feed_url)
        .timeout(std::time::Duration::from_secs(30))
        .send()
        .await
    {
        Ok(r) if r.status().is_success() => match r.text().await {
            Ok(t) => t,
            Err(e) => {
                warn!("rss_scraper: failed to read feed body for {feed_name}: {e}");
                update_feed_metrics(
                    pool,
                    feed_id,
                    0,
                    0,
                    0,
                    1,
                    start.elapsed().as_secs_f64(),
                    &std::collections::HashMap::new(),
                )
                .await;
                return ScrapeResult {
                    items_found: 0,
                    items_processed: 0,
                    items_skipped: 0,
                    errors: 1,
                };
            }
        },
        Ok(r) => {
            warn!("rss_scraper: feed {feed_name} returned HTTP {}", r.status());
            update_feed_metrics(
                pool,
                feed_id,
                0,
                0,
                0,
                1,
                start.elapsed().as_secs_f64(),
                &std::collections::HashMap::new(),
            )
            .await;
            return ScrapeResult {
                items_found: 0,
                items_processed: 0,
                items_skipped: 0,
                errors: 1,
            };
        }
        Err(e) => {
            warn!(
                error_kind = crate::util::http::transport_error_kind(&e),
                "rss_scraper: could not fetch feed {feed_name}: {e}"
            );
            update_feed_metrics(
                pool,
                feed_id,
                0,
                0,
                0,
                1,
                start.elapsed().as_secs_f64(),
                &std::collections::HashMap::new(),
            )
            .await;
            return ScrapeResult {
                items_found: 0,
                items_processed: 0,
                items_skipped: 0,
                errors: 1,
            };
        }
    };

    let items = parse_rss_xml(&xml);
    let items_found = items.len() as i64;
    if items_found == 0 {
        warn!("rss_scraper: no items in feed {feed_name}");
        update_feed_metrics(
            pool,
            feed_id,
            0,
            0,
            0,
            0,
            start.elapsed().as_secs_f64(),
            &std::collections::HashMap::new(),
        )
        .await;
        return ScrapeResult {
            items_found: 0,
            items_processed: 0,
            items_skipped: 0,
            errors: 0,
        };
    }

    let source = feed_source.filter(|s| !s.is_empty()).unwrap_or(feed_name);
    let source = format!("RSS Feed: {source}");

    let mut processed = 0i64;
    let mut skipped = 0i64;
    let mut errors = 0i64;
    let mut seen_hashes: HashSet<String> = HashSet::new();
    let mut skip_reasons: std::collections::HashMap<String, i64> = std::collections::HashMap::new();
    let sports_feed = is_sports_content_type(content_type);
    let resolved_catalog_id = effective_catalog_id(catalog_id);

    let record_skip =
        |reason: &'static str,
         skipped: &mut i64,
         skip_reasons: &mut std::collections::HashMap<String, i64>| {
            *skipped += 1;
            *skip_reasons.entry(reason.to_string()).or_insert(0) += 1;
        };

    for item in &items {
        // Extract title
        let title = match extract_title(item, patterns) {
            Some(t) if !t.is_empty() => t,
            _ => {
                record_skip("empty_title", &mut skipped, &mut skip_reasons);
                continue;
            }
        };

        // Skip content blocked by the admin keyword filter (includes adult keywords)
        if keyword_filters.matches_blocked_keyword(&title) {
            debug!("rss_scraper: skipping blocked title '{title}'");
            record_skip("keyword_blocked", &mut skipped, &mut skip_reasons);
            continue;
        }

        // Extract torrent metadata — fall back to downloading .torrent file if needed
        let extracted = match extract_torrent_metadata(
            http,
            item,
            patterns,
            feed_torrent_type,
            credential_params,
        )
        .await
        {
            Some(e) => e,
            None => {
                debug!("rss_scraper: no info_hash or torrent URL for '{title}'");
                record_skip("no_torrent", &mut skipped, &mut skip_reasons);
                continue;
            }
        };
        let info_hash = extracted.info_hash;

        if seen_hashes.contains(&info_hash) {
            record_skip("duplicate_hash", &mut skipped, &mut skip_reasons);
            continue;
        }

        let size = extract_size(item, patterns);
        let seeders = extract_seeders(item, patterns);

        let media = if sports_feed {
            // Sports path: use sports stub creator (sets is_add_title_to_poster=true) + catalog link
            let clean_title = parser::sports_parser::clean_sports_title(&title);
            let detected_cat = parser::sports_parser::detect_sports_category(&title);
            let sports_catalog = resolved_catalog_id.or(detected_cat).unwrap_or("sports");
            let catalogs: Vec<&str> = vec![sports_catalog];

            // Extract year from sports title parser
            let sports_parsed = parser::sports_parser::parse_sports_title(&title);
            let year = sports_parsed.year;

            match crate::scrapers::media_resolve::find_or_create_sports_stub(
                pool,
                &clean_title,
                year,
                None,
                "movie",
                sports_catalog,
            )
            .await
            {
                Some(id) => {
                    crate::scrapers::media_resolve::link_to_catalogs(pool, id, &catalogs).await;
                    crate::scrapers::media_resolve::MediaEntry {
                        id,
                        title: clean_title,
                        year,
                    }
                }
                None => {
                    warn!("rss_scraper: could not find/create sports media for '{title}'");
                    errors += 1;
                    continue;
                }
            }
        } else {
            // Standard PTT path
            let parsed = parser::parse_title(&title);
            let is_series = match content_type.trim().to_ascii_lowercase().as_str() {
                "series" => true,
                "movies" => false,
                _ => !parsed.seasons.is_empty() || !parsed.episodes.is_empty(),
            };

            let rss_catalog = if is_series {
                "rss_feed_series"
            } else {
                "rss_feed_movies"
            };
            let catalogs: Vec<&str> = vec![rss_catalog];
            let parsed_title = parsed
                .title
                .as_deref()
                .filter(|t| !t.is_empty())
                .unwrap_or(&title);

            match find_or_create_media(
                pool,
                http,
                parsed_title,
                parsed.year,
                is_series,
                &catalogs,
                tmdb_api_key,
                cinemeta_fallback_enabled,
                strict_media_resolve,
            )
            .await
            {
                Some(m) => m,
                None => {
                    if strict_media_resolve {
                        debug!("rss_scraper: no confident media match for '{title}', skipping");
                        record_skip("no_media_match", &mut skipped, &mut skip_reasons);
                    } else {
                        debug!("rss_scraper: could not resolve or create media for '{title}'");
                        record_skip("media_create_failed", &mut skipped, &mut skip_reasons);
                    }
                    continue;
                }
            }
        };

        // For stream upsert we need is_series and parsed; re-derive for non-sports
        let (is_series_for_stream, parsed_for_stream) = if sports_feed {
            (false, parser::parse_title(&title))
        } else {
            let parsed = parser::parse_title(&title);
            let is_series = match content_type.trim().to_ascii_lowercase().as_str() {
                "series" => true,
                "movies" => false,
                _ => !parsed.seasons.is_empty() || !parsed.episodes.is_empty(),
            };
            (is_series, parsed)
        };

        // Upsert stream
        match upsert_rss_stream(
            pool,
            &info_hash,
            &title,
            &source,
            seeders,
            size,
            media.id,
            is_series_for_stream,
            &parsed_for_stream,
            feed_torrent_type,
            extracted.torrent_file,
            &extracted.announce_list,
        )
        .await
        {
            RssStreamUpsert::Processed => {
                processed += 1;
                info!(
                    "rss_scraper: inserted stream for '{}' ({:?}) → media {} ({})",
                    title, media.year, media.id, media.title
                );
            }
            RssStreamUpsert::Skipped(reason) => {
                record_skip(reason, &mut skipped, &mut skip_reasons);
            }
        }

        seen_hashes.insert(info_hash);
    }

    let duration = start.elapsed().as_secs_f64();
    update_feed_metrics(
        pool,
        feed_id,
        items_found,
        processed,
        skipped,
        errors,
        duration,
        &skip_reasons,
    )
    .await;

    ScrapeResult {
        items_found,
        items_processed: processed,
        items_skipped: skipped,
        errors,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_atom_feed_strips_namespace_and_extracts_content() {
        let xml = r#"<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Formula 1 2026. R09. British Grand Prix</title>
    <link href="https://www.reddit.com/r/test/comments/abc/post/" />
    <content type="html">&lt;a href="magnet:?xt=urn:btih:deadbeefdeadbeefdeadbeefdeadbeefdeadbeef&amp;amp;dn=test"&gt;magnet&lt;/a&gt;</content>
  </entry>
</feed>"#;

        let items = parse_rss_xml(xml);
        assert_eq!(items.len(), 1);
        let item = &items[0];
        assert_eq!(
            item.title.as_deref(),
            Some("Formula 1 2026. R09. British Grand Prix")
        );
        assert_eq!(
            item.link.as_deref(),
            Some("https://www.reddit.com/r/test/comments/abc/post/")
        );
        let desc = item.description.as_deref().unwrap_or("");
        assert!(desc.contains("magnet:?xt=urn:btih:deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"));
        assert!(extract_info_hash(item, &Value::Null).is_some());
    }
}
