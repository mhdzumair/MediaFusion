use std::collections::HashSet;

use regex::Regex;
use serde_json::{json, Value};

use crate::db::types::TorrentType;
use crate::models::user_data::{SortingOption, StreamingProvider, UserData};
use crate::scrapers::torrent_metadata::{
    provider_supports_private_trackers, torrent_type_from_json_value,
};
use crate::usenet_compat::is_usenet_stream_compatible;

use super::constants::{
    normalized_hdr_filter_and_display, supported_languages, supported_qualities,
    supported_resolutions,
};
use super::contains_adult_keywords;
use super::sort::sort_and_cap_stream_rows;

pub const MAX_STREAM_NAME_FILTER_PATTERNS: usize = 10;
pub const MAX_STREAM_NAME_FILTER_PATTERN_LENGTH: usize = 120;

const DISALLOWED_STREAM_FILTER_REGEX_TOKENS: &[&str] = &["(?=", "(?!", "(?<=", "(?<"];

static DISALLOWED_BACKREF: std::sync::LazyLock<Regex> =
    std::sync::LazyLock::new(|| Regex::new(r"\\[1-9]").expect("valid regex"));

pub struct FilterContext<'a> {
    pub user_data: &'a UserData,
    pub season: Option<i32>,
    pub episode: Option<i32>,
    pub primary_provider: Option<&'a StreamingProvider>,
    pub is_usenet: bool,
    pub allow_public_usenet: bool,
}

/// Apply user nudity and certification filters to a stream list (scrape-time adult keywords only).
pub fn apply_filters(
    streams: Vec<crate::scrapers::ScrapedStream>,
    _user_data: &UserData,
) -> Vec<crate::scrapers::ScrapedStream> {
    streams
        .into_iter()
        .filter(|s| !contains_adult_keywords(&s.name))
        .collect()
}

/// Port of Python `filter_streams_by_user_preferences` — returns rows with `filtered_*` sidecars set.
pub fn filter_streams_by_preferences(streams: Vec<Value>, ctx: &FilterContext<'_>) -> Vec<Value> {
    let ud = ctx.user_data;
    let selected_resolutions = ud.effective_selected_resolutions();
    let selected_resolutions_set: HashSet<&Option<String>> = selected_resolutions.iter().collect();
    let quality_filter_set = ud.quality_filter_set();
    let hdr_filter_set = ud.hdr_filter_set();
    let language_filter_set = ud.language_filter_set();

    let name_patterns = safe_stream_name_patterns(ud);
    let use_regex = ud.stream_name_filter_use_regex;
    let name_mode = ud.stream_name_filter_mode.as_deref().unwrap_or("disabled");

    let mut working = streams;

    // Step 1: usenet pre-pass
    if ctx.is_usenet {
        if let Some(provider) = ctx.primary_provider {
            working.retain(|row| {
                is_usenet_stream_compatible(row, provider, ud, ctx.allow_public_usenet)
            });
        }
    }

    let mut out = Vec::with_capacity(working.len());

    for mut row in working {
        let torrent_type = torrent_type_from_json_value(&row);

        // Step 2: private torrent gating
        if torrent_type != TorrentType::Public {
            let Some(provider) = ctx.primary_provider else {
                continue;
            };
            if torrent_type != TorrentType::WebSeed
                && !provider_supports_private_trackers(&provider.service)
            {
                continue;
            }
        }

        // Step 3: normalize sidecars
        let raw_resolution = row
            .get("resolution")
            .and_then(|v| v.as_str())
            .map(str::to_string);
        let filtered_resolution = if raw_resolution
            .as_ref()
            .is_some_and(|r| supported_resolutions().contains(&Some(r.clone())))
        {
            raw_resolution
        } else {
            None
        };

        let raw_quality = row
            .get("quality")
            .and_then(|v| v.as_str())
            .map(str::to_string);
        let filtered_quality = if raw_quality
            .as_ref()
            .is_some_and(|q| supported_qualities().contains(&Some(q.clone())))
        {
            raw_quality
        } else {
            None
        };

        let hdr_raw = string_array_from_row(&row, "hdr_formats");
        let (filtered_hdr_formats, _display_hdr) = normalized_hdr_filter_and_display(&hdr_raw);

        let stream_languages = string_array_from_row(&row, "languages");
        let filtered_languages: Vec<Option<String>> = {
            let langs: Vec<Option<String>> = stream_languages
                .into_iter()
                .filter(|lang| supported_languages().contains(&Some(lang.clone())))
                .map(Some)
                .collect();
            if langs.is_empty() {
                vec![None]
            } else {
                langs
            }
        };

        if let Some(obj) = row.as_object_mut() {
            match &filtered_resolution {
                Some(r) => {
                    obj.insert("filtered_resolution".into(), json!(r));
                }
                None => {
                    obj.insert("filtered_resolution".into(), Value::Null);
                }
            }
            match &filtered_quality {
                Some(q) => {
                    obj.insert("filtered_quality".into(), json!(q));
                }
                None => {
                    obj.insert("filtered_quality".into(), Value::Null);
                }
            }
            obj.insert("filtered_hdr_formats".into(), json!(filtered_hdr_formats));
            obj.insert(
                "filtered_languages".into(),
                json!(filtered_languages
                    .iter()
                    .map(|l| match l {
                        Some(s) => Value::String(s.clone()),
                        None => Value::Null,
                    })
                    .collect::<Vec<_>>()),
            );
        }

        // Step 4: resolution
        if !selected_resolutions_set.contains(&filtered_resolution) {
            continue;
        }

        // Step 5–6: size
        let size = row.get("size").and_then(|v| v.as_i64()).unwrap_or(0);
        if size as f64 > ud.max_size {
            continue;
        }
        if ud.min_size > 0 && size > 0 && size < ud.min_size {
            continue;
        }

        // Step 7: quality
        if !quality_filter_set.contains(&filtered_quality) {
            continue;
        }

        // Step 8: HDR
        if !filtered_hdr_formats
            .iter()
            .any(|h| hdr_filter_set.contains(h.as_str()))
        {
            continue;
        }

        // Step 9: language
        if !filtered_languages
            .iter()
            .any(|lang| language_filter_set.contains(lang))
        {
            continue;
        }

        // Step 10: 18+
        let name = row.get("name").and_then(|v| v.as_str()).unwrap_or("");
        if contains_adult_keywords(name) {
            continue;
        }

        // Step 11: stream name filter
        if name_mode != "disabled" && !name_patterns.is_empty() {
            let matches = name_pattern_matches(name, &name_patterns, use_regex);
            if name_mode == "include" && !matches {
                continue;
            }
            if name_mode == "exclude" && matches {
                continue;
            }
        }

        out.push(row);
    }

    out
}

/// Full Python `filter_and_sort_streams` pipeline: filter → sort → cap.
pub fn filter_sort_and_cap_streams(
    streams: Vec<Value>,
    ctx: &FilterContext<'_>,
    priority: &[SortingOption],
    selected_resolutions: &[Option<String>],
    quality_filter: &[String],
    language_sorting: &[Option<String>],
    cached_hashes: &std::collections::HashMap<String, bool>,
    max_per_resolution: u32,
    max_total: u32,
) -> Vec<Value> {
    let filtered = filter_streams_by_preferences(streams, ctx);
    sort_and_cap_stream_rows(
        filtered,
        priority,
        selected_resolutions,
        quality_filter,
        language_sorting,
        cached_hashes,
        ctx.season,
        ctx.episode,
        max_per_resolution,
        max_total,
    )
}

/// Per-resolution then total cap (Python steps 4–5 of filter_and_sort_streams).
pub fn cap_streams(streams: Vec<Value>, max_per_resolution: u32, max_total: u32) -> Vec<Value> {
    let mut res_counts: std::collections::HashMap<String, u32> = std::collections::HashMap::new();
    let mut capped = Vec::new();
    for row in streams {
        let res_key = resolution_cap_key(&row);
        let count = res_counts.entry(res_key).or_insert(0);
        if *count < max_per_resolution {
            *count += 1;
            capped.push(row);
        }
    }
    capped.truncate(max_total as usize);
    capped
}

pub fn resolution_cap_key(row: &Value) -> String {
    row.get("filtered_resolution")
        .and_then(|v| v.as_str())
        .or_else(|| row.get("resolution").and_then(|v| v.as_str()))
        .unwrap_or("")
        .to_string()
}

fn string_array_from_row(row: &Value, key: &str) -> Vec<String> {
    row.get(key)
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default()
}

fn safe_stream_name_patterns(ud: &UserData) -> Vec<NamePattern> {
    let mode = ud.stream_name_filter_mode.as_deref().unwrap_or("disabled");
    if mode == "disabled" {
        return vec![];
    }

    let mut patterns = Vec::new();
    let mut seen = HashSet::new();
    for raw in ud
        .stream_name_filter_patterns
        .iter()
        .take(MAX_STREAM_NAME_FILTER_PATTERNS)
    {
        let pattern = raw.trim();
        if pattern.is_empty() || pattern.len() > MAX_STREAM_NAME_FILTER_PATTERN_LENGTH {
            continue;
        }
        if !seen.insert(pattern.to_string()) {
            continue;
        }
        if ud.stream_name_filter_use_regex {
            if DISALLOWED_STREAM_FILTER_REGEX_TOKENS
                .iter()
                .any(|t| pattern.contains(t))
            {
                continue;
            }
            if DISALLOWED_BACKREF.is_match(pattern) {
                continue;
            }
            match Regex::new(&format!("(?i){pattern}")) {
                Ok(re) => patterns.push(NamePattern::Regex(re)),
                Err(_) => continue,
            }
        } else {
            patterns.push(NamePattern::Literal(pattern.to_lowercase()));
        }
    }
    patterns
}

enum NamePattern {
    Literal(String),
    Regex(Regex),
}

fn name_pattern_matches(name: &str, patterns: &[NamePattern], use_regex: bool) -> bool {
    if use_regex {
        patterns.iter().any(|p| match p {
            NamePattern::Regex(re) => re.is_match(name),
            NamePattern::Literal(_) => false,
        })
    } else {
        let lower = name.to_lowercase();
        patterns.iter().any(|p| match p {
            NamePattern::Literal(lit) => lower.contains(lit.as_str()),
            NamePattern::Regex(_) => false,
        })
    }
}

/// Episode-aware byte size for sort (Python `_sort_size_bytes_for_stream`).
pub fn sort_size_bytes_for_row(row: &Value, season: Option<i32>, episode: Option<i32>) -> i64 {
    let total = row.get("size").and_then(|v| v.as_i64()).unwrap_or(0);
    if season.is_none() || episode.is_none() {
        return total;
    }
    if let Some(file_size) = row.get("file_size").and_then(|v| v.as_i64()) {
        if file_size > 0 {
            return file_size;
        }
    }
    total
}

/// Read filtered language list from a row after filter pass.
pub fn filtered_languages_from_row(row: &Value) -> Vec<Option<String>> {
    row.get("filtered_languages")
        .and_then(|v| v.as_array())
        .map(|arr| arr.iter().map(|v| v.as_str().map(str::to_string)).collect())
        .unwrap_or_default()
}
