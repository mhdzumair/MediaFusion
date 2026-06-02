//! Ported from `python-deprecated/utils/const.py` — single source for stream filter/sort constants.

use std::collections::{HashMap, HashSet};
use std::sync::LazyLock;

/// Maps Python's `const.QUALITY_GROUPS` — group name → member quality strings.
pub static QUALITY_GROUPS: &[(&str, &[&str])] = &[
    (
        "BluRay/UHD",
        &[
            "BluRay",
            "BluRay REMUX",
            "BRRip",
            "BDRip",
            "UHDRip",
            "REMUX",
            "BLURAY",
        ],
    ),
    (
        "WEB/HD",
        &["WEB-DL", "WEB-DLRip", "WEBRip", "HDRip", "WEBMux"],
    ),
    (
        "DVD/TV/SAT",
        &["DVD", "DVDRip", "HDTV", "SATRip", "TVRip", "PPVRip", "PDTV"],
    ),
    ("CAM/Screener", &["CAM", "TeleSync", "TeleCine", "SCR"]),
    ("Unknown", &[]),
];

pub static RESOLUTIONS: &[Option<&str>] = &[
    Some("4k"),
    Some("2160p"),
    Some("1440p"),
    Some("1080p"),
    Some("720p"),
    Some("576p"),
    Some("480p"),
    Some("360p"),
    Some("240p"),
    None,
];

pub static LANGUAGES_FILTERS: &[Option<&str>] = &[
    Some("English"),
    Some("Tamil"),
    Some("Hindi"),
    Some("Malayalam"),
    Some("Kannada"),
    Some("Telugu"),
    Some("Chinese"),
    Some("Russian"),
    Some("Arabic"),
    Some("Japanese"),
    Some("Korean"),
    Some("Taiwanese"),
    Some("Latino"),
    Some("French"),
    Some("Spanish"),
    Some("Portuguese"),
    Some("Italian"),
    Some("German"),
    Some("Ukrainian"),
    Some("Polish"),
    Some("Czech"),
    Some("Thai"),
    Some("Indonesian"),
    Some("Vietnamese"),
    Some("Dutch"),
    Some("Bengali"),
    Some("Turkish"),
    Some("Greek"),
    Some("Swedish"),
    Some("Romanian"),
    Some("Hungarian"),
    Some("Finnish"),
    Some("Norwegian"),
    Some("Danish"),
    Some("Hebrew"),
    Some("Lithuanian"),
    Some("Punjabi"),
    Some("Marathi"),
    Some("Gujarati"),
    Some("Bhojpuri"),
    Some("Nepali"),
    Some("Urdu"),
    Some("Tagalog"),
    Some("Filipino"),
    Some("Malay"),
    Some("Mongolian"),
    Some("Armenian"),
    Some("Georgian"),
    None,
];

pub static HDR_FORMATS_FILTERS: &[&str] =
    &["HDR10", "HDR10+", "Dolby Vision", "HLG", "SDR", "Unknown"];

pub static HDR_FORMAT_ALIASES: &[(&str, &str)] = &[
    ("hdr10", "HDR10"),
    ("hdr 10", "HDR10"),
    ("hdr-10", "HDR10"),
    ("hdr10+", "HDR10+"),
    ("hdr10plus", "HDR10+"),
    ("hdr 10+", "HDR10+"),
    ("dv", "Dolby Vision"),
    ("dovi", "Dolby Vision"),
    ("dovi.", "Dolby Vision"),
    ("dolby vision", "Dolby Vision"),
    ("dolby-vision", "Dolby Vision"),
    ("hlg", "HLG"),
    ("sdr", "SDR"),
    ("hdr", "HDR10"),
    ("uhdr", "HDR10"),
];

static SUPPORTED_RESOLUTIONS: LazyLock<HashSet<Option<String>>> = LazyLock::new(|| {
    RESOLUTIONS
        .iter()
        .map(|r| r.map(|s| s.to_string()))
        .collect()
});

static SUPPORTED_QUALITIES: LazyLock<HashSet<Option<String>>> = LazyLock::new(|| {
    QUALITY_GROUPS
        .iter()
        .flat_map(|(_, members)| {
            if members.is_empty() {
                vec![None]
            } else {
                members.iter().map(|q| Some((*q).to_string())).collect()
            }
        })
        .collect()
});

static SUPPORTED_LANGUAGES: LazyLock<HashSet<Option<String>>> = LazyLock::new(|| {
    LANGUAGES_FILTERS
        .iter()
        .map(|l| l.map(|s| s.to_string()))
        .collect()
});

static SUPPORTED_HDR_FORMATS: LazyLock<HashSet<&'static str>> =
    LazyLock::new(|| HDR_FORMATS_FILTERS.iter().copied().collect());

static HDR_ALIAS_MAP: LazyLock<HashMap<String, &'static str>> = LazyLock::new(|| {
    HDR_FORMAT_ALIASES
        .iter()
        .map(|(k, v)| (k.to_string(), *v))
        .collect()
});

pub fn supported_resolutions() -> &'static HashSet<Option<String>> {
    &SUPPORTED_RESOLUTIONS
}

pub fn supported_qualities() -> &'static HashSet<Option<String>> {
    &SUPPORTED_QUALITIES
}

pub fn supported_languages() -> &'static HashSet<Option<String>> {
    &SUPPORTED_LANGUAGES
}

pub fn supported_hdr_formats() -> &'static HashSet<&'static str> {
    &SUPPORTED_HDR_FORMATS
}

pub fn default_resolutions_vec() -> Vec<Option<String>> {
    RESOLUTIONS
        .iter()
        .map(|r| r.map(|s| s.to_string()))
        .collect()
}

pub fn default_language_sorting_values() -> Vec<serde_json::Value> {
    LANGUAGES_FILTERS
        .iter()
        .map(|l| match l {
            Some(s) => serde_json::Value::String(s.to_string()),
            None => serde_json::Value::Null,
        })
        .collect()
}

pub fn default_hdr_filter_vec() -> Vec<String> {
    HDR_FORMATS_FILTERS
        .iter()
        .map(|s| (*s).to_string())
        .collect()
}

pub fn default_quality_filter_groups() -> Vec<String> {
    QUALITY_GROUPS
        .iter()
        .map(|(name, _)| (*name).to_string())
        .collect()
}

pub fn expand_quality_filter(groups: &[String]) -> HashSet<Option<String>> {
    let mut out = HashSet::new();
    for group in groups {
        if let Some((_, members)) = QUALITY_GROUPS.iter().find(|(n, _)| *n == group.as_str()) {
            if members.is_empty() {
                out.insert(None);
            } else {
                for q in *members {
                    out.insert(Some(q.to_string()));
                }
            }
        }
    }
    out
}

fn hdr_normalize_token_key(token: &str) -> String {
    token
        .trim()
        .to_lowercase()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}

fn resolve_hdr_token_to_filter_value(token: &str) -> Option<String> {
    let stripped = token.trim();
    if stripped.is_empty() {
        return None;
    }
    let key = hdr_normalize_token_key(stripped);
    if key == "unknown" {
        return Some("Unknown".to_string());
    }
    if let Some(&mapped) = HDR_ALIAS_MAP.get(&key) {
        return Some(mapped.to_string());
    }
    if SUPPORTED_HDR_FORMATS.contains(stripped) {
        return Some(stripped.to_string());
    }
    None
}

/// Port of Python `normalized_hdr_filter_and_display`.
pub fn normalized_hdr_filter_and_display(raw_formats: &[String]) -> (Vec<String>, Vec<String>) {
    let raw: Vec<String> = raw_formats
        .iter()
        .map(|t| t.trim().to_string())
        .filter(|t| !t.is_empty())
        .collect();

    if raw.is_empty() {
        return (vec!["Unknown".to_string()], vec![]);
    }

    let mut mapped_values = Vec::new();
    let mut unmapped = false;
    for token in &raw {
        match resolve_hdr_token_to_filter_value(token) {
            Some(v) => mapped_values.push(v),
            None => unmapped = true,
        }
    }

    let mut seen = HashSet::new();
    let mut filter_formats = Vec::new();
    for value in mapped_values {
        if seen.insert(value.clone()) {
            filter_formats.push(value);
        }
    }

    if unmapped && !filter_formats.iter().any(|h| h == "Unknown") {
        filter_formats.push("Unknown".to_string());
    }

    let display_formats: Vec<String> = filter_formats
        .iter()
        .filter(|h| h.as_str() != "Unknown")
        .cloned()
        .collect();

    (filter_formats, display_formats)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hdr_empty_is_unknown_not_sdr() {
        let (filter, display) = normalized_hdr_filter_and_display(&[]);
        assert_eq!(filter, vec!["Unknown"]);
        assert!(display.is_empty());
    }

    #[test]
    fn hdr_alias_dv() {
        let (filter, _) = normalized_hdr_filter_and_display(&["DV".to_string()]);
        assert!(filter.contains(&"Dolby Vision".to_string()));
    }
}
