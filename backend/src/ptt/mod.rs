pub mod clean;
pub mod engine;
pub mod handlers;
pub mod transformers;

use std::collections::HashMap;
use std::sync::OnceLock;

use engine::{FieldValue, Parser};
use regex::Regex;

use serde::{Deserialize, Serialize};

// ── Public output struct ──────────────────────────────────────────────────────

#[derive(Debug, Default, Clone, Serialize, Deserialize)]
pub struct ParsedTitle {
    pub title: String,
    /// Per-episode title extracted from the region between the `SxxExx` marker
    /// and the first release-metadata token (resolution, quality, codec …).
    /// Only populated for filenames that contain a season/episode marker.
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
    pub date: Option<String>,
    pub bit_depth: Option<String>,
    pub edition: Option<String>,
    pub network: Option<String>,
    pub site: Option<String>,
    pub size: Option<String>,
    pub group: Option<String>,
    pub country: Option<String>,
    pub container: Option<String>,
    pub extension: Option<String>,
    pub bitrate: Option<String>,
    pub is_3d: bool,
    pub is_complete: bool,
    pub is_proper: bool,
    pub is_repack: bool,
    pub is_retail: bool,
    pub is_remastered: bool,
    pub is_unrated: bool,
    pub is_uncensored: bool,
    pub is_documentary: bool,
    pub is_upscaled: bool,
    pub is_hardcoded: bool,
    pub is_trash: bool,
    pub is_scene: bool,
    pub is_adult: bool,
    pub is_dubbed: bool,
    pub is_subbed: bool,
    pub is_ppv: bool,
    pub extras: Vec<String>,
}

// ── Language translation table ────────────────────────────────────────────────

static LANG_TABLE: &[(&str, &str)] = &[
    ("en", "English"),
    ("ja", "Japanese"),
    ("zh", "Chinese"),
    ("ru", "Russian"),
    ("ar", "Arabic"),
    ("pt", "Portuguese"),
    ("es", "Spanish"),
    ("fr", "French"),
    ("de", "German"),
    ("it", "Italian"),
    ("ko", "Korean"),
    ("hi", "Hindi"),
    ("bn", "Bengali"),
    ("pa", "Punjabi"),
    ("mr", "Marathi"),
    ("gu", "Gujarati"),
    ("ta", "Tamil"),
    ("te", "Telugu"),
    ("kn", "Kannada"),
    ("ml", "Malayalam"),
    ("th", "Thai"),
    ("vi", "Vietnamese"),
    ("id", "Indonesian"),
    ("tr", "Turkish"),
    ("he", "Hebrew"),
    ("fa", "Persian"),
    ("uk", "Ukrainian"),
    ("el", "Greek"),
    ("lt", "Lithuanian"),
    ("lv", "Latvian"),
    ("et", "Estonian"),
    ("pl", "Polish"),
    ("cs", "Czech"),
    ("sk", "Slovak"),
    ("hu", "Hungarian"),
    ("ro", "Romanian"),
    ("bg", "Bulgarian"),
    ("sr", "Serbian"),
    ("hr", "Croatian"),
    ("sl", "Slovenian"),
    ("nl", "Dutch"),
    ("da", "Danish"),
    ("fi", "Finnish"),
    ("sv", "Swedish"),
    ("no", "Norwegian"),
    ("ms", "Malay"),
    ("la", "Latino"),
];

fn translate_lang(code: &str) -> Option<&'static str> {
    LANG_TABLE.iter().find(|(k, _)| *k == code).map(|(_, v)| *v)
}

// ── Episode-title extraction ──────────────────────────────────────────────────

/// Matches a `SxxExx` / `S1E4` marker (case-insensitive).
fn re_sxxexx() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"(?i)[sS](\d{1,2})[eE](\d{1,2})").unwrap())
}

/// Matches the first release-metadata token that reliably ends the episode-title
/// region.  Uses a leading separator anchor (`^` or `[._\s-]`) so `m.start()`
/// is the byte-offset of the separator, giving a clean cut-point.
fn re_episode_title_cutoff() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        Regex::new(
            r"(?i)(?:^|[._\s-])(?:4k|uhd|2160p|1440p|1080[ip]|720p|576p|480p|360p|240p\
|web[.-]?dl|webrip|blu[.-]?ray|bdrip|brrip|hdrip|hdtv|dvdrip|dvd|pdtv|satrip|tvrip\
|cam(?:rip)?\b|telecine|telesync|scr\b|remux|webmux\
|[hx][._]?26[45]|hevc|avc|xvid|divx\
|aac|ac3|mp3|dts|truehd|flac|dolby|atmos\
|multi|dual|proper|repack|extended|remastered|limited|complete|internal|subbed|dubbed)",
        )
        .unwrap()
    })
}

/// Normalise a title for comparison: dots/underscores → spaces, collapse runs,
/// lowercase.
fn norm_title(s: &str) -> String {
    s.chars()
        .map(|c| if matches!(c, '.' | '_') { ' ' } else { c })
        .collect::<String>()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_lowercase()
}

/// Extract the per-episode title from a raw filename string.
///
/// Looks for text between the `SxxExx` marker and the first release-metadata
/// token.  Returns `None` when:
/// - no `SxxExx` marker is found,
/// - the extracted text is empty after cleaning, or
/// - it normalises to the same string as `show_title` (i.e. it is the series
///   name, not a per-episode title — which is what PTT's `title` returns for
///   these filenames).
fn extract_episode_title(raw: &str, show_title: &str) -> Option<String> {
    // Work on the base name without extension.
    let stem = std::path::Path::new(raw)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or(raw);

    let cap = re_sxxexx().find(stem)?;
    let after = &stem[cap.end()..];
    let after = after.trim_start_matches(['.', '_', ' ', '-']);

    let end = re_episode_title_cutoff()
        .find(after)
        .map(|m| m.start())
        .unwrap_or(after.len());

    let raw_slice = after[..end].trim_end_matches(['.', '_', ' ', '-']);

    let title: String = raw_slice
        .chars()
        .map(|c| if matches!(c, '.' | '_') { ' ' } else { c })
        .collect::<String>()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");

    if title.is_empty() || norm_title(&title) == norm_title(show_title) {
        return None;
    }

    Some(title)
}

// ── Singleton parser (compiled once at startup) ───────────────────────────────

fn global_parser() -> &'static Parser {
    static PARSER: OnceLock<Parser> = OnceLock::new();
    PARSER.get_or_init(|| {
        let mut p = Parser::new();
        handlers::add_defaults(&mut p);
        p
    })
}

// ── Public API ────────────────────────────────────────────────────────────────

/// Parse a torrent/release title.
///
/// If `translate_languages` is `true`, ISO 639-1 codes are replaced with full
/// language names (e.g. `"en"` → `"English"`).
pub fn parse(raw: &str, translate_languages: bool) -> ParsedTitle {
    let map = global_parser().parse_raw(raw);
    let mut out = from_map(map, translate_languages);
    // Populate episode_title when a season/episode marker is present.
    if !out.seasons.is_empty() && !out.episodes.is_empty() {
        out.episode_title = extract_episode_title(raw, &out.title);
    }
    out
}

/// Parse with default options (language codes, not full names).
pub fn parse_title(raw: &str) -> ParsedTitle {
    parse(raw, false)
}

// ── Internal: convert raw HashMap to typed ParsedTitle ────────────────────────

fn from_map(mut m: HashMap<String, FieldValue>, translate: bool) -> ParsedTitle {
    let mut out = ParsedTitle::default();

    if let Some(FieldValue::Str(s)) = m.remove("title") {
        out.title = s;
    }
    if let Some(FieldValue::Int(n)) = m.remove("year") {
        out.year = Some(n);
    }

    if let Some(FieldValue::Str(s)) = m.remove("resolution") {
        out.resolution = Some(s);
    }
    if let Some(FieldValue::Str(s)) = m.remove("quality") {
        out.quality = Some(s);
    }
    if let Some(FieldValue::Str(s)) = m.remove("codec") {
        out.codec = Some(s);
    }
    if let Some(FieldValue::Str(s)) = m.remove("date") {
        out.date = Some(s);
    }
    if let Some(FieldValue::Str(s)) = m.remove("bit_depth") {
        out.bit_depth = Some(s);
    }
    if let Some(FieldValue::Str(s)) = m.remove("edition") {
        out.edition = Some(s);
    }
    if let Some(FieldValue::Str(s)) = m.remove("network") {
        out.network = Some(s);
    }
    if let Some(FieldValue::Str(s)) = m.remove("site") {
        out.site = Some(s);
    }
    if let Some(FieldValue::Str(s)) = m.remove("size") {
        out.size = Some(s);
    }
    if let Some(FieldValue::Str(s)) = m.remove("group") {
        out.group = Some(s);
    }
    if let Some(FieldValue::Str(s)) = m.remove("country") {
        out.country = Some(s);
    }
    if let Some(FieldValue::Str(s)) = m.remove("container") {
        out.container = Some(s);
    }
    if let Some(FieldValue::Str(s)) = m.remove("extension") {
        out.extension = Some(s);
    }
    if let Some(FieldValue::Str(s)) = m.remove("bitrate") {
        out.bitrate = Some(s);
    }

    out.audio = strs(m.remove("audio"));
    out.channels = strs(m.remove("channels"));
    out.hdr = strs(m.remove("hdr"));
    out.extras = strs(m.remove("extras"));

    let mut langs = strs(m.remove("languages"));
    if translate {
        langs = langs
            .iter()
            .map(|c| translate_lang(c).unwrap_or(c.as_str()).to_string())
            .collect();
    }
    out.languages = langs;

    out.seasons = ints(m.remove("seasons"));
    out.episodes = ints(m.remove("episodes"));

    out.is_3d = bool_val(m.remove("3d"));
    out.is_complete = bool_val(m.remove("complete"));
    out.is_proper = bool_val(m.remove("proper"));
    out.is_repack = bool_val(m.remove("repack"));
    out.is_retail = bool_val(m.remove("retail"));
    out.is_remastered = bool_val(m.remove("remastered"));
    out.is_unrated = bool_val(m.remove("unrated"));
    out.is_uncensored = bool_val(m.remove("uncensored"));
    out.is_documentary = bool_val(m.remove("documentary"));
    out.is_upscaled = bool_val(m.remove("upscaled"));
    out.is_hardcoded = bool_val(m.remove("hardcoded"));
    out.is_trash = bool_val(m.remove("trash"));
    out.is_scene = bool_val(m.remove("scene"));
    out.is_adult = bool_val(m.remove("adult"));
    out.is_dubbed = bool_val(m.remove("dubbed"));
    out.is_subbed = bool_val(m.remove("subbed"));
    out.is_ppv = bool_val(m.remove("ppv"));

    out
}

fn strs(v: Option<FieldValue>) -> Vec<String> {
    match v {
        Some(FieldValue::Strs(s)) => s,
        Some(FieldValue::Str(s)) => vec![s],
        _ => vec![],
    }
}

fn ints(v: Option<FieldValue>) -> Vec<i32> {
    match v {
        Some(FieldValue::Ints(i)) => i,
        Some(FieldValue::Int(i)) => vec![i],
        _ => vec![],
    }
}

fn bool_val(v: Option<FieldValue>) -> bool {
    matches!(v, Some(FieldValue::Bool(true)))
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_basic_movie() {
        let r = parse_title("Inception.2010.1080p.BluRay.x264-GROUP");
        assert_eq!(r.title, "Inception");
        assert_eq!(r.year, Some(2010));
        assert_eq!(r.resolution.as_deref(), Some("1080p"));
        assert_eq!(r.quality.as_deref(), Some("BluRay"));
        assert_eq!(r.codec.as_deref(), Some("avc"));
        assert_eq!(r.group.as_deref(), Some("GROUP"));
    }

    #[test]
    fn test_series_episode() {
        let r = parse_title("The.Simpsons.S05E10.720p.WEB-DL");
        assert_eq!(r.seasons, vec![5]);
        assert_eq!(r.episodes, vec![10]);
        assert_eq!(r.resolution.as_deref(), Some("720p"));
        assert_eq!(r.quality.as_deref(), Some("WEB-DL"));
        // No episode title text between marker and first release token
        assert!(r.episode_title.is_none());
    }

    #[test]
    fn test_episode_title_extracted() {
        let r = parse_title(
            "Brothers.and.Sisters.S01E01.The.House.of.SS.2160p.JHS.WEB-DL.MULTi.AAC2.0.H.265-4kHdHub.Com.mkv",
        );
        assert_eq!(r.title, "Brothers and Sisters");
        assert_eq!(r.seasons, vec![1]);
        assert_eq!(r.episodes, vec![1]);
        assert_eq!(r.episode_title.as_deref(), Some("The House of SS"));
    }

    #[test]
    fn test_episode_title_e02() {
        let r = parse_title(
            "Brothers.and.Sisters.S01E02.Jayshrees.Second.Chance.2160p.JHS.WEB-DL.MULTi.AAC2.0.H.265-4kHdHub.Com.mkv",
        );
        assert_eq!(r.episode_title.as_deref(), Some("Jayshrees Second Chance"));
    }

    #[test]
    fn test_episode_title_e03() {
        let r = parse_title(
            "Brothers.and.Sisters.S01E03.Priyan.Guides.Jayshree.2160p.JHS.WEB-DL.MULTi.AAC2.0.H.265-4kHdHub.Com.mkv",
        );
        assert_eq!(r.episode_title.as_deref(), Some("Priyan Guides Jayshree"));
    }

    #[test]
    fn test_episode_title_e04() {
        let r = parse_title(
            "Brothers.and.Sisters.S01E04.Harini.vs.Jayshrees.Secret.2160p.JHS.WEB-DL.MULTi.AAC2.0.H.265-4kHdHub.Com.mkv",
        );
        assert_eq!(
            r.episode_title.as_deref(),
            Some("Harini vs Jayshrees Secret")
        );
    }

    #[test]
    fn test_episode_title_absent_when_no_sxxexx() {
        let r = parse_title("Some.Movie.2023.1080p.WEB-DL.mkv");
        assert!(r.episode_title.is_none());
    }

    #[test]
    fn test_hdr_audio() {
        let r = parse_title("Movie.2023.2160p.BluRay.DV.HDR10.DTS-HD.MA.5.1.HEVC-GROUP");
        assert_eq!(r.resolution.as_deref(), Some("2160p"));
        assert!(r.hdr.contains(&"DV".to_string()));
        assert!(r.hdr.contains(&"HDR10+".to_string()) || r.hdr.contains(&"HDR".to_string()));
        assert!(r.audio.contains(&"DTS Lossless".to_string()));
        assert_eq!(r.codec.as_deref(), Some("hevc"));
    }

    #[test]
    fn test_translate_languages() {
        let r = parse("Movie.2020.1080p.English.French.Spanish.mkv", true);
        assert!(r.languages.contains(&"English".to_string()));
        assert!(r.languages.contains(&"French".to_string()));
        assert!(r.languages.contains(&"Spanish".to_string()));
    }

    #[test]
    fn test_ukrainian_ukr_tag() {
        let r = parse("Example.2021.1080p.BluRay.2xUkr.Eng.mkv", true);
        assert_eq!(r.languages.len(), 2);
        assert!(r.languages.contains(&"Ukrainian".to_string()));
        assert!(r.languages.contains(&"English".to_string()));
    }
}
