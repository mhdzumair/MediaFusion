pub mod episode_detector;
pub mod filter;

use std::sync::OnceLock;

/// Parsed fields from a torrent/release title.
#[derive(Debug, Default, Clone)]
pub struct ParsedTitle {
    pub title: Option<String>,
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
}

pub fn parse_title(raw: &str) -> ParsedTitle {
    let p = crate::ptt::parse_title(raw);
    ParsedTitle {
        title: Some(p.title),
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

/// Checks whether a string contains any known adult/18+ keyword.
///
/// Keywords are loaded from the embedded PTT keyword list (combined-keywords.txt)
/// and matched as substrings of the lowercased input, mirroring the Python PTT
/// `is_adult_content` logic.
pub fn contains_adult_keywords(s: &str) -> bool {
    static ADULT_KEYWORDS: OnceLock<Vec<String>> = OnceLock::new();
    let keywords = ADULT_KEYWORDS.get_or_init(|| {
        // Lines prefixed with '!' are whitelist entries — skip them here.
        include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/resources/adult-keywords.txt"
        ))
        .lines()
        .map(|l| l.trim())
        .filter(|l| !l.is_empty() && !l.starts_with('!'))
        .map(|l| l.to_lowercase())
        .collect()
    });
    let lower = s.to_lowercase();
    keywords.iter().any(|kw| lower.contains(kw.as_str()))
}
