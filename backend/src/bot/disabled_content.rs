use crate::bot::model::ContentType;

const CONFIG_TO_CONTENT: &[(&str, &[ContentType])] = &[
    ("magnet", &[ContentType::Magnet]),
    (
        "torrent",
        &[ContentType::TorrentFile, ContentType::TorrentUrl],
    ),
    ("nzb", &[ContentType::Nzb]),
    ("youtube", &[ContentType::Youtube]),
    ("http", &[ContentType::Http]),
    ("acestream", &[ContentType::Acestream]),
    ("telegram", &[ContentType::Video]),
];

pub fn is_content_type_disabled(content_type: ContentType, disabled: &[String]) -> bool {
    if disabled.is_empty() {
        return false;
    }
    for key in disabled {
        let Some((_, types)) = CONFIG_TO_CONTENT.iter().find(|(k, _)| *k == key.as_str()) else {
            continue;
        };
        if types.contains(&content_type) {
            return true;
        }
    }
    false
}

pub fn content_type_label(content_type: ContentType) -> &'static str {
    match content_type {
        ContentType::Magnet => "Magnet",
        ContentType::TorrentFile => "Torrent File",
        ContentType::TorrentUrl => "Torrent Url",
        ContentType::Nzb => "Nzb",
        ContentType::Youtube => "Youtube",
        ContentType::Http => "Http",
        ContentType::Acestream => "Acestream",
        ContentType::Video => "Video",
    }
}

const CONTENT_HELP_LINES: &[(ContentType, &str)] = &[
    (ContentType::Magnet, "Magnet links"),
    (ContentType::TorrentFile, "Torrent files / URLs"),
    (ContentType::Nzb, "NZB URLs"),
    (ContentType::Youtube, "YouTube URLs"),
    (ContentType::Http, "HTTP direct links"),
    (ContentType::Acestream, "AceStream IDs"),
    (ContentType::Video, "Video files (forward or upload)"),
];

pub fn enabled_content_lines(disabled: &[String]) -> Vec<&'static str> {
    CONTENT_HELP_LINES
        .iter()
        .filter(|(content_type, _)| !is_content_type_disabled(*content_type, disabled))
        .map(|(_, label)| *label)
        .collect()
}
