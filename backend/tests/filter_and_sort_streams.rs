//! Parity tests for stream filter + sort pipeline (Python `filter_and_sort_streams`).

use std::collections::HashMap;

use mediafusion_api::models::user_data::{SortingOption, UserData};
use mediafusion_api::parser::{
    filter_sort_and_cap_streams, sort_size_bytes_for_row, FilterContext,
};
use serde_json::{json, Value};

const GB: i64 = 1024 * 1024 * 1024;

fn make_stream(
    name: &str,
    size: i64,
    resolution: Option<&str>,
    quality: Option<&str>,
    hdr_formats: &[&str],
    languages: &[&str],
    seeders: i64,
    info_hash: Option<&str>,
    file_size: Option<i64>,
) -> Value {
    let default_hash: String = {
        let h: String = name
            .chars()
            .filter(|c| c.is_alphanumeric())
            .take(40)
            .collect();
        format!("{h:0<40}")
    };
    let hash = info_hash.unwrap_or(default_hash.as_str());
    let mut row = json!({
        "name": name,
        "info_hash": hash,
        "size": size,
        "resolution": resolution,
        "quality": quality,
        "hdr_formats": hdr_formats,
        "languages": languages,
        "seeders": seeders,
        "torrent_type": "public",
    });
    if let Some(fs) = file_size {
        row.as_object_mut()
            .expect("object")
            .insert("file_size".into(), json!(fs));
    }
    row
}

fn make_user_data(overrides: serde_json::Value) -> UserData {
    let mut base = json!({
        "sr": ["1080p", "720p", "480p"],
        "qf": ["WEB/HD", "BluRay/UHD"],
        "hf": ["HDR10", "HDR10+", "Dolby Vision", "HLG", "SDR", "Unknown"],
        "ls": ["English", "Hindi", "Tamil"],
        "tsp": [
            {"k": "resolution", "d": "desc"},
            {"k": "size", "d": "desc"},
        ],
        "ms": "inf",
        "mns": 0,
        "mspr": 10,
        "mxs": 50,
        "snfm": "disabled",
        "snfp": [],
        "snfr": false,
    });
    if let (Some(base_obj), Some(over_obj)) = (base.as_object_mut(), overrides.as_object()) {
        for (k, v) in over_obj {
            base_obj.insert(k.clone(), v.clone());
        }
    }
    serde_json::from_value(base).expect("valid UserData")
}

fn run_pipeline(
    streams: Vec<Value>,
    user_data: &UserData,
    season: Option<i32>,
    episode: Option<i32>,
) -> Vec<Value> {
    let ctx = FilterContext {
        user_data,
        season,
        episode,
        primary_provider: None,
        is_usenet: false,
        allow_public_usenet: false,
    };
    let priority = user_data.sorting_priority();
    let selected_resolutions = user_data.effective_selected_resolutions();
    let quality_filter = if user_data.quality_filter.is_empty() {
        mediafusion_api::parser::default_quality_filter_groups()
    } else {
        user_data.quality_filter.clone()
    };
    let language_sorting = user_data.language_sorting_list();
    filter_sort_and_cap_streams(
        streams,
        &ctx,
        &priority,
        &selected_resolutions,
        &quality_filter,
        &language_sorting,
        &HashMap::new(),
        user_data.max_streams_per_resolution,
        user_data.effective_max_streams(),
    )
}

fn names(rows: &[Value]) -> Vec<String> {
    rows.iter()
        .filter_map(|r| r.get("name").and_then(|v| v.as_str()).map(str::to_string))
        .collect()
}

#[test]
fn resolution_filter_selected_pass() {
    let streams = vec![
        make_stream(
            "S.1080p",
            2 * GB,
            Some("1080p"),
            Some("WEB-DL"),
            &[],
            &["English"],
            10,
            None,
            None,
        ),
        make_stream(
            "S.720p",
            2 * GB,
            Some("720p"),
            Some("WEB-DL"),
            &[],
            &["English"],
            10,
            None,
            None,
        ),
    ];
    let ud = make_user_data(json!({"sr": ["1080p", "720p"]}));
    assert_eq!(run_pipeline(streams, &ud, Some(1), Some(1)).len(), 2);
}

#[test]
fn resolution_filter_unselected_removed() {
    let streams = vec![
        make_stream(
            "S.4k",
            2 * GB,
            Some("4k"),
            Some("WEB-DL"),
            &[],
            &["English"],
            10,
            None,
            None,
        ),
        make_stream(
            "S.1080p",
            2 * GB,
            Some("1080p"),
            Some("WEB-DL"),
            &[],
            &["English"],
            10,
            None,
            None,
        ),
    ];
    let ud = make_user_data(json!({"sr": ["1080p"]}));
    let result = run_pipeline(streams, &ud, Some(1), Some(1));
    assert_eq!(result.len(), 1);
    assert_eq!(
        result[0].get("name").and_then(|v| v.as_str()),
        Some("S.1080p")
    );
}

#[test]
fn resolution_sort_user_order_desc() {
    let streams = vec![
        make_stream(
            "S.1080p",
            2 * GB,
            Some("1080p"),
            Some("WEB-DL"),
            &[],
            &["English"],
            10,
            None,
            None,
        ),
        make_stream(
            "S.720p",
            2 * GB,
            Some("720p"),
            Some("WEB-DL"),
            &[],
            &["English"],
            10,
            None,
            None,
        ),
        make_stream(
            "S.4k",
            2 * GB,
            Some("4k"),
            Some("WEB-DL"),
            &[],
            &["English"],
            10,
            None,
            None,
        ),
    ];
    let ud = make_user_data(json!({
        "sr": ["720p", "1080p", "4k"],
        "tsp": [{"k": "resolution", "d": "desc"}],
    }));
    assert_eq!(
        names(&run_pipeline(streams, &ud, Some(1), Some(1))),
        vec!["S.720p", "S.1080p", "S.4k"]
    );
}

#[test]
fn quality_filter_unselected_removed() {
    let streams = vec![
        make_stream(
            "S.WEB",
            2 * GB,
            Some("1080p"),
            Some("WEB-DL"),
            &[],
            &["English"],
            10,
            None,
            None,
        ),
        make_stream(
            "S.CAM",
            2 * GB,
            Some("1080p"),
            Some("CAM"),
            &[],
            &["English"],
            10,
            None,
            None,
        ),
    ];
    let ud = make_user_data(json!({"qf": ["WEB/HD"]}));
    let result = run_pipeline(streams, &ud, Some(1), Some(1));
    assert_eq!(result.len(), 1);
    assert_eq!(
        result[0].get("name").and_then(|v| v.as_str()),
        Some("S.WEB")
    );
}

#[test]
fn hdr_unknown_matches_unknown_filter() {
    let streams = vec![make_stream(
        "S.UnknownHDR",
        2 * GB,
        Some("1080p"),
        Some("WEB-DL"),
        &[],
        &["English"],
        10,
        None,
        None,
    )];
    let ud = make_user_data(json!({"hf": ["Unknown"]}));
    let result = run_pipeline(streams, &ud, Some(1), Some(1));
    assert_eq!(result.len(), 1);
    let hdr = result[0]
        .get("filtered_hdr_formats")
        .and_then(|v| v.as_array())
        .expect("hdr array");
    assert_eq!(hdr[0].as_str(), Some("Unknown"));
}

#[test]
fn hdr_empty_not_treated_as_sdr() {
    let streams = vec![make_stream(
        "S.UnknownHDR",
        2 * GB,
        Some("1080p"),
        Some("WEB-DL"),
        &[],
        &["English"],
        10,
        None,
        None,
    )];
    let ud = make_user_data(json!({"hf": ["SDR"]}));
    assert!(run_pipeline(streams, &ud, Some(1), Some(1)).is_empty());
}

#[test]
fn hdr_aliases_map_to_canonical() {
    let streams = vec![make_stream(
        "S.DV.HDR",
        2 * GB,
        Some("1080p"),
        Some("WEB-DL"),
        &["DV", "HDR"],
        &["English"],
        10,
        None,
        None,
    )];
    let ud = make_user_data(json!({"hf": ["Dolby Vision", "HDR10"]}));
    let result = run_pipeline(streams, &ud, Some(1), Some(1));
    assert_eq!(result.len(), 1);
    let hdr: Vec<String> = result[0]
        .get("filtered_hdr_formats")
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();
    assert!(hdr.contains(&"Dolby Vision".to_string()));
    assert!(hdr.contains(&"HDR10".to_string()));
}

#[test]
fn language_filter_unselected_removed() {
    let streams = vec![
        make_stream(
            "S.En",
            2 * GB,
            Some("1080p"),
            Some("WEB-DL"),
            &[],
            &["English"],
            10,
            None,
            None,
        ),
        make_stream(
            "S.Fr",
            2 * GB,
            Some("1080p"),
            Some("WEB-DL"),
            &[],
            &["French"],
            10,
            None,
            None,
        ),
    ];
    let ud = make_user_data(json!({"ls": ["English"]}));
    let result = run_pipeline(streams, &ud, Some(1), Some(1));
    assert_eq!(result.len(), 1);
    assert_eq!(result[0].get("name").and_then(|v| v.as_str()), Some("S.En"));
}

#[test]
fn language_sort_user_preference() {
    let streams = vec![
        make_stream(
            "S.En",
            2 * GB,
            Some("1080p"),
            Some("WEB-DL"),
            &[],
            &["English"],
            10,
            None,
            None,
        ),
        make_stream(
            "S.Ta",
            2 * GB,
            Some("1080p"),
            Some("WEB-DL"),
            &[],
            &["Tamil"],
            10,
            None,
            None,
        ),
    ];
    let ud = make_user_data(json!({
        "sr": ["1080p"],
        "ls": ["Tamil", "English"],
        "tsp": [{"k": "language", "d": "desc"}],
    }));
    assert_eq!(
        names(&run_pipeline(streams, &ud, Some(1), Some(1))),
        vec!["S.Ta", "S.En"]
    );
}

#[test]
fn episode_aware_size_sort_prefers_episode_file() {
    let ep_bytes = (10.9 * GB as f64) as i64;
    let pack_total = (85.3 * GB as f64) as i64;
    let streams = vec![
        make_stream(
            "season.pack",
            pack_total,
            Some("1080p"),
            Some("WEB-DL"),
            &[],
            &["English"],
            10,
            None,
            Some(ep_bytes),
        ),
        make_stream(
            "single.large",
            (12.1 * GB as f64) as i64,
            Some("1080p"),
            Some("WEB-DL"),
            &[],
            &["English"],
            10,
            None,
            None,
        ),
        make_stream(
            "single.mid",
            (11.2 * GB as f64) as i64,
            Some("1080p"),
            Some("WEB-DL"),
            &[],
            &["English"],
            10,
            None,
            None,
        ),
    ];
    let ud = make_user_data(json!({"tsp": [{"k": "size", "d": "desc"}]}));
    assert_eq!(
        names(&run_pipeline(streams, &ud, Some(1), Some(1))),
        vec!["single.large", "single.mid", "season.pack"]
    );
}

#[test]
fn max_size_filters_large_streams() {
    let streams = vec![
        make_stream(
            "S.Small",
            5 * GB,
            Some("1080p"),
            Some("WEB-DL"),
            &[],
            &["English"],
            10,
            None,
            None,
        ),
        make_stream(
            "S.Big",
            100 * GB,
            Some("1080p"),
            Some("WEB-DL"),
            &[],
            &["English"],
            10,
            None,
            None,
        ),
    ];
    let ud = make_user_data(json!({"ms": 50 * GB}));
    let result = run_pipeline(streams, &ud, Some(1), Some(1));
    assert_eq!(result.len(), 1);
    assert_eq!(
        result[0].get("name").and_then(|v| v.as_str()),
        Some("S.Small")
    );
}

#[test]
fn min_size_filters_small_streams() {
    let streams = vec![
        make_stream(
            "S.Tiny",
            500 * 1024 * 1024,
            Some("1080p"),
            Some("WEB-DL"),
            &[],
            &["English"],
            10,
            None,
            None,
        ),
        make_stream(
            "S.Normal",
            5 * GB,
            Some("1080p"),
            Some("WEB-DL"),
            &[],
            &["English"],
            10,
            None,
            None,
        ),
    ];
    let ud = make_user_data(json!({"mns": GB}));
    let result = run_pipeline(streams, &ud, Some(1), Some(1));
    assert_eq!(result.len(), 1);
    assert_eq!(
        result[0].get("name").and_then(|v| v.as_str()),
        Some("S.Normal")
    );
}

#[test]
fn min_size_zero_keeps_unknown_size() {
    let streams = vec![
        make_stream(
            "S.Unknown",
            0,
            Some("1080p"),
            Some("WEB-DL"),
            &[],
            &["English"],
            10,
            None,
            None,
        ),
        make_stream(
            "S.Big",
            5 * GB,
            Some("1080p"),
            Some("WEB-DL"),
            &[],
            &["English"],
            10,
            None,
            None,
        ),
    ];
    let ud = make_user_data(json!({"mns": GB}));
    assert_eq!(run_pipeline(streams, &ud, Some(1), Some(1)).len(), 2);
}

#[test]
fn max_streams_per_resolution() {
    let streams: Vec<Value> = (0..5)
        .map(|i| {
            make_stream(
                &format!("S.1080p.{i}"),
                2 * GB,
                Some("1080p"),
                Some("WEB-DL"),
                &[],
                &["English"],
                10,
                Some(&format!("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa{i}")),
                None,
            )
        })
        .collect();
    let ud = make_user_data(json!({"mspr": 3}));
    assert_eq!(run_pipeline(streams, &ud, Some(1), Some(1)).len(), 3);
}

#[test]
fn max_total_streams_cap() {
    let streams: Vec<Value> = (0..20)
        .map(|i| {
            make_stream(
                &format!("S.{i}"),
                2 * GB,
                Some("1080p"),
                Some("WEB-DL"),
                &[],
                &["English"],
                10,
                Some(&format!("cccccccccccccccccccccccccccccccccccccc{i}")),
                None,
            )
        })
        .collect();
    let ud = make_user_data(json!({"mxs": 5, "mspr": 50}));
    assert_eq!(run_pipeline(streams, &ud, Some(1), Some(1)).len(), 5);
}

#[test]
fn stream_name_include_keyword() {
    let streams = vec![
        make_stream(
            "Movie.HEVC.1080p",
            2 * GB,
            Some("1080p"),
            Some("WEB-DL"),
            &[],
            &["English"],
            10,
            None,
            None,
        ),
        make_stream(
            "Movie.x264.1080p",
            2 * GB,
            Some("1080p"),
            Some("WEB-DL"),
            &[],
            &["English"],
            10,
            None,
            None,
        ),
    ];
    let ud = make_user_data(json!({
        "snfm": "include",
        "snfp": ["HEVC"],
        "snfr": false,
    }));
    let result = run_pipeline(streams, &ud, Some(1), Some(1));
    assert_eq!(result.len(), 1);
    assert_eq!(
        result[0].get("name").and_then(|v| v.as_str()),
        Some("Movie.HEVC.1080p")
    );
}

#[test]
fn stream_name_exclude_keyword() {
    let streams = vec![
        make_stream(
            "Movie.HEVC.1080p",
            2 * GB,
            Some("1080p"),
            Some("WEB-DL"),
            &[],
            &["English"],
            10,
            None,
            None,
        ),
        make_stream(
            "Movie.x264.1080p",
            2 * GB,
            Some("1080p"),
            Some("WEB-DL"),
            &[],
            &["English"],
            10,
            None,
            None,
        ),
    ];
    let ud = make_user_data(json!({
        "snfm": "exclude",
        "snfp": ["HEVC"],
        "snfr": false,
    }));
    let result = run_pipeline(streams, &ud, Some(1), Some(1));
    assert_eq!(result.len(), 1);
    assert_eq!(
        result[0].get("name").and_then(|v| v.as_str()),
        Some("Movie.x264.1080p")
    );
}

#[test]
fn full_pipeline_integration() {
    let streams = vec![
        make_stream(
            "Big.BluRay.4k",
            80 * GB,
            Some("4k"),
            Some("BluRay"),
            &[],
            &["English"],
            50,
            None,
            None,
        ),
        make_stream(
            "Good.WEB.1080p",
            4 * GB,
            Some("1080p"),
            Some("WEB-DL"),
            &[],
            &["English"],
            100,
            None,
            None,
        ),
        make_stream(
            "Small.WEB.720p",
            GB,
            Some("720p"),
            Some("WEB-DL"),
            &[],
            &["Hindi"],
            30,
            None,
            None,
        ),
        make_stream(
            "Tiny.CAM.480p",
            500 * 1024 * 1024,
            Some("480p"),
            Some("CAM"),
            &[],
            &["English"],
            5,
            None,
            None,
        ),
        make_stream(
            "Hindi.WEB.1080p",
            3 * GB,
            Some("1080p"),
            Some("WEB-DL"),
            &[],
            &["Hindi"],
            80,
            None,
            None,
        ),
        make_stream(
            "French.WEB.1080p",
            4 * GB,
            Some("1080p"),
            Some("WEB-DL"),
            &[],
            &["French"],
            40,
            None,
            None,
        ),
    ];
    let ud = make_user_data(json!({
        "sr": ["1080p", "720p"],
        "qf": ["WEB/HD"],
        "ls": ["Hindi", "English"],
        "tsp": [
            {"k": "language", "d": "desc"},
            {"k": "size", "d": "desc"},
        ],
        "ms": 50 * GB,
        "mns": GB,
        "mxs": 10,
    }));
    assert_eq!(
        names(&run_pipeline(streams, &ud, Some(1), Some(1))),
        vec!["Hindi.WEB.1080p", "Small.WEB.720p", "Good.WEB.1080p"]
    );
}

#[test]
fn sort_size_bytes_episode_vs_movie() {
    let ep_bytes = (10.9 * GB as f64) as i64;
    let pack_total = (85.3 * GB as f64) as i64;
    let row = make_stream(
        "season.pack",
        pack_total,
        Some("1080p"),
        Some("WEB-DL"),
        &[],
        &["English"],
        10,
        None,
        Some(ep_bytes),
    );
    assert_eq!(sort_size_bytes_for_row(&row, Some(1), Some(1)), ep_bytes);
    assert_eq!(sort_size_bytes_for_row(&row, None, None), pack_total);
}

#[test]
fn sorting_option_deserializes_short_keys() {
    let opt: SortingOption =
        serde_json::from_value(json!({"k": "resolution", "d": "desc"})).unwrap();
    assert_eq!(opt.key, "resolution");
    assert_eq!(opt.direction, "desc");
}
