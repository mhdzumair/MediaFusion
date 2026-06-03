//! Metadata picker options and override resolution for the contribution wizard.

use std::sync::LazyLock;

use serde_json::Value;

use crate::parser::{LANGUAGES_FILTERS, QUALITY_GROUPS, RESOLUTIONS};

pub static AUDIO_OPTIONS: &[&str] = &[
    "AAC",
    "AC3",
    "DTS",
    "DTS-HD MA",
    "TrueHD",
    "Atmos",
    "FLAC",
    "MP3",
    "EAC3",
];

pub static CODEC_OPTIONS: &[&str] = &["x265", "HEVC", "x264", "AVC", "AV1", "VP9", "MPEG-4"];

pub static RESOLUTION_OPTIONS: LazyLock<Vec<&'static str>> =
    LazyLock::new(|| RESOLUTIONS.iter().filter_map(|r| *r).collect());

pub static QUALITY_OPTIONS: LazyLock<Vec<&'static str>> = LazyLock::new(|| {
    QUALITY_GROUPS
        .iter()
        .flat_map(|(_, qualities)| qualities.iter().copied())
        .collect()
});

pub static LANGUAGE_OPTIONS: LazyLock<Vec<&'static str>> =
    LazyLock::new(|| LANGUAGES_FILTERS.iter().filter_map(|lang| *lang).collect());

pub fn field_options(field: &str) -> Vec<&'static str> {
    match field {
        "resolution" => RESOLUTION_OPTIONS.clone(),
        "quality" => QUALITY_OPTIONS.clone(),
        "codec" => CODEC_OPTIONS.to_vec(),
        "audio" => AUDIO_OPTIONS.to_vec(),
        "languages" => LANGUAGE_OPTIONS.clone(),
        _ => Vec::new(),
    }
}

pub fn normalize_language_values(value: &Value) -> Vec<String> {
    match value {
        Value::String(s) => s
            .split(',')
            .map(str::trim)
            .filter(|part| !part.is_empty() && !part.eq_ignore_ascii_case("auto"))
            .map(str::to_string)
            .collect(),
        Value::Array(items) => items
            .iter()
            .filter_map(|v| v.as_str())
            .map(str::trim)
            .filter(|part| !part.is_empty() && !part.eq_ignore_ascii_case("auto"))
            .map(str::to_string)
            .collect(),
        _ => Vec::new(),
    }
}

pub fn selected_languages(analysis: &Value, overrides: &Value) -> Vec<String> {
    let override_langs = normalize_language_values(&overrides["languages"]);
    if !override_langs.is_empty() {
        return override_langs;
    }
    normalize_language_values(&analysis["languages"])
}

pub fn metadata_value<'a>(field: &str, analysis: &'a Value, overrides: &'a Value) -> String {
    overrides
        .get(field)
        .or_else(|| analysis.get(field))
        .and_then(|v| {
            if v.is_string() {
                v.as_str().map(str::to_string)
            } else if v.is_i64() {
                Some(v.as_i64().unwrap_or_default().to_string())
            } else {
                None
            }
        })
        .unwrap_or_else(|| "Auto".to_string())
}

pub fn episode_info(
    analysis: &Value,
    overrides: &Value,
) -> (Option<i32>, Option<i32>, Option<i32>) {
    let season_number = overrides
        .get("season_number")
        .and_then(|v| v.as_i64())
        .map(|n| n as i32)
        .or_else(|| {
            analysis
                .get("seasons")
                .and_then(|v| v.as_array())
                .and_then(|seasons| seasons.first())
                .and_then(|v| v.as_i64())
                .map(|n| n as i32)
        });

    let mut episode_number = overrides
        .get("episode_number")
        .and_then(|v| v.as_i64())
        .map(|n| n as i32);
    let mut episode_end = None;

    if episode_number.is_none() {
        if let Some(episodes) = analysis.get("episodes").and_then(|v| v.as_array()) {
            if let Some(first) = episodes.first().and_then(|v| v.as_i64()) {
                episode_number = Some(first as i32);
            }
            if episodes.len() > 1 {
                episode_end = episodes.last().and_then(|v| v.as_i64()).map(|n| n as i32);
            }
        }
    }

    (season_number, episode_number, episode_end)
}

pub fn is_valid_poster_url(url: &str) -> bool {
    let lower = url.to_lowercase();
    if !url.starts_with("http") {
        return false;
    }
    const EXT: &[&str] = &[".jpg", ".jpeg", ".png", ".webp", ".gif"];
    EXT.iter()
        .any(|ext| lower.ends_with(ext) || lower.contains(ext))
        || lower.contains("image")
        || lower.contains("/photo/")
        || lower.contains("imgur")
        || lower.contains("postimg")
}
