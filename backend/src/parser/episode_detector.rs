/// Episode and season detection from filenames.
///
/// Patterns are tried in priority order; the first match wins.
/// When a pattern captures only an episode (no season group), `default_season` is used.
use std::sync::OnceLock;

use regex::Regex;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DetectedEpisode {
    pub season: i32,
    pub episode: i32,
}

// ─── Compiled patterns ────────────────────────────────────────────────────────

fn re_sxxexx() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"(?i)[sS](\d{1,2})[eE](\d{1,2})").unwrap())
}

fn re_1x04() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    // Require word boundaries so "1080" doesn't match as "10x80"
    R.get_or_init(|| Regex::new(r"\b(\d{1,2})[xX](\d{2})\b").unwrap())
}

fn re_season_episode_text() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        Regex::new(r"(?i)[sS]eason\s+(\d{1,2})[^0-9]{1,20}[eE]pisode\s+(\d{1,2})").unwrap()
    })
}

fn re_bracketed() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        Regex::new(r"[\[\(](?:[sS])?(\d{1,2})[.\s]?(?:[eExX])(\d{1,2})[\]\)]").unwrap()
    })
}

fn re_period_sep() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    // e.g. 1.04  (season.episode, episode must be zero-padded two digits)
    R.get_or_init(|| Regex::new(r"\b(\d{1,2})\.(\d{2})\b").unwrap())
}

fn re_episode_only_dash() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    // _e01 / -ep01 / -e01
    R.get_or_init(|| Regex::new(r"(?i)[_\-][eE]p?(\d{1,3})\b").unwrap())
}

fn re_ep_word() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    // "Ep 4", "Episode.04", "ep04"
    R.get_or_init(|| Regex::new(r"(?i)\bEp(?:isode)?[.\s_]?(\d{1,3})\b").unwrap())
}

fn re_absolute() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    // Zero-padded 3-digit episode: 023, 123 — only if surrounded by non-digits
    R.get_or_init(|| Regex::new(r"\b0*(\d{3})\b").unwrap())
}

// ─── Public API ───────────────────────────────────────────────────────────────

/// Detect the season and episode from a filename.
///
/// `default_season` is used when a pattern yields only an episode number.
/// Returns `None` if no episode can be reliably detected.
pub fn detect_episode(filename: &str, default_season: i32) -> Option<DetectedEpisode> {
    // Strip directory component — work on the base name only.
    let base = std::path::Path::new(filename)
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or(filename);

    // Priority 1: S01E04 / s1e4
    if let Some(cap) = re_sxxexx().captures(base) {
        let s: i32 = cap[1].parse().ok()?;
        let e: i32 = cap[2].parse().ok()?;
        return Some(DetectedEpisode {
            season: s,
            episode: e,
        });
    }

    // Priority 2: 1x04 / 01X04
    if let Some(cap) = re_1x04().captures(base) {
        let s: i32 = cap[1].parse().ok()?;
        let e: i32 = cap[2].parse().ok()?;
        return Some(DetectedEpisode {
            season: s,
            episode: e,
        });
    }

    // Priority 3: Season 1 Episode 4 (text form)
    if let Some(cap) = re_season_episode_text().captures(base) {
        let s: i32 = cap[1].parse().ok()?;
        let e: i32 = cap[2].parse().ok()?;
        return Some(DetectedEpisode {
            season: s,
            episode: e,
        });
    }

    // Priority 4: [S01E04] / (1x4) — bracketed
    if let Some(cap) = re_bracketed().captures(base) {
        let s: i32 = cap[1].parse().ok()?;
        let e: i32 = cap[2].parse().ok()?;
        return Some(DetectedEpisode {
            season: s,
            episode: e,
        });
    }

    // Priority 5: 1.04 / 01.04 — period-separated
    if let Some(cap) = re_period_sep().captures(base) {
        let s: i32 = cap[1].parse().ok()?;
        let e: i32 = cap[2].parse().ok()?;
        // Sanity: episode <= 50, season <= 30 (avoid matching resolutions like 1080)
        if s <= 30 && e <= 50 {
            return Some(DetectedEpisode {
                season: s,
                episode: e,
            });
        }
    }

    // Priority 6: _e01 / -ep01 (episode only)
    if let Some(cap) = re_episode_only_dash().captures(base) {
        let e: i32 = cap[1].parse().ok()?;
        return Some(DetectedEpisode {
            season: default_season,
            episode: e,
        });
    }

    // Priority 7: Ep 4 / Episode 04 (episode only)
    if let Some(cap) = re_ep_word().captures(base) {
        let e: i32 = cap[1].parse().ok()?;
        return Some(DetectedEpisode {
            season: default_season,
            episode: e,
        });
    }

    // Priority 8: zero-padded 3-digit episode (e.g. 023) — only if no long hex context
    if let Some(cap) = re_absolute().captures(base) {
        // Skip if the surrounding context looks like a hex hash
        let m = cap.get(0).unwrap();
        let before = &base[..m.start()];
        let after = &base[m.end()..];
        let hex_ctx = before.chars().rev().take(8).all(|c| c.is_ascii_hexdigit())
            || after.chars().take(8).all(|c| c.is_ascii_hexdigit());
        if !hex_ctx {
            let e: i32 = cap[1].parse().ok()?;
            if e > 0 && e <= 999 {
                return Some(DetectedEpisode {
                    season: default_season,
                    episode: e,
                });
            }
        }
    }

    None
}

// ─── Video file detection ─────────────────────────────────────────────────────

const VIDEO_EXTENSIONS: &[&str] = &[
    "mkv", "mp4", "avi", "webm", "mov", "flv", "wmv", "m4v", "ts", "m2ts", "mpg", "mpeg",
];

pub fn is_video_file(filename: &str) -> bool {
    let lower = filename.to_lowercase();
    let ext = std::path::Path::new(&lower)
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("");
    VIDEO_EXTENSIONS.contains(&ext)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn formula2_practice_detect_episode_does_not_steal() {
        let f = "01.Formula.2.2026.R07.British.Practice.SkyF1HD.1080P.mkv";
        assert_eq!(detect_episode(f, 1), None);
    }

    #[test]
    fn test_sxxexx() {
        let r = detect_episode("Show.S02E05.mkv", 1).unwrap();
        assert_eq!(
            r,
            DetectedEpisode {
                season: 2,
                episode: 5
            }
        );
    }

    #[test]
    fn test_1x04() {
        let r = detect_episode("Show.2x08.720p.mkv", 1).unwrap();
        assert_eq!(
            r,
            DetectedEpisode {
                season: 2,
                episode: 8
            }
        );
    }

    #[test]
    fn test_text_form() {
        let r = detect_episode("Season 3 Episode 7.mp4", 1).unwrap();
        assert_eq!(
            r,
            DetectedEpisode {
                season: 3,
                episode: 7
            }
        );
    }

    #[test]
    fn test_bracketed() {
        let r = detect_episode("[S01E03] Title.mkv", 1).unwrap();
        assert_eq!(
            r,
            DetectedEpisode {
                season: 1,
                episode: 3
            }
        );
    }

    #[test]
    fn test_ep_word() {
        let r = detect_episode("ShowName.Ep.07.mkv", 1).unwrap();
        assert_eq!(
            r,
            DetectedEpisode {
                season: 1,
                episode: 7
            }
        );
    }

    #[test]
    fn test_no_match_resolution() {
        // 1080 should NOT match as season 10 episode 80
        assert!(detect_episode("Show.1080p.BluRay.mkv", 1).is_none());
    }

    #[test]
    fn test_is_video_file() {
        assert!(is_video_file("video.mkv"));
        assert!(is_video_file("video.MP4"));
        assert!(!is_video_file("readme.txt"));
    }
}
