use std::sync::OnceLock;

use rand::seq::IndexedRandom;
use serde_json::Value;

static SPORTS_ARTIFACTS: OnceLock<Value> = OnceLock::new();

static JSON_BYTES: &str = include_str!("../../../resources/json/sports_artifacts.json");

fn artifacts() -> &'static Value {
    SPORTS_ARTIFACTS.get_or_init(|| {
        serde_json::from_str(JSON_BYTES).expect("sports_artifacts.json is valid JSON")
    })
}

/// Return a random poster URL for one of the given genre names, with an
/// unconditional "Other Sports"/"Sports" fallback when no genre matches.
/// Used when `is_add_title_to_poster = true`.
pub fn random_sports_poster(genres: &[String]) -> Option<String> {
    let artifacts = artifacts();
    let obj = artifacts.as_object()?;
    let mut rng = rand::rng();

    for genre in genres {
        // Exact match
        if let Some(posters) = obj
            .get(genre)
            .and_then(|v| v.get("poster"))
            .and_then(|v| v.as_array())
        {
            let urls: Vec<&str> = posters.iter().filter_map(|v| v.as_str()).collect();
            if let Some(url) = urls.choose(&mut rng) {
                return Some((*url).to_string());
            }
        }
        // Case-insensitive match
        let lower = genre.to_lowercase();
        for (key, val) in obj {
            if key.to_lowercase() == lower {
                if let Some(posters) = val.get("poster").and_then(|v| v.as_array()) {
                    let urls: Vec<&str> = posters.iter().filter_map(|v| v.as_str()).collect();
                    if let Some(url) = urls.choose(&mut rng) {
                        return Some((*url).to_string());
                    }
                }
            }
        }
    }

    // Fallback
    for key in &["Other Sports", "Sports"] {
        if let Some(posters) = obj
            .get(*key)
            .and_then(|v| v.get("poster"))
            .and_then(|v| v.as_array())
        {
            let urls: Vec<&str> = posters.iter().filter_map(|v| v.as_str()).collect();
            if let Some(url) = urls.choose(&mut rng) {
                return Some((*url).to_string());
            }
        }
    }

    None
}

/// Return a random poster URL only when a genre explicitly matches a sports
/// artifact key. Unlike `random_sports_poster`, this function does NOT fall
/// back to "Other Sports"/"Sports" — returning `None` for non-sports items so
/// the poster endpoint can still serve 404 for unrelated content.
pub fn random_sports_poster_strict(genres: &[String]) -> Option<String> {
    let artifacts = artifacts();
    let obj = artifacts.as_object()?;
    let mut rng = rand::rng();

    for genre in genres {
        if let Some(posters) = obj
            .get(genre)
            .and_then(|v| v.get("poster"))
            .and_then(|v| v.as_array())
        {
            let urls: Vec<&str> = posters.iter().filter_map(|v| v.as_str()).collect();
            if let Some(url) = urls.choose(&mut rng) {
                return Some((*url).to_string());
            }
        }
        let lower = genre.to_lowercase();
        for (key, val) in obj {
            if key.to_lowercase() == lower {
                if let Some(posters) = val.get("poster").and_then(|v| v.as_array()) {
                    let urls: Vec<&str> = posters.iter().filter_map(|v| v.as_str()).collect();
                    if let Some(url) = urls.choose(&mut rng) {
                        return Some((*url).to_string());
                    }
                }
            }
        }
    }

    None
}
