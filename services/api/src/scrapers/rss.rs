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

use crate::parser::{self, ParsedTitle};

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

/// Parse an RSS/Atom XML string into a list of flat items.
pub fn parse_rss_xml(xml: &str) -> Vec<RssItem> {
    use quick_xml::events::Event;
    use quick_xml::Reader;

    let mut reader = Reader::from_str(xml);
    reader.config_mut().trim_text(true);

    let mut items: Vec<RssItem> = Vec::new();
    let mut current: Option<RssItem> = None;
    let mut current_tag = String::new();
    let mut buf = Vec::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(ref e)) => {
                let name = String::from_utf8_lossy(e.name().as_ref()).to_lowercase();
                match name.as_str() {
                    "item" | "entry" => {
                        current = Some(RssItem::default());
                    }
                    _ => {
                        if current.is_some() {
                            // Capture enclosure attributes
                            if name == "enclosure" {
                                if let Some(item) = current.as_mut() {
                                    for attr in e.attributes().flatten() {
                                        let k = String::from_utf8_lossy(attr.key.as_ref())
                                            .to_lowercase();
                                        let v = String::from_utf8_lossy(&attr.value).to_string();
                                        match k.as_str() {
                                            "url" => item.enclosure_url = Some(v),
                                            "length" => item.enclosure_length = v.parse().ok(),
                                            _ => {}
                                        }
                                    }
                                }
                            }
                            current_tag = name;
                        }
                    }
                }
            }
            Ok(Event::Empty(ref e)) => {
                if current.is_some() {
                    let name = String::from_utf8_lossy(e.name().as_ref()).to_lowercase();
                    if name == "enclosure" {
                        if let Some(item) = current.as_mut() {
                            for attr in e.attributes().flatten() {
                                let k = String::from_utf8_lossy(attr.key.as_ref()).to_lowercase();
                                let v = String::from_utf8_lossy(&attr.value).to_string();
                                match k.as_str() {
                                    "url" => item.enclosure_url = Some(v),
                                    "length" => item.enclosure_length = v.parse().ok(),
                                    _ => {}
                                }
                            }
                        }
                    }
                }
            }
            Ok(Event::Text(ref e)) => {
                if let Some(item) = current.as_mut() {
                    let text = e.unescape().unwrap_or_default().to_string();
                    if !text.trim().is_empty() {
                        match current_tag.as_str() {
                            "title" => item.title = Some(text.clone()),
                            "link" => {
                                if item.link.is_none() {
                                    item.link = Some(text.clone());
                                }
                            }
                            "description" | "summary" | "content" => {
                                item.description = Some(text.clone())
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
                let name = String::from_utf8_lossy(e.name().as_ref()).to_lowercase();
                if name == "item" || name == "entry" {
                    if let Some(item) = current.take() {
                        items.push(item);
                    }
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

/// Download a .torrent file and extract its info_hash.
async fn info_hash_from_torrent_url(http: &reqwest::Client, url: &str) -> Option<String> {
    let bytes = http
        .get(url)
        .timeout(std::time::Duration::from_secs(15))
        .send()
        .await
        .ok()?
        .bytes()
        .await
        .ok()?;
    let torrent = lava_torrent::torrent::v1::Torrent::read_from_bytes(&bytes).ok()?;
    let hash = torrent.info_hash();
    if hash.len() == 40 && hash.chars().all(|c| c.is_ascii_hexdigit()) {
        Some(hash.to_lowercase())
    } else {
        None
    }
}

/// Extract the info_hash from an item using the feed's parsing_patterns.
/// Priority: `info_hash` field → `info_hash_regex` on `info_hash_source` → magnet in `magnet`/`link`/`enclosure_url`/`description`.
fn extract_info_hash(item: &RssItem, patterns: &Value) -> Option<String> {
    // 1. Direct info_hash field
    let direct_field = patterns.get("info_hash").and_then(|v| v.as_str());
    if let Some(val) = extract_field(item, direct_field) {
        if let Some(h) = parser::extract_info_hash(val) {
            return Some(h);
        }
    }

    // 2. info_hash_regex applied to info_hash_source
    let ih_regex = patterns.get("info_hash_regex").and_then(|v| v.as_str());
    if let Some(regex) = ih_regex {
        let source_path = patterns.get("info_hash_source").and_then(|v| v.as_str());
        let source_text = extract_field(item, source_path)
            .or(item.description.as_deref())
            .or(item.link.as_deref())
            .unwrap_or("");
        if let Some(m) = apply_regex(source_text, regex) {
            if let Some(h) = parser::extract_info_hash(&m) {
                return Some(h);
            }
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
        if candidate.contains("magnet:") || candidate.contains("btih:") {
            if let Some(h) = parser::extract_info_hash(candidate) {
                return Some(h);
            }
        }
    }

    // 4. magnet_regex
    let m_regex = patterns.get("magnet_regex").and_then(|v| v.as_str());
    if let Some(regex) = m_regex {
        let source_text = item.description.as_deref().unwrap_or("");
        if let Some(m) = apply_regex(source_text, regex) {
            if let Some(h) = parser::extract_info_hash(&m) {
                return Some(h);
            }
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
    if let Some(desc) = item.description.as_deref() {
        if let Some(b) = extract_size_from_text(desc) {
            return b;
        }
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

struct MediaEntry {
    pub id: i64,
    pub title: String,
    pub year: Option<i32>,
}

/// Look up the media table by title similarity, create a minimal entry if not found.
async fn find_or_create_media(
    pool: &PgPool,
    title: &str,
    year: Option<i32>,
    is_series: bool,
    catalog_ids: &[&str],
) -> Option<MediaEntry> {
    let media_type = if is_series { "SERIES" } else { "MOVIE" };

    // 1. Try exact title match (case-insensitive) with optional year filter
    let row: Option<(i64, String, Option<i32>)> = if let Some(y) = year {
        sqlx::query_as(
            "SELECT id, title, year FROM media WHERE LOWER(title) = LOWER($1) AND type::text = $2 AND (year = $3 OR year = $4 OR year IS NULL) LIMIT 1"
        )
        .bind(title)
        .bind(media_type)
        .bind(y)
        .bind(y - 1)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten()
    } else {
        sqlx::query_as(
            "SELECT id, title, year FROM media WHERE LOWER(title) = LOWER($1) AND type::text = $2 LIMIT 1"
        )
        .bind(title)
        .bind(media_type)
        .fetch_optional(pool)
        .await
        .ok()
        .flatten()
    };

    if let Some((id, t, y)) = row {
        debug!("rss_scraper: found existing media {id} for '{title}'");
        return Some(MediaEntry {
            id,
            title: t,
            year: y,
        });
    }

    // 2. Fuzzy search: pg_trgm similarity
    let fuzzy: Option<(i64, String, Option<i32>)> = sqlx::query_as(
        "SELECT id, title, year FROM media WHERE type::text = $1 AND similarity(title, $2) > 0.4 ORDER BY similarity(title, $2) DESC LIMIT 1"
    )
    .bind(media_type)
    .bind(title)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten();

    if let Some((id, t, y)) = fuzzy {
        let sim = parser::similarity_ratio(title, &t);
        if sim >= 70 {
            debug!("rss_scraper: fuzzy match {id} (sim={sim}) for '{title}' → '{t}'");
            return Some(MediaEntry {
                id,
                title: t,
                year: y,
            });
        }
    }

    // 3. Create minimal media entry with internal mf* ID
    let clean = title
        .to_lowercase()
        .chars()
        .map(|c| if c.is_alphanumeric() { c } else { '_' })
        .collect::<String>();
    let clean = clean.trim_matches('_');
    let clean: String = clean.chars().take(40).collect();
    let year_str = year.map(|y| format!("_{y}")).unwrap_or_default();
    let hash_input = format!("{clean}{year_str}");
    let mf_prefix = if is_series { "mfs" } else { "mfm" };
    let short_hash: String = format!("{:x}", md5_short(&hash_input));
    let mf_id = format!("{mf_prefix}_{short_hash}");

    // Insert media row
    let insert: Option<(i64,)> = sqlx::query_as(
        r#"
        INSERT INTO media (type, title, year, adult, is_blocked, total_streams, popularity)
        VALUES ($1::mediatype, $2, $3, false, false, 0, 0.0)
        ON CONFLICT DO NOTHING
        RETURNING id
        "#,
    )
    .bind(media_type)
    .bind(title)
    .bind(year)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten();

    let media_id = match insert {
        Some((id,)) => id,
        None => {
            // Conflict — fetch it
            sqlx::query_scalar::<_, i64>(
                "SELECT id FROM media WHERE LOWER(title) = LOWER($1) AND type::text = $2 LIMIT 1",
            )
            .bind(title)
            .bind(media_type)
            .fetch_optional(pool)
            .await
            .ok()
            .flatten()?
        }
    };

    // Insert internal external_id
    let _ = sqlx::query(
        "INSERT INTO media_external_id (media_id, provider, external_id) VALUES ($1, 'mediafusion', $2) ON CONFLICT DO NOTHING"
    )
    .bind(media_id)
    .bind(&mf_id)
    .execute(pool)
    .await;

    // Link to catalogs
    for catalog_name in catalog_ids {
        let _ = sqlx::query(
            r#"
            INSERT INTO media_catalog_link (media_id, catalog_id)
            SELECT $1, c.id FROM catalog c WHERE c.name = $2
            ON CONFLICT DO NOTHING
            "#,
        )
        .bind(media_id)
        .bind(catalog_name)
        .execute(pool)
        .await;
    }

    info!("rss_scraper: created new media {media_id} (id={mf_id}) for '{title}'");
    Some(MediaEntry {
        id: media_id,
        title: title.to_string(),
        year,
    })
}

fn md5_short(s: &str) -> u32 {
    use std::hash::{Hash, Hasher};
    let mut h = std::collections::hash_map::DefaultHasher::new();
    s.hash(&mut h);
    h.finish() as u32
}

// ─── Stream upsert ────────────────────────────────────────────────────────────

/// Upsert a single torrent stream for the given media entry.
/// Returns true if a new row was inserted.
#[allow(clippy::too_many_arguments)]
async fn upsert_rss_stream(
    pool: &PgPool,
    info_hash: &str,
    name: &str,
    source: &str,
    seeders: Option<i32>,
    size: i64,
    media_id: i64,
    is_series: bool,
    parsed: &ParsedTitle,
) -> bool {
    // Check existing
    let existing: Option<(i32,)> =
        sqlx::query_as("SELECT stream_id FROM torrent_stream WHERE info_hash = $1")
            .bind(info_hash)
            .fetch_optional(pool)
            .await
            .unwrap_or(None);

    if let Some((stream_id,)) = existing {
        if let Some(s) = seeders {
            let _ = sqlx::query(
                "UPDATE torrent_stream SET seeders = GREATEST(seeders, $1) WHERE stream_id = $2",
            )
            .bind(s)
            .bind(stream_id)
            .execute(pool)
            .await;
        }
        return false;
    }

    // Insert base stream row
    let stream_row: Option<(i32,)> = sqlx::query_as(
        r#"
        INSERT INTO stream (
            stream_type, name, source,
            resolution, codec, quality,
            is_proper, is_repack, is_extended, is_complete, is_dubbed,
            release_group,
            is_active, is_blocked, is_public, playback_count,
            created_at, updated_at
        ) VALUES (
            'TORRENT'::streamtype, $1, $2,
            $3, $4, $5,
            $6, $7, $8, $9, $10,
            $11,
            true, false, true, 0,
            NOW(), NOW()
        )
        RETURNING id
        "#,
    )
    .bind(name)
    .bind(source)
    .bind(&parsed.resolution)
    .bind(&parsed.codec)
    .bind(&parsed.quality)
    .bind(parsed.is_proper)
    .bind(parsed.is_repack)
    .bind(parsed.is_extended)
    .bind(parsed.is_complete)
    .bind(parsed.is_dubbed)
    .bind(&parsed.release_group)
    .fetch_optional(pool)
    .await
    .ok()
    .flatten();

    let stream_id = match stream_row {
        Some((id,)) => id,
        None => return false,
    };

    // Insert torrent_stream
    let ts = sqlx::query(
        r#"
        INSERT INTO torrent_stream (
            stream_id, info_hash, total_size, seeders, torrent_type, file_count, created_at
        ) VALUES ($1, $2, $3, $4, 'PUBLIC'::torrenttype, 1, NOW())
        ON CONFLICT (info_hash) DO NOTHING
        "#,
    )
    .bind(stream_id)
    .bind(info_hash)
    .bind(size)
    .bind(seeders)
    .execute(pool)
    .await;

    if let Ok(r) = &ts {
        if r.rows_affected() == 0 {
            let _ = sqlx::query("DELETE FROM stream WHERE id = $1")
                .bind(stream_id)
                .execute(pool)
                .await;
            return false;
        }
    } else {
        return false;
    }

    // Link stream → media
    let season = parsed.seasons.first().copied();
    let episode = parsed.episodes.first().copied();

    if is_series {
        if let (Some(s), Some(e)) = (season, episode) {
            let sf: Option<(i32,)> = sqlx::query_as(
                "INSERT INTO stream_file (stream_id, file_index, filename, file_type) VALUES ($1, 0, $2, 'VIDEO'::filetype) ON CONFLICT (stream_id, file_index) DO NOTHING RETURNING id"
            )
            .bind(stream_id)
            .bind(name)
            .fetch_optional(pool)
            .await
            .ok()
            .flatten();

            if let Some((file_id,)) = sf {
                let _ = sqlx::query(
                    "INSERT INTO file_media_link (file_id, media_id, season_number, episode_number, is_primary, confidence, link_source) VALUES ($1, $2, $3, $4, true, 1.0, 'PTT_PARSER'::linksource) ON CONFLICT DO NOTHING"
                )
                .bind(file_id)
                .bind(media_id as i32)
                .bind(s)
                .bind(e)
                .execute(pool)
                .await;
            }
        } else {
            // Series but no season/episode — link to media via stream_media_link
            let _ = sqlx::query(
                "INSERT INTO stream_media_link (stream_id, media_id, is_primary) SELECT $1, $2, true WHERE NOT EXISTS (SELECT 1 FROM stream_media_link WHERE stream_id = $1 AND media_id = $2)"
            )
            .bind(stream_id)
            .bind(media_id as i32)
            .execute(pool)
            .await;
        }
    } else {
        let _ = sqlx::query(
            "INSERT INTO stream_media_link (stream_id, media_id, is_primary) SELECT $1, $2, true WHERE NOT EXISTS (SELECT 1 FROM stream_media_link WHERE stream_id = $1 AND media_id = $2)"
        )
        .bind(stream_id)
        .bind(media_id as i32)
        .execute(pool)
        .await;
    }

    true
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
        "skip_reasons": {}
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
) -> ScrapeResult {
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
                update_feed_metrics(pool, feed_id, 0, 0, 0, 1, start.elapsed().as_secs_f64()).await;
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
            update_feed_metrics(pool, feed_id, 0, 0, 0, 1, start.elapsed().as_secs_f64()).await;
            return ScrapeResult {
                items_found: 0,
                items_processed: 0,
                items_skipped: 0,
                errors: 1,
            };
        }
        Err(e) => {
            warn!("rss_scraper: could not fetch feed {feed_name}: {e}");
            update_feed_metrics(pool, feed_id, 0, 0, 0, 1, start.elapsed().as_secs_f64()).await;
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
        update_feed_metrics(pool, feed_id, 0, 0, 0, 0, start.elapsed().as_secs_f64()).await;
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

    for item in &items {
        // Extract title
        let title = match extract_title(item, patterns) {
            Some(t) if !t.is_empty() => t,
            _ => {
                skipped += 1;
                continue;
            }
        };

        // Skip adult content
        if parser::contains_adult_keywords(&title) {
            skipped += 1;
            continue;
        }

        // Extract info_hash — fall back to downloading torrent file if needed
        let info_hash = match extract_info_hash(item, patterns) {
            Some(h) => h,
            None => {
                // Try torrent download from link or enclosure_url
                let torrent_url = item
                    .link
                    .as_deref()
                    .or(item.enclosure_url.as_deref())
                    .filter(|u| {
                        let l = u.to_lowercase();
                        l.ends_with(".torrent") || l.contains("/torrent") || l.contains("torrent?")
                    });
                if let Some(url) = torrent_url {
                    match info_hash_from_torrent_url(http, url).await {
                        Some(h) => h,
                        None => {
                            debug!("rss_scraper: could not extract info_hash for '{title}'");
                            skipped += 1;
                            continue;
                        }
                    }
                } else {
                    debug!("rss_scraper: no info_hash or torrent URL for '{title}'");
                    skipped += 1;
                    continue;
                }
            }
        };

        if seen_hashes.contains(&info_hash) {
            skipped += 1;
            continue;
        }

        // Parse title with PTT
        let parsed = parser::parse_title(&title);
        let is_series = !parsed.seasons.is_empty() || !parsed.episodes.is_empty();

        let size = extract_size(item, patterns);
        let seeders = extract_seeders(item, patterns);

        // Determine catalogs
        let rss_catalog = if is_series {
            "rss_feed_series"
        } else {
            "rss_feed_movies"
        };
        let catalogs: Vec<&str> = vec![rss_catalog];

        // Find or create media
        let parsed_title = parsed.title.as_deref().unwrap_or(&title);
        let media =
            match find_or_create_media(pool, parsed_title, parsed.year, is_series, &catalogs).await
            {
                Some(m) => m,
                None => {
                    warn!("rss_scraper: could not find/create media for '{title}'");
                    errors += 1;
                    continue;
                }
            };

        // Upsert stream
        let inserted = upsert_rss_stream(
            pool, &info_hash, &title, &source, seeders, size, media.id, is_series, &parsed,
        )
        .await;

        seen_hashes.insert(info_hash);
        if inserted {
            processed += 1;
            info!(
                "rss_scraper: inserted stream for '{}' ({:?}) → media {} ({})",
                title, media.year, media.id, media.title
            );
        } else {
            skipped += 1;
        }
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
    )
    .await;

    ScrapeResult {
        items_found,
        items_processed: processed,
        items_skipped: skipped,
        errors,
    }
}
