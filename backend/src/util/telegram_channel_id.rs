//! Telegram scraping channel identifier parsing and normalization.

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ChannelRef {
    Username(String),
    DialogId(i64),
}

pub fn format_dialog_id(dialog_id: i64) -> String {
    format!("id:{dialog_id}")
}

pub fn parse_channel_ref(raw: &str) -> Option<ChannelRef> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return None;
    }

    if let Some(username) = trimmed.strip_prefix('@') {
        if username.is_empty() {
            return None;
        }
        return Some(ChannelRef::Username(username.to_string()));
    }

    if let Some(id_str) = trimmed.strip_prefix("id:") {
        let id: i64 = id_str.parse().ok()?;
        return Some(ChannelRef::DialogId(id));
    }

    // Legacy browse picker formats.
    if let Some(id_str) = trimmed.strip_prefix("channel:") {
        let id: i64 = id_str.parse().ok()?;
        return Some(ChannelRef::DialogId(id));
    }
    if let Some(id_str) = trimmed.strip_prefix("group:") {
        let id: i64 = id_str.parse().ok()?;
        return Some(ChannelRef::DialogId(id));
    }
    if let Some(id_str) = trimmed.strip_prefix("chat-") {
        let id: i64 = id_str.parse().ok()?;
        return Some(ChannelRef::DialogId(id));
    }

    if trimmed.starts_with('-') || trimmed.chars().all(|c| c.is_ascii_digit()) {
        let id: i64 = trimmed.parse().ok()?;
        return Some(ChannelRef::DialogId(id));
    }

    if !trimmed.contains('/') && !trimmed.contains(':') {
        return Some(ChannelRef::Username(trimmed.to_string()));
    }

    None
}

pub fn normalize_stored_channel_id(raw: &str) -> String {
    match parse_channel_ref(raw) {
        Some(ChannelRef::Username(username)) => format!("@{username}"),
        Some(ChannelRef::DialogId(id)) => format_dialog_id(id),
        None => String::new(),
    }
}

pub fn is_public_username(id: &str) -> bool {
    matches!(parse_channel_ref(id), Some(ChannelRef::Username(_)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalizes_username() {
        assert_eq!(normalize_stored_channel_id("@movies"), "@movies");
        assert_eq!(normalize_stored_channel_id("movies"), "@movies");
    }

    #[test]
    fn normalizes_dialog_id() {
        assert_eq!(
            normalize_stored_channel_id("id:-1001234567890"),
            "id:-1001234567890"
        );
        assert_eq!(
            normalize_stored_channel_id("-1001234567890"),
            "id:-1001234567890"
        );
        assert_eq!(
            normalize_stored_channel_id("channel:-1001234567890"),
            "id:-1001234567890"
        );
    }

    #[test]
    fn does_not_force_at_prefix_on_numeric_ids() {
        let normalized = normalize_stored_channel_id("id:-100999");
        assert!(!normalized.starts_with('@'));
    }
}
