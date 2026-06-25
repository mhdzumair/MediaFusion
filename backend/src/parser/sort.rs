use std::collections::HashMap;

use serde_json::Value;

use crate::models::user_data::SortingOption;

use super::constants::QUALITY_GROUPS;
use super::filter::{cap_streams, sort_size_bytes_for_row};

pub fn quality_rank(quality: Option<&str>, quality_filter: &[String]) -> f64 {
    let q = quality.unwrap_or("");
    if let Some(idx) = quality_filter.iter().position(|qf| qf == q) {
        return idx as f64;
    }
    for (idx, group_name) in quality_filter.iter().enumerate() {
        if let Some((_, members)) = QUALITY_GROUPS
            .iter()
            .find(|(g, _)| *g == group_name.as_str())
            && members.contains(&q) {
                return idx as f64;
            }
    }
    quality_filter.len() as f64
}

fn json_resolution_for_sort(t: &Value) -> Option<&str> {
    t.get("filtered_resolution")
        .and_then(|v| v.as_str())
        .or_else(|| t.get("resolution").and_then(|v| v.as_str()))
}

fn json_quality_for_sort(t: &Value) -> Option<&str> {
    t.get("filtered_quality")
        .and_then(|v| v.as_str())
        .or_else(|| t.get("quality").and_then(|v| v.as_str()))
}

pub fn parse_created_at_ts(v: &Value) -> f64 {
    if let Some(s) = v.as_str() {
        if let Ok(dt) = chrono::DateTime::parse_from_rfc3339(s) {
            return dt.timestamp() as f64;
        }
        let padded = if s.ends_with("+00") || s.ends_with("-00") {
            format!("{s}:00")
        } else {
            s.to_string()
        };
        if let Ok(dt) = chrono::DateTime::parse_from_rfc3339(&padded) {
            return dt.timestamp() as f64;
        }
    } else if let Some(n) = v.as_f64() {
        return n;
    }
    f64::NEG_INFINITY
}

pub fn torrent_sort_key(
    t: &Value,
    priority: &[SortingOption],
    selected_resolutions: &[Option<String>],
    quality_filter: &[String],
    language_sorting: &[Option<String>],
    cached_hashes: &HashMap<String, bool>,
    season: Option<i32>,
    episode: Option<i32>,
) -> Vec<f64> {
    let language_filter_set: std::collections::HashSet<&Option<String>> =
        language_sorting.iter().collect();

    priority
        .iter()
        .map(|opt| {
            let mult = if opt.direction == "asc" {
                1.0_f64
            } else {
                -1.0_f64
            };
            match opt.key.as_str() {
                "cached" => {
                    let is_cached = t
                        .get("info_hash")
                        .and_then(|v| v.as_str())
                        .filter(|h| !h.is_empty())
                        .map(|h| cached_hashes.get(h).copied().unwrap_or(false))
                        .unwrap_or_else(|| {
                            t.get("cached").and_then(|v| v.as_bool()).unwrap_or(false)
                        });
                    mult * if is_cached { 1.0 } else { 0.0 }
                }
                "resolution" => {
                    let res = json_resolution_for_sort(t);
                    let rank = selected_resolutions
                        .iter()
                        .position(|r| r.as_deref() == res)
                        .unwrap_or(selected_resolutions.len())
                        as f64;
                    mult * -rank
                }
                "quality" => {
                    let quality = json_quality_for_sort(t);
                    mult * -quality_rank(quality, quality_filter)
                }
                "size" => mult * sort_size_bytes_for_row(t, season, episode) as f64,
                "seeders" => mult * t.get("seeders").and_then(|v| v.as_i64()).unwrap_or(0) as f64,
                "created_at" => {
                    let ts = t
                        .get("created_at")
                        .map(parse_created_at_ts)
                        .unwrap_or(f64::NEG_INFINITY);
                    mult * ts
                }
                "language" => {
                    let filtered_langs: Vec<Option<String>> = t
                        .get("filtered_languages")
                        .and_then(|v| v.as_array())
                        .map(|arr| arr.iter().map(|v| v.as_str().map(str::to_string)).collect())
                        .unwrap_or_default();
                    let min_idx = language_sorting
                        .iter()
                        .enumerate()
                        .filter_map(|(i, lang)| {
                            if filtered_langs.contains(lang) && language_filter_set.contains(lang) {
                                Some(i as f64)
                            } else {
                                None
                            }
                        })
                        .fold(language_sorting.len() as f64, f64::min);
                    mult * -min_idx
                }
                _ => 0.0,
            }
        })
        .collect()
}

pub fn compare_sort_keys(ka: &[f64], kb: &[f64]) -> std::cmp::Ordering {
    for (va, vb) in ka.iter().zip(kb.iter()) {
        match va.partial_cmp(vb) {
            Some(std::cmp::Ordering::Equal) | None => continue,
            Some(ord) => return ord,
        }
    }
    std::cmp::Ordering::Equal
}

/// Sort by user priority then apply per-resolution and total caps.
pub fn sort_and_cap_stream_rows(
    mut rows: Vec<Value>,
    priority: &[SortingOption],
    selected_resolutions: &[Option<String>],
    quality_filter: &[String],
    language_sorting: &[Option<String>],
    cached_hashes: &HashMap<String, bool>,
    season: Option<i32>,
    episode: Option<i32>,
    max_per_resolution: u32,
    max_total: u32,
) -> Vec<Value> {
    if !priority.is_empty() {
        rows.sort_by(|a, b| {
            let ka = torrent_sort_key(
                a,
                priority,
                selected_resolutions,
                quality_filter,
                language_sorting,
                cached_hashes,
                season,
                episode,
            );
            let kb = torrent_sort_key(
                b,
                priority,
                selected_resolutions,
                quality_filter,
                language_sorting,
                cached_hashes,
                season,
                episode,
            );
            compare_sort_keys(&ka, &kb)
        });
    }
    cap_streams(rows, max_per_resolution, max_total)
}
