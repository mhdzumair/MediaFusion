//! Metadata picker options and override resolution for the contribution wizard.

use std::sync::LazyLock;

use serde_json::{Value, json};

use crate::parser::{LANGUAGES_FILTERS, ParsedTitle, QUALITY_GROUPS, RESOLUTIONS};

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

pub static CHANNEL_OPTIONS: &[&str] = &["2.0", "5.1", "7.1", "7.1.4"];

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
        "audio_formats" => AUDIO_OPTIONS.to_vec(),
        "channels" => CHANNEL_OPTIONS.to_vec(),
        "languages" => LANGUAGE_OPTIONS.clone(),
        _ => Vec::new(),
    }
}

pub fn parsed_title_json_extras(parsed: &ParsedTitle) -> Value {
    json!({
        "languages": parsed.languages,
        "audio_formats": parsed.audio,
        "channels": parsed.channels,
    })
}

pub fn string_list_from_value(value: &Value) -> Vec<String> {
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

pub fn seed_metadata_overrides_from_analysis(overrides: &mut Value, analysis: &Value) {
    let Some(obj) = overrides.as_object_mut() else {
        return;
    };

    if !obj.contains_key("languages") {
        let langs = normalize_language_values(&analysis["languages"]);
        if !langs.is_empty() {
            obj.insert(
                "languages".to_string(),
                Value::Array(langs.iter().cloned().map(Value::String).collect()),
            );
        }
    }

    if !obj.contains_key("audio_formats") {
        let audio = string_list_from_value(&analysis["audio_formats"]);
        let audio = if audio.is_empty() {
            string_list_from_value(&analysis["audio"])
        } else {
            audio
        };
        if !audio.is_empty() {
            obj.insert(
                "audio_formats".to_string(),
                Value::Array(audio.iter().cloned().map(Value::String).collect()),
            );
        }
    }

    if !obj.contains_key("channels") {
        let channels = string_list_from_value(&analysis["channels"]);
        if !channels.is_empty() {
            obj.insert(
                "channels".to_string(),
                Value::Array(channels.iter().cloned().map(Value::String).collect()),
            );
        }
    }
}

fn string_list_from_overrides(overrides: &Value, field: &str) -> Vec<String> {
    string_list_from_value(&overrides[field])
}

fn string_list_from_analysis_or_overrides(
    analysis: &Value,
    overrides: &Value,
    override_field: &str,
    analysis_field: &str,
) -> Vec<String> {
    let override_values = string_list_from_overrides(overrides, override_field);
    if !override_values.is_empty() {
        return override_values;
    }
    let analysis_values = string_list_from_value(&analysis[analysis_field]);
    if !analysis_values.is_empty() {
        return analysis_values;
    }
    if override_field != analysis_field {
        string_list_from_value(&analysis[override_field])
    } else {
        analysis_values
    }
}

pub fn selected_audio_formats(analysis: &Value, overrides: &Value) -> Vec<String> {
    string_list_from_analysis_or_overrides(analysis, overrides, "audio_formats", "audio_formats")
}

pub fn selected_channels(analysis: &Value, overrides: &Value) -> Vec<String> {
    string_list_from_analysis_or_overrides(analysis, overrides, "channels", "channels")
}

pub fn format_audio_display(analysis: &Value, overrides: &Value) -> String {
    let mut parts = selected_audio_formats(analysis, overrides);
    for channel in selected_channels(analysis, overrides) {
        if !parts.iter().any(|part| part == &channel) {
            parts.push(channel);
        }
    }
    if parts.is_empty() {
        "Auto".to_string()
    } else {
        parts.join(", ")
    }
}

fn toggle_string_list_override(
    overrides: &mut Value,
    field: &str,
    value: &str,
    current: &[String],
) -> Vec<String> {
    let mut selected = current.to_vec();
    if let Some(pos) = selected.iter().position(|item| item == value) {
        selected.remove(pos);
    } else {
        selected.push(value.to_string());
    }
    selected.sort_unstable();
    if let Some(obj) = overrides.as_object_mut() {
        if selected.is_empty() {
            obj.remove(field);
        } else {
            obj.insert(
                field.to_string(),
                Value::Array(selected.iter().cloned().map(Value::String).collect()),
            );
        }
    }
    selected
}

pub fn toggle_audio_override(overrides: &mut Value, format: &str, analysis: &Value) -> Vec<String> {
    let current = selected_audio_formats(analysis, overrides);
    toggle_string_list_override(overrides, "audio_formats", format, &current)
}

pub fn toggle_channel_override(
    overrides: &mut Value,
    channel: &str,
    analysis: &Value,
) -> Vec<String> {
    let current = selected_channels(analysis, overrides);
    toggle_string_list_override(overrides, "channels", channel, &current)
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

pub fn toggle_language_override(
    overrides: &mut Value,
    language: &str,
    analysis: &Value,
) -> Vec<String> {
    let mut selected = selected_languages(analysis, overrides);
    if let Some(pos) = selected.iter().position(|lang| lang == language) {
        selected.remove(pos);
    } else {
        selected.push(language.to_string());
    }
    selected.sort_unstable();
    if let Some(obj) = overrides.as_object_mut() {
        if selected.is_empty() {
            obj.remove("languages");
        } else {
            obj.insert(
                "languages".to_string(),
                Value::Array(selected.iter().cloned().map(Value::String).collect()),
            );
        }
    }
    selected
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

    if episode_number.is_none()
        && let Some(episodes) = analysis.get("episodes").and_then(|v| v.as_array())
    {
        if let Some(first) = episodes.first().and_then(|v| v.as_i64()) {
            episode_number = Some(first as i32);
        }
        if episodes.len() > 1 {
            episode_end = episodes.last().and_then(|v| v.as_i64()).map(|n| n as i32);
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser;

    #[test]
    fn seed_metadata_overrides_populates_parsed_languages_and_audio() {
        let parsed = parser::parse_title(
            "Veer Dheera Sura Part 2 2025 1080p WEB-DL HIN-TAM x264 AAC 5 1 ESub",
        );
        let analysis = parsed_title_json_extras(&parsed);
        let mut overrides = json!({});
        seed_metadata_overrides_from_analysis(&mut overrides, &analysis);

        let langs = selected_languages(&analysis, &overrides);
        assert!(
            langs.iter().any(|l| l == "Hindi"),
            "expected Hindi, got {langs:?}"
        );
        assert!(
            langs.iter().any(|l| l == "Tamil"),
            "expected Tamil, got {langs:?}"
        );

        let audio = selected_audio_formats(&analysis, &overrides);
        assert!(
            audio.iter().any(|a| a == "AAC"),
            "expected AAC, got {audio:?}"
        );

        let channels = selected_channels(&analysis, &overrides);
        assert!(
            channels.iter().any(|c| c == "5.1"),
            "expected 5.1 channel, got {channels:?}"
        );

        assert_eq!(format_audio_display(&analysis, &overrides), "AAC, 5.1");
    }
}
