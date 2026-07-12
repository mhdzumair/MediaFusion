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

/// Map a sports catalog key (e.g. `formula_racing`) to a `sports_artifacts.json` top-level key.
pub fn catalog_to_artifact_key(catalog: &str) -> Option<&'static str> {
    match catalog {
        "formula_racing" => Some("Formula Racing"),
        "motogp_racing" => Some("MotoGP Racing"),
        "fighting" => Some("Fighting (WWE, UFC)"),
        "football" => Some("Football"),
        "basketball" => Some("Basketball"),
        "hockey" => Some("Hockey"),
        "american_football" => Some("American Football"),
        "baseball" => Some("Baseball"),
        "rugby" => Some("Rugby/AFL"),
        "tennis" => Some("Tennis"),
        "golf" => Some("Golf"),
        "cycling" => Some("Cycling"),
        "athletics" => Some("Athletics"),
        "wwe" => Some("WWE"),
        _ => None,
    }
}

fn pick_poster_for_keys(keys: &[String], allow_fallback: bool) -> Option<String> {
    let artifacts = artifacts();
    let obj = artifacts.as_object()?;
    let mut rng = rand::rng();

    for key in keys {
        if let Some(posters) = obj
            .get(key)
            .and_then(|v| v.get("poster"))
            .and_then(|v| v.as_array())
        {
            let urls: Vec<&str> = posters.iter().filter_map(|v| v.as_str()).collect();
            if let Some(url) = urls.choose(&mut rng) {
                return Some((*url).to_string());
            }
        }
        let lower = key.to_lowercase().replace('_', " ");
        for (artifact_key, val) in obj {
            if artifact_key.to_lowercase().replace('_', " ") == lower
                && let Some(posters) = val.get("poster").and_then(|v| v.as_array())
            {
                let urls: Vec<&str> = posters.iter().filter_map(|v| v.as_str()).collect();
                if let Some(url) = urls.choose(&mut rng) {
                    return Some((*url).to_string());
                }
            }
        }
    }

    if !allow_fallback {
        return None;
    }

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

/// Return a random poster URL for one of the given sports catalog keys, with an
/// unconditional "Other Sports"/"Sports" fallback when no catalog matches.
/// Used when `is_add_title_to_poster = true`.
pub fn random_sports_poster_for_catalogs(catalogs: &[String]) -> Option<String> {
    let keys: Vec<String> = catalogs
        .iter()
        .filter_map(|c| catalog_to_artifact_key(c).map(str::to_string))
        .collect();
    pick_poster_for_keys(&keys, true)
}

/// Return a random poster URL only when a catalog explicitly maps to a sports
/// artifact key. Unlike `random_sports_poster_for_catalogs`, this function does
/// NOT fall back to "Other Sports"/"Sports".
pub fn random_sports_poster_strict_for_catalogs(catalogs: &[String]) -> Option<String> {
    let keys: Vec<String> = catalogs
        .iter()
        .filter_map(|c| catalog_to_artifact_key(c).map(str::to_string))
        .collect();
    pick_poster_for_keys(&keys, false)
}

/// Map a fighting torrent title to a brand-specific sports artifact key.
pub fn fighting_brand_key(title: &str) -> &'static str {
    crate::parser::detect_fighting_brand(title)
}

/// Pick a brand-appropriate fighting poster (WWE, AEW, UFC, Boxing, …).
pub fn random_poster_for_fighting_title(title: &str) -> Option<String> {
    let brand = fighting_brand_key(title);
    pick_poster_for_keys(&[brand.to_string()], false)
        .or_else(|| pick_poster_for_keys(&["Fighting".to_string()], false))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn catalog_maps_to_formula_racing_artifact() {
        assert_eq!(
            catalog_to_artifact_key("formula_racing"),
            Some("Formula Racing")
        );
    }

    #[test]
    fn catalog_poster_picks_formula_artwork() {
        let url = random_sports_poster_for_catalogs(&["formula_racing".to_string()]);
        assert!(url.is_some());
        assert!(url.unwrap().starts_with("http"));
    }
}
