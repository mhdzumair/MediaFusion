/// PTT-based file selection for torrent and usenet provider playback.
///
/// Mirrors Python `workers/providers/parser.py` `select_file_index_from_torrent`
/// and `workers/providers/usenet_file_selection.py` using the shared `ptt`
/// parser engine and `episode_detector` fallback.
use std::sync::OnceLock;

use regex::Regex;

use crate::{
    parser::episode_detector::{detect_episode, is_video_file},
    providers::ProviderError,
    ptt,
};

#[derive(Debug, Clone)]
pub struct FileEntry {
    pub index: usize,
    pub name: String,
    pub size: i64,
}

fn basename(name: &str) -> &str {
    std::path::Path::new(name)
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or(name)
}

fn video_files(files: &[FileEntry]) -> Vec<&FileEntry> {
    files
        .iter()
        .filter(|f| is_video_file(basename(&f.name)))
        .collect()
}

fn parse_season_episode(
    filename: &str,
    torrent_name: &str,
    default_season: i32,
) -> Option<(i32, i32)> {
    let base = basename(filename);
    let parsed = ptt::parse_title(base);
    if let (Some(s), Some(e)) = (parsed.seasons.first(), parsed.episodes.first()) {
        return Some((*s, *e));
    }
    if !parsed.episodes.is_empty() && parsed.seasons.is_empty() {
        return Some((default_season, parsed.episodes[0]));
    }

    if parsed.seasons.is_empty()
        && parsed.episodes.is_empty()
        && let Some(date_match) = date_str_regex().find(base)
        && normalize_calendar_date(date_match.as_str()).is_some()
    {
        return None;
    }

    let title_parsed = ptt::parse_title(torrent_name);
    if title_parsed.seasons.len() == 1 && title_parsed.episodes.len() == 1 {
        return Some((title_parsed.seasons[0], title_parsed.episodes[0]));
    }

    detect_episode(base, default_season).map(|ep| (ep.season, ep.episode))
}

fn date_str_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"\d{4}\.\d{2}\.\d{2}|\d{4}-\d{2}-\d{2}|\d{4}_\d{2}_\d{2}|\d{4}\s+\d{2}\s+\d{2}|\d{2}\.\d{2}\.\d{4}|\d{2}-\d{2}-\d{4}|\d{2}_\d{2}_\d{4}|\d{2}\s+\d{2}\s+\d{4}",
        )
        .expect("DATE_STR_REGEX")
    })
}

fn normalize_calendar_date(value: &str) -> Option<String> {
    let s = value.trim();
    if s.len() >= 10 && s.as_bytes().get(4) == Some(&b'-') && s.as_bytes().get(7) == Some(&b'-') {
        return Some(s[..10].to_string());
    }
    parse_date_token(s)
}

fn parse_date_token(token: &str) -> Option<String> {
    let token = token.trim();
    let parts: Vec<&str> = token
        .split(|c: char| c == '.' || c == '-' || c == '_' || c.is_whitespace())
        .filter(|p| !p.is_empty())
        .collect();
    if parts.len() < 3 {
        return None;
    }
    let (y, m, d) = if parts[0].len() == 4 {
        (parts[0], parts[1], parts[2])
    } else if parts[2].len() == 4 {
        (parts[2], parts[1], parts[0])
    } else {
        return None;
    };
    if y.len() != 4 || m.len() > 2 || d.len() > 2 {
        return None;
    }
    let month: u32 = m.parse().ok()?;
    let day: u32 = d.parse().ok()?;
    if !(1..=12).contains(&month) || !(1..=31).contains(&day) {
        return None;
    }
    Some(format!("{y}-{month:02}-{day:02}"))
}

/// Best-effort YYYY-MM-DD from a release label (PTT date, then DATE_STR_REGEX).
pub fn extract_air_date_from_label(label: &str) -> Option<String> {
    let text = label.trim();
    if text.is_empty() {
        return None;
    }

    let parsed = ptt::parse_title(text);
    if let Some(ref raw_date) = parsed.date
        && let Some(normalized) = normalize_calendar_date(raw_date)
    {
        return Some(normalized);
    }

    let search_text = text.replace('\\', "/");
    let re = date_str_regex();
    let m = re.find(&search_text)?;
    parse_date_token(m.as_str())
}

/// True if `label` contains a calendar date equal to `air_date_iso` (YYYY-MM-DD).
pub fn usenet_label_matches_air_date(label: &str, air_date_iso: &str) -> bool {
    let target = normalize_calendar_date(air_date_iso);
    let Some(target) = target else {
        return false;
    };
    extract_air_date_from_label(label).is_some_and(|extracted| extracted == target)
}

/// True if `label` parses to this season/episode (PTT + torrent-style fallback patterns).
pub fn usenet_label_matches_season_episode(label: &str, season: i32, episode: i32) -> bool {
    let text = label.trim();
    if text.is_empty() {
        return false;
    }

    let parsed = ptt::parse_title(text);
    if !parsed.seasons.is_empty() && !parsed.episodes.is_empty() {
        return parsed.seasons[0] == season && parsed.episodes[0] == episode;
    }
    if parsed.seasons.is_empty() && !parsed.episodes.is_empty() {
        return parsed.episodes[0] == episode;
    }

    detect_episode(text, season).is_some_and(|ep| ep.season == season && ep.episode == episode)
}

/// Select the best file index from a provider file list.
pub fn select_torrent_file_index(
    files: &[FileEntry],
    torrent_name: &str,
    filename: Option<&str>,
    season: Option<i32>,
    episode: Option<i32>,
    file_index: Option<i32>,
    episode_air_date: Option<&str>,
) -> Result<usize, ProviderError> {
    if files.is_empty() {
        return Err(ProviderError::api(
            "No files found in torrent",
            "no_matching_file.mp4",
        ));
    }

    if let Some(name) = filename {
        for f in files {
            if basename(&f.name) == name {
                return Ok(f.index);
            }
        }
    }

    let videos = video_files(files);
    if videos.is_empty() {
        return Err(ProviderError::api(
            "No valid video files found in torrent",
            "no_matching_file.mp4",
        ));
    }

    if let Some(fi) = file_index
        && fi >= 0
        && (fi as usize) < files.len()
    {
        return Ok(fi as usize);
    }

    if let (Some(s), Some(e)) = (season, episode) {
        for f in &videos {
            if let Some((fs, fe)) = parse_season_episode(&f.name, torrent_name, s)
                && fs == s
                && fe == e
            {
                return Ok(f.index);
            }
        }
        if !videos.is_empty() {
            return Ok(videos[0].index);
        }
        return Err(ProviderError::api(
            "Found video files but couldn't match season/episode",
            "episode_not_found.mp4",
        ));
    }

    if let Some(air_date) = episode_air_date.filter(|d| !d.trim().is_empty()) {
        for f in &videos {
            if usenet_label_matches_air_date(&f.name, air_date) {
                return Ok(f.index);
            }
        }
    }

    videos
        .iter()
        .max_by_key(|f| f.size)
        .map(|f| f.index)
        .ok_or_else(|| {
            ProviderError::api(
                "No valid video file found in torrent",
                "no_matching_file.mp4",
            )
        })
}

/// Build [`FileEntry`] list from JSON provider file arrays.
pub fn files_from_json(
    arr: &[serde_json::Value],
    name_keys: &[&str],
    size_keys: &[&str],
) -> Vec<FileEntry> {
    arr.iter()
        .enumerate()
        .filter_map(|(idx, v)| {
            let name = name_keys
                .iter()
                .find_map(|k| v.get(*k).and_then(|x| x.as_str()))
                .unwrap_or_default()
                .to_string();
            if name.is_empty() {
                return None;
            }
            let size = size_keys
                .iter()
                .find_map(|k| v.get(*k).and_then(|x| x.as_i64()))
                .unwrap_or(0);
            Some(FileEntry {
                index: idx,
                name,
                size,
            })
        })
        .collect()
}

/// Shared helper for debrid providers: PTT-based index with largest-video fallback.
pub fn select_debrid_file_index(
    pairs: &[(String, i64)],
    release_name: &str,
    filename: Option<&str>,
    file_index: Option<i32>,
    season: Option<i32>,
    episode: Option<i32>,
    episode_air_date: Option<&str>,
) -> usize {
    let files: Vec<FileEntry> = pairs
        .iter()
        .enumerate()
        .map(|(i, (name, size))| FileEntry {
            index: i,
            name: name.clone(),
            size: *size,
        })
        .collect();

    if let Ok(idx) = select_torrent_file_index(
        &files,
        release_name,
        filename,
        season,
        episode,
        file_index,
        episode_air_date,
    ) {
        return idx;
    }

    if episode_air_date.is_some()
        && let Ok(idx) = select_usenet_file_index(
            &files,
            release_name,
            filename,
            season,
            episode,
            episode_air_date,
        )
    {
        return idx;
    }

    if let Ok(idx) =
        select_torrent_file_index(&files, release_name, filename, None, None, file_index, None)
    {
        return idx;
    }

    video_files(&files)
        .iter()
        .max_by_key(|f| f.size)
        .map(|f| f.index)
        .unwrap_or(0)
}

/// Pick index among **video** files only: exact name, season/episode, optional air date, else largest.
pub fn select_usenet_file_index(
    files: &[FileEntry],
    _release_name: &str,
    filename: Option<&str>,
    season: Option<i32>,
    episode: Option<i32>,
    episode_air_date: Option<&str>,
) -> Result<usize, ProviderError> {
    select_usenet_file_index_with_options(files, filename, season, episode, episode_air_date, false)
}

pub fn select_usenet_file_index_with_options(
    files: &[FileEntry],
    filename: Option<&str>,
    season: Option<i32>,
    episode: Option<i32>,
    episode_air_date: Option<&str>,
    match_path_suffix: bool,
) -> Result<usize, ProviderError> {
    if files.is_empty() {
        return Err(ProviderError::api(
            "No files found in usenet download",
            "no_matching_file.mp4",
        ));
    }

    if let Some(name) = filename.filter(|n| !n.trim().is_empty()) {
        let want = name.trim().to_lowercase();
        for f in files {
            if !is_video_file(basename(&f.name)) {
                continue;
            }
            let ll = f.name.to_lowercase();
            if ll == want || (match_path_suffix && ll.ends_with(&format!("/{want}"))) {
                return Ok(f.index);
            }
        }
    }

    if let (Some(s), Some(e)) = (season, episode) {
        for f in files {
            if !is_video_file(basename(&f.name)) {
                continue;
            }
            if usenet_label_matches_season_episode(&f.name, s, e) {
                return Ok(f.index);
            }
        }
    }

    if let Some(air_date) = episode_air_date.filter(|d| !d.trim().is_empty()) {
        for f in files {
            if !is_video_file(basename(&f.name)) {
                continue;
            }
            if usenet_label_matches_air_date(&f.name, air_date) {
                return Ok(f.index);
            }
        }
    }

    let videos = video_files(files);
    if videos.is_empty() {
        return Err(ProviderError::api(
            "No video file found in this Usenet download (only non-video files present).",
            "no_video_file_found.mp4",
        ));
    }

    videos
        .iter()
        .max_by_key(|f| f.size)
        .map(|f| f.index)
        .ok_or_else(|| {
            ProviderError::api(
                "No video file found in this Usenet download (only non-video files present).",
                "no_video_file_found.mp4",
            )
        })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn entry(index: usize, name: &str, size: i64) -> FileEntry {
        FileEntry {
            index,
            name: name.to_string(),
            size,
        }
    }

    #[test]
    fn usenet_matches_air_date_in_filename() {
        let files = vec![
            entry(0, "readme.nfo", 100),
            entry(1, "Late Night 2024-06-15 720p.mkv", 2_000_000),
        ];
        let idx = select_usenet_file_index(&files, "release", None, None, None, Some("2024-06-15"))
            .unwrap();
        assert_eq!(idx, 1);
    }

    #[test]
    fn usenet_skips_non_video_for_air_date() {
        let files = vec![
            entry(0, "2024-06-15.txt", 100),
            entry(1, "show.2024.06.15.1080p.mkv", 3_000_000),
        ];
        let idx = select_usenet_file_index(&files, "release", None, None, None, Some("2024-06-15"))
            .unwrap();
        assert_eq!(idx, 1);
    }

    #[test]
    fn usenet_season_episode_takes_priority_over_largest() {
        let files = vec![
            entry(0, "Show.S01E02.720p.mkv", 1_000_000),
            entry(1, "Show.S01E99.1080p.mkv", 5_000_000),
        ];
        let idx = select_usenet_file_index(&files, "Show", None, Some(1), Some(2), None).unwrap();
        assert_eq!(idx, 0);
    }

    #[test]
    fn usenet_filename_match_is_case_insensitive() {
        let files = vec![entry(0, "Folder/EPISODE.MKV", 1_000_000)];
        let idx =
            select_usenet_file_index(&files, "release", Some("episode.mkv"), None, None, None)
                .unwrap();
        assert_eq!(idx, 0);
    }

    #[test]
    fn extract_air_date_from_dotted_format() {
        assert_eq!(
            extract_air_date_from_label("Daily.Show.2024.06.15.720p.WEB.mkv").as_deref(),
            Some("2024-06-15")
        );
    }

    #[test]
    fn torrent_single_video_fallback_when_season_episode_unmatched() {
        let files = vec![entry(0, "Random.Release.1080p.mkv", 2_000_000)];
        let idx =
            select_torrent_file_index(&files, "Show", None, Some(1), Some(2), None, None).unwrap();
        assert_eq!(idx, 0);
    }
}
