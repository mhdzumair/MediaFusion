pub mod constants;
pub mod episode_detector;
pub mod filter;
pub mod sort;
pub mod sports_parser;

pub use constants::{
    LANGUAGES_FILTERS, QUALITY_GROUPS, RESOLUTIONS, default_hdr_filter_vec,
    default_language_sorting_values, default_quality_filter_groups, default_resolutions_vec,
    expand_quality_filter, normalized_hdr_filter_and_display,
};
pub use filter::{
    FilterContext, MAX_STREAM_NAME_FILTER_PATTERN_LENGTH, MAX_STREAM_NAME_FILTER_PATTERNS,
    cap_streams, filter_sort_and_cap_streams, filter_streams_by_preferences, resolution_cap_key,
    sort_size_bytes_for_row,
};
pub use sort::{
    compare_sort_keys, parse_created_at_ts, quality_rank, sort_and_cap_stream_rows,
    torrent_sort_key,
};

pub use sports_parser::{
    FightingSeriesEpisode, RacingParsed, TEAM_MATCHUP_CATEGORIES, WweEpisodeInfo,
    canonical_matchup_title, classify_aew_title, classify_drive_to_survive,
    classify_fighting_series_title, classify_wwe_title, clean_fighting_event_title,
    clean_sports_title, clean_ufc_event_title, detect_fighting_brand, detect_sports_category,
    extract_event_date_from_title, extract_team_matchup, is_sports_title, numbered_prefix_episode,
    parse_racing_title, parse_sports_title, racing_file_display_title, racing_file_episode,
    racing_session_episode, resolve_team_matchup_media_title,
};

use std::sync::OnceLock;

/// Parsed fields from a torrent/release title.
#[derive(Debug, Default, Clone)]
pub struct ParsedTitle {
    pub title: Option<String>,
    /// Per-episode title (text between `SxxExx` marker and first release token).
    /// `None` for movies and episodes without descriptive title text.
    pub episode_title: Option<String>,
    pub year: Option<i32>,
    pub resolution: Option<String>,
    pub quality: Option<String>,
    pub codec: Option<String>,
    pub audio: Vec<String>,
    pub channels: Vec<String>,
    pub hdr: Vec<String>,
    pub languages: Vec<String>,
    pub seasons: Vec<i32>,
    pub episodes: Vec<i32>,
    pub is_proper: bool,
    pub is_repack: bool,
    pub is_extended: bool,
    pub is_complete: bool,
    pub is_dubbed: bool,
    pub is_subbed: bool,
    pub is_remastered: bool,
    pub is_upscaled: bool,
    pub release_group: Option<String>,
    pub bit_depth: Option<String>,
}

pub fn parse_title(raw: &str) -> ParsedTitle {
    // Match Python scrapers: PTT.parse_title(title, translate_languages=True)
    let p = crate::ptt::parse(raw, true);
    ParsedTitle {
        title: Some(p.title),
        episode_title: p.episode_title,
        year: p.year,
        resolution: p.resolution,
        quality: p.quality,
        codec: p.codec,
        audio: p.audio,
        channels: p.channels,
        hdr: p.hdr,
        languages: p.languages,
        seasons: p.seasons,
        episodes: p.episodes,
        is_proper: p.is_proper,
        is_repack: p.is_repack,
        is_extended: p
            .edition
            .as_deref()
            .is_some_and(|e| e.to_lowercase().contains("extended")),
        is_complete: p.is_complete,
        is_dubbed: p.is_dubbed,
        is_subbed: p.is_subbed,
        is_remastered: p.is_remastered,
        is_upscaled: p.is_upscaled,
        release_group: p.group,
        bit_depth: p.bit_depth,
    }
}

/// Extract a 40-char hex info_hash from a string (URL or magnet).
pub fn extract_info_hash(s: &str) -> Option<String> {
    static INFO_HASH_RE: OnceLock<regex::Regex> = OnceLock::new();

    // btih: prefix in magnets
    let lower = s.to_lowercase();
    if let Some(pos) = lower.find("btih:") {
        let rest = &lower[pos + 5..];
        let hash: String = rest.chars().take(40).collect();
        if hash.len() == 40 && hash.chars().all(|c| c.is_ascii_hexdigit()) {
            return Some(hash);
        }
    }

    let re = INFO_HASH_RE.get_or_init(|| regex::Regex::new(r"[a-fA-F0-9]{40}").unwrap());
    re.find(s).map(|m| m.as_str().to_lowercase())
}

/// Extract the display name (`dn`) from a magnet URI.
pub fn extract_magnet_dn(magnet: &str) -> Option<String> {
    static DN_RE: OnceLock<regex::Regex> = OnceLock::new();
    let re = DN_RE.get_or_init(|| regex::Regex::new(r"[?&]dn=([^&]+)").unwrap());
    re.captures(magnet).and_then(|c| c.get(1)).map(|m| {
        let plus_decoded = m.as_str().replace('+', "%20");
        urlencoding::decode(&plus_decoded)
            .unwrap_or_default()
            .into_owned()
    })
}

/// Extract a magnet URI from a string that may be a bare magnet link or embed one.
pub fn extract_magnet_uri(s: &str) -> Option<String> {
    let trimmed = s.trim();
    if trimmed.starts_with("magnet:") {
        return Some(trimmed.replace("&amp;", "&"));
    }
    static MAGNET_RE: OnceLock<regex::Regex> = OnceLock::new();
    let re = MAGNET_RE.get_or_init(|| regex::Regex::new(r#"magnet:\?[^\s"'<>]+"#).unwrap());
    re.find(trimmed).map(|m| m.as_str().replace("&amp;", "&"))
}

/// Best stream/torrent name for a scraped magnet link.
/// Uses the magnet `dn=` display name when present; otherwise `fallback`.
pub fn stream_name_from_magnet(magnet: &str, fallback: &str) -> String {
    extract_magnet_dn(magnet)
        .filter(|name| !name.trim().is_empty())
        .unwrap_or_else(|| fallback.trim().to_string())
}

/// Parse stream metadata from a magnet URI using `dn=` when available.
pub fn parse_magnet_stream(magnet: &str, fallback: &str) -> (String, ParsedTitle) {
    let name = stream_name_from_magnet(magnet, fallback);
    (name.clone(), parse_title(&name))
}

/// Parse stream metadata, preferring magnet `dn=` found in any of `sources`.
pub fn parse_stream_name_from_sources(sources: &[&str], fallback: &str) -> (String, ParsedTitle) {
    for source in sources {
        if let Some(magnet) = extract_magnet_uri(source) {
            return parse_magnet_stream(&magnet, fallback);
        }
    }
    let name = fallback.trim().to_string();
    (name.clone(), parse_title(&name))
}

/// Title similarity ratio (0–100) — mirrors Python `calculate_max_similarity_ratio`.
///
/// Uses word-token Jaccard similarity after normalisation (lowercase, alphanumeric only).
/// Returns the highest ratio between `parsed` and any of the candidate titles.
pub fn similarity_ratio(parsed: &str, candidate: &str) -> u32 {
    jaccard(parsed, candidate)
}

/// Similarity against a main title plus optional aka titles; returns max.
pub fn max_similarity_ratio(parsed: &str, main: &str, akas: &[String]) -> u32 {
    let base = jaccard(parsed, main);
    akas.iter().map(|a| jaccard(parsed, a)).fold(base, u32::max)
}

fn normalise(s: &str) -> Vec<String> {
    s.to_lowercase()
        .split(|c: char| !c.is_alphanumeric())
        .filter(|t| !t.is_empty())
        .map(|t| t.to_string())
        .collect()
}

#[cfg(test)]
mod magnet_tests {
    use super::*;

    #[test]
    fn stream_name_prefers_magnet_dn() {
        let magnet =
            "magnet:?xt=urn:btih:abc123deadbeefabc123deadbeefabc123&dn=Movie.2026.1080p.WEB-DL";
        assert_eq!(
            stream_name_from_magnet(magnet, "Movie (2026) HDRip"),
            "Movie.2026.1080p.WEB-DL"
        );
    }

    #[test]
    fn parse_stream_name_from_sources_finds_embedded_magnet() {
        let html = r#"<a href="magnet:?xt=urn:btih:abc123deadbeefabc123deadbeefabc123&dn=Show.S01E01.1080p.WEB">link</a>"#;
        let (name, parsed) = parse_stream_name_from_sources(&[html], "Show S01E01 Generic Title");
        assert_eq!(name, "Show.S01E01.1080p.WEB");
        assert_eq!(parsed.resolution.as_deref(), Some("1080p"));
    }

    #[test]
    fn parse_magnet_stream_falls_back_without_dn() {
        let magnet = "magnet:?xt=urn:btih:abc123deadbeefabc123deadbeefabc123";
        let (name, parsed) = parse_magnet_stream(magnet, "Fallback.2026.720p.WEB-DL");
        assert_eq!(name, "Fallback.2026.720p.WEB-DL");
        assert_eq!(parsed.resolution.as_deref(), Some("720p"));
    }
}

fn jaccard(a: &str, b: &str) -> u32 {
    let ta: std::collections::HashSet<String> = normalise(a).into_iter().collect();
    let tb: std::collections::HashSet<String> = normalise(b).into_iter().collect();

    if ta.is_empty() && tb.is_empty() {
        return 100;
    }
    if ta.is_empty() || tb.is_empty() {
        return 0;
    }

    let intersection = ta.intersection(&tb).count();
    let union = ta.union(&tb).count();
    ((intersection * 100) / union) as u32
}
