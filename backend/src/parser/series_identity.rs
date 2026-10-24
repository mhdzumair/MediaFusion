//! Regional editions of the Bigg Boss franchise are distinct series, rather
//! than dubbed audio versions of one show. Keep their identity during parsing.
use regex::Regex;
use std::sync::OnceLock;

const EDITIONS: &[(&str, &str)] = &[
    ("Tamil", "ta"),
    ("Telugu", "te"),
    ("Hindi", "hi"),
    ("Kannada", "kn"),
    ("Malayalam", "ml"),
    ("Marathi", "mr"),
    ("Bengali", "bn"),
];

fn is_base_title(title: &str) -> bool {
    let normalized: String = title
        .chars()
        .filter(|c| c.is_alphanumeric())
        .flat_map(char::to_lowercase)
        .collect();
    matches!(normalized.as_str(), "biggboss" | "bigboss")
}

fn edition(title: &str) -> Option<(&'static str, &'static str)> {
    static EDITION: OnceLock<Regex> = OnceLock::new();
    let captures = EDITION.get_or_init(|| Regex::new(
        r"(?i)\bbigg?[ ._-]*boss[ ._-]*[\[(]?[ ._-]*(tamil|telugu|hindi|kannada|malayalam|marathi|bengali)\b"
    ).unwrap()).captures(title)?;
    EDITIONS
        .iter()
        .copied()
        .find(|(name, _)| name.eq_ignore_ascii_case(&captures[1]))
}

pub fn restore_series_edition(raw: &str, parsed: &str) -> String {
    if is_base_title(parsed)
        && let Some((name, _)) = edition(raw)
    {
        return format!("Bigg Boss {name}");
    }
    parsed.to_string()
}

pub fn qualify_series_title(title: &str, original_language: Option<&str>) -> String {
    if is_base_title(title)
        && let Some((name, _)) = EDITIONS
            .iter()
            .find(|(_, code)| Some(*code) == original_language)
    {
        return format!("Bigg Boss {name}");
    }
    title.to_string()
}

pub fn series_editions_match(query: &str, candidate: &str) -> bool {
    match edition(query) {
        Some((_, language)) => edition(candidate).is_some_and(|(_, other)| language == other),
        None => true,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn editions_are_distinct_and_do_not_relabel_other_series() {
        assert!(!series_editions_match(
            "Bigg Boss Tamil",
            "Bigg Boss Telugu"
        ));
        assert!(!series_editions_match("Bigg Boss (Tamil)", "Bigg Boss"));
        assert!(series_editions_match(
            "Bigg Boss (Tamil)",
            "Bigg Boss Tamil"
        ));
        assert_eq!(
            qualify_series_title("Bigg Boss", Some("te")),
            "Bigg Boss Telugu"
        );
        assert_eq!(qualify_series_title("Loki", Some("ta")), "Loki");
        assert_eq!(
            restore_series_edition("Loki (Tamil) S01E01", "Loki"),
            "Loki"
        );
        assert_eq!(
            restore_series_edition("Bigg Boss Tamil Jodigal", "Bigg Boss Jodigal"),
            "Bigg Boss Jodigal"
        );
    }
}
