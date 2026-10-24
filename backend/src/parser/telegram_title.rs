//! Decide whether a Telegram upload has enough evidence for a title-only stub.
//! This is deliberately not a language/dictionary or adult-performer blacklist.
//! Candidates that fail this check can still be accepted by metadata providers.
use std::sync::LazyLock;

use regex::Regex;

use super::ParsedTitle;

static UPLOAD_MARKERS: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)(?:^|[^\p{L}])(?:[bw]gvid|redbedvid|vid\d*|tw\d+(?:vid)?|ppwks|scene\d*mpeg\w*|jd[ ._-]+stream)(?:$|[^\p{L}])").unwrap()
});
static LONG_NUMBER: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\d{6,}").unwrap());
static HASH_NAME: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)(?:^|[^a-z0-9])[a-f0-9]{24,}(?:$|[^a-z0-9])").unwrap());

/// None means a plausible release can be retained without provider metadata.
/// Some(reason) means a provider match is required, never an unconditional ban.
pub fn unresolved_rejection_reason(filename: &str, parsed: &ParsedTitle) -> Option<&'static str> {
    let title = parsed.title.as_deref().unwrap_or("").trim();
    if title.is_empty() {
        return Some("missing title");
    }
    if UPLOAD_MARKERS.is_match(filename) {
        return Some("upload/scene identifier");
    }
    if LONG_NUMBER.is_match(title) || HASH_NAME.is_match(filename) {
        return Some("opaque numeric/hash identifier");
    }
    let words: Vec<_> = title
        .split(|c: char| !c.is_alphanumeric())
        .filter(|s| !s.is_empty())
        .collect();
    if words.is_empty()
        || words.iter().all(|s| {
            matches!(
                s.to_ascii_lowercase().as_str(),
                "video" | "file" | "clip" | "movie" | "download" | "document" | "untitled"
            ) || s.chars().all(|c| c.is_ascii_digit())
        }) && words.iter().any(|s| s.chars().any(char::is_alphabetic))
    {
        return Some("generic upload name");
    }
    // A date is not a release year: the long-number check above takes priority.
    // Single-word, short, numeric and non-Latin titles are allowed with evidence.
    let has_release_evidence = parsed.year.is_some_and(|y| (1800..=2100).contains(&y))
        || !parsed.episodes.is_empty()
        || !parsed.seasons.is_empty()
        || parsed.resolution.is_some()
        || parsed.quality.is_some()
        || parsed.codec.is_some();
    if !has_release_evidence {
        return Some("no year, episode or video release details");
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::parse_title;

    #[test]
    fn requires_metadata_for_screenshot_upload_names() {
        for name in [
            "ashleygrahampb050813-bgvid124.mp4",
            "ashleygrahamts082413-v03 999.mp4",
            "ashlynmolloyts112313-v02 044.mp4",
            "aubreystarrh070314-tw502vid999.mp4",
            "blanchebradburyjb060813-redbedvid999.mp4",
            "breabennettcd032213-vid1999.mp4",
            "breabennetthi983-110993 full.mp4",
            "brettrossinataliasstarhr091812-wgpvid.mp4",
            "brooklittleit012513-vid2064.mp4",
            "sgs kiki minaj ap042513.mp4",
            "Scene4MPEGhii.mp4",
            "KrystalS PPWKS.mp4",
            "cherry torn 1 JD stream.mp4",
            "TeensLikeItBig - Aimee Black.mp4",
            "Deja Chan.mp4",
            "Kassin.mp4",
            "nnr.mp4",
            "video_12345.mp4",
            "movie.2024.1080p.mp4",
            "d41d8cd98f00b204e9800998ecf8427e.1080p.mkv",
        ] {
            let parsed = parse_title(name);
            assert!(
                unresolved_rejection_reason(name, &parsed).is_some(),
                "{name}: {parsed:?}"
            );
        }
    }

    #[test]
    fn preserves_release_evidence_without_dictionary_or_language_requirements() {
        for name in [
            "Unlisted Local Film (2024).mkv",
            "Unlisted.Local.Film.1080p.WEB-DL.mkv",
            "Unlisted Show S01E02.mkv",
            "IT.2017.1080p.mkv",
            "1917.2019.1080p.mkv",
            "3.2012.720p.mkv",
            "Pi.1998.mkv",
            "Amélie.2001.mkv",
            "東京物語.1953.1080p.mkv",
            "മഞ്ഞുമ്മൽ ബോയ്സ് (2024).mkv",
            "සිංහල චිත්‍රපටය (2024).mkv",
            "Dark.S01E01.mkv",
            "Big.Brother.Australia.S03E01.720p.mkv",
            "Videodrome.1983.1080p.mkv",
            "Unknown.2011.1080p.mkv",
        ] {
            let parsed = parse_title(name);
            assert_eq!(
                unresolved_rejection_reason(name, &parsed),
                None,
                "{name}: {parsed:?}"
            );
        }
    }
}
