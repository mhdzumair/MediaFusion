use crate::config::AppConfig;
use crate::models::user_data::{IndexerConfig, IndexerInstanceConfig};

fn non_empty(value: &Option<String>) -> Option<&str> {
    value.as_deref().map(str::trim).filter(|s| !s.is_empty())
}

/// Resolve URL + API key for a Prowlarr/Jackett profile block.
///
/// Mirrors Python `scraper_tasks._create_prowlarr_task` / `_create_jackett_task`:
/// - `enabled=false` → no scrape (no global fallback)
/// - `use_global=true` → global env credentials (requires global scrape flag)
/// - `use_global=false` → user URL + user API key only (both required, no mixing)
fn resolve_instance(
    instance: &IndexerInstanceConfig,
    global_url: &Option<String>,
    global_key: &Option<String>,
    global_scrape_enabled: bool,
) -> Option<(String, String)> {
    if !instance.enabled {
        return None;
    }

    if instance.use_global {
        if !global_scrape_enabled {
            return None;
        }
        let url = non_empty(global_url)?.to_string();
        let key = non_empty(global_key)?.to_string();
        return Some((url, key));
    }

    let url = non_empty(&instance.url)?.to_string();
    let key = non_empty(&instance.api_key)?.to_string();
    Some((url, key))
}

/// Resolve Prowlarr credentials from the user profile (`ic.pr`) or global env.
pub fn resolve_prowlarr_credentials(
    ic: &IndexerConfig,
    cfg: &AppConfig,
) -> Option<(String, String)> {
    if let Some(instance) = &ic.prowlarr {
        return resolve_instance(
            instance,
            &cfg.prowlarr_url,
            &cfg.prowlarr_api_key,
            cfg.is_scrap_from_prowlarr,
        );
    }

    if !cfg.is_scrap_from_prowlarr {
        return None;
    }
    let url = non_empty(&cfg.prowlarr_url)?.to_string();
    let key = non_empty(&cfg.prowlarr_api_key)?.to_string();
    Some((url, key))
}

/// Resolve Jackett credentials from the user profile (`ic.jk`) or global env.
pub fn resolve_jackett_credentials(
    ic: &IndexerConfig,
    cfg: &AppConfig,
) -> Option<(String, String)> {
    if let Some(instance) = &ic.jackett {
        return resolve_instance(
            instance,
            &cfg.jackett_url,
            &cfg.jackett_api_key,
            cfg.is_scrap_from_jackett,
        );
    }

    if !cfg.is_scrap_from_jackett {
        return None;
    }
    let url = non_empty(&cfg.jackett_url)?.to_string();
    let key = non_empty(&cfg.jackett_api_key)?.to_string();
    Some((url, key))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn custom_instance_requires_api_key() {
        let instance = IndexerInstanceConfig {
            enabled: true,
            use_global: false,
            url: Some("http://127.0.0.1:9696".into()),
            api_key: None,
        };
        assert!(resolve_instance(
            &instance,
            &Some("http://global:9696".into()),
            &Some("global-key".into()),
            true,
        )
        .is_none());
    }

    #[test]
    fn use_global_ignores_profile_url_and_key() {
        let instance = IndexerInstanceConfig {
            enabled: true,
            use_global: true,
            url: Some("http://ignored:9696".into()),
            api_key: Some("ignored".into()),
        };
        assert_eq!(
            resolve_instance(
                &instance,
                &Some("http://prowlarr:9696".into()),
                &Some("global-key".into()),
                true,
            ),
            Some(("http://prowlarr:9696".into(), "global-key".into()))
        );
    }

    #[test]
    fn disabled_instance_does_not_fall_back_to_global() {
        let instance = IndexerInstanceConfig {
            enabled: false,
            use_global: true,
            ..IndexerInstanceConfig::default()
        };
        assert!(resolve_instance(
            &instance,
            &Some("http://prowlarr:9696".into()),
            &Some("global-key".into()),
            true,
        )
        .is_none());
    }
}
