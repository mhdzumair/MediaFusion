//! Field accessors for usenet provider config JSON blobs.
//!
//! The Configure UI stores short keys (`u`, `ak`, `wur`, …). Long-form keys are
//! also accepted for backward compatibility and direct API payloads.

use serde_json::Value;

pub const URL_KEYS: &[&str] = &["url", "base_url", "u"];
pub const API_KEY_KEYS: &[&str] = &["api_key", "apikey", "ak"];
pub const USERNAME_KEYS: &[&str] = &["username", "un", "user"];
pub const PASSWORD_KEYS: &[&str] = &["password", "pw", "pass"];
pub const WEBDAV_URL_KEYS: &[&str] = &["webdav_url", "wur"];
pub const WEBDAV_USER_KEYS: &[&str] = &["webdav_username", "username", "wus", "un"];
pub const WEBDAV_PASS_KEYS: &[&str] = &["webdav_password", "password", "wpw", "pw"];
pub const CATEGORY_KEYS: &[&str] = &["category", "cat"];

pub fn str_field<'a>(config: &'a Value, keys: &[&str]) -> Option<&'a str> {
    keys.iter()
        .find_map(|k| config.get(*k).and_then(|v| v.as_str()))
        .filter(|s| !s.is_empty())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn accepts_short_sabnzbd_keys() {
        let cfg = json!({"u": "http://sab", "ak": "secret", "wur": "http://dav"});
        assert_eq!(str_field(&cfg, URL_KEYS), Some("http://sab"));
        assert_eq!(str_field(&cfg, API_KEY_KEYS), Some("secret"));
        assert_eq!(str_field(&cfg, WEBDAV_URL_KEYS), Some("http://dav"));
    }

    #[test]
    fn accepts_long_form_keys() {
        let cfg = json!({"url": "http://sab", "api_key": "secret"});
        assert_eq!(str_field(&cfg, URL_KEYS), Some("http://sab"));
        assert_eq!(str_field(&cfg, API_KEY_KEYS), Some("secret"));
    }
}
