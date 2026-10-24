//! Preserve explicit series editions separately from dubbed audio languages.
//! Country qualifiers work across franchises; language editions are scoped to
//! franchises where a language actually identifies a separate production.
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

// Reuse the project's country catalog rather than maintaining a franchise or
// country allowlist here. Only release-title aliases need explicit additions.
const COUNTRY_ALIASES: &[(&str, &[&str])] = &[
    ("US", &["USA", "United States of America"]),
    ("GB", &["UK", "Great Britain"]),
    ("AU", &["AUS"]),
    ("CA", &["CAN"]),
    ("BR", &["Brasil"]),
    ("KR", &["South Korea"]),
    ("RU", &["Russia"]),
    ("CZ", &["Czech Republic"]),
    ("VN", &["Vietnam"]),
];

fn countries() -> &'static std::collections::BTreeMap<String, String> {
    static COUNTRIES: OnceLock<std::collections::BTreeMap<String, String>> = OnceLock::new();
    COUNTRIES.get_or_init(|| {
        serde_json::from_str(include_str!("../../../resources/json/countries.json"))
            .expect("bundled country catalog must be valid")
    })
}

fn normalized(title: &str) -> String {
    title
        .chars()
        .filter(|c| c.is_alphanumeric())
        .flat_map(char::to_lowercase)
        .collect()
}

fn country_pattern() -> &'static Regex {
    static COUNTRY: OnceLock<Regex> = OnceLock::new();
    COUNTRY.get_or_init(|| {
        let mut codes = Vec::new();
        let mut names = Vec::new();
        for (code, name) in countries() {
            codes.push(regex::escape(code));
            names.push(regex::escape(name).replace(' ', "[ ._-]+"));
        }
        for (_, aliases) in COUNTRY_ALIASES {
            for alias in *aliases {
                if alias.len() <= 3 { codes.push(regex::escape(alias)); }
                else { names.push(regex::escape(alias).replace(' ', "[ ._-]+")); }
            }
        }
        names.sort_by_key(|value| std::cmp::Reverse(value.len()));
        let names = names.join("|");
        let bracketed_codes = codes.join("|");
        // These ISO codes also mean common release properties (SD quality,
        // Blu-ray/discs, TV source, closed captions, streaming services).
        // Require brackets for them.
        codes.retain(|code| !matches!(code.as_str(), "SD" | "BD" | "CD" | "TV" | "CC" | "NF" | "AM"));
        let plain_codes = codes.join("|");
        Regex::new(&format!(r"[\[(](?i:{bracketed_codes}|{names})[\])]|(?:\b|_)(?:{plain_codes}|(?i:{names}))(?:\b|_)")).unwrap()
    })
}

fn country_code(marker: &str) -> Option<&'static str> {
    static LOOKUP: OnceLock<std::collections::HashMap<String, &'static str>> = OnceLock::new();
    LOOKUP
        .get_or_init(|| {
            let mut lookup = std::collections::HashMap::new();
            for (code, name) in countries() {
                lookup.insert(normalized(code), code.as_str());
                lookup.insert(normalized(name), code.as_str());
            }
            for (code, aliases) in COUNTRY_ALIASES {
                for alias in *aliases {
                    lookup.insert(normalized(alias), *code);
                }
            }
            lookup
        })
        .get(&normalized(marker))
        .copied()
}

fn country_edition(title: &str) -> Option<(&str, &'static str)> {
    country_pattern().find_iter(title).find_map(|found| {
        if !title[found.end()..]
            .trim_matches([' ', '.', '_', '-', ')', ']'])
            .is_empty()
        {
            return None;
        }
        let base = title[..found.start()].trim_end_matches([' ', '.', '_', '-', '(', '[']);
        if base.is_empty() {
            return None;
        }
        Some((base, country_code(found.as_str())?))
    })
}

/// Normalize country aliases only for comparison; keep the displayed title intact.
pub fn comparison_title(title: &str) -> String {
    match country_edition(title) {
        Some((base, code)) => format!("{base} {code}"),
        None => title.to_string(),
    }
}

pub fn restore_series_edition(raw: &str, parsed: &str) -> String {
    restore_series_edition_with_languages(raw, parsed, &[])
}

pub fn restore_series_edition_with_languages(
    raw: &str,
    parsed: &str,
    languages: &[String],
) -> String {
    if is_base_title(parsed)
        && let Some((name, _)) = edition(raw)
    {
        return format!("Bigg Boss {name}");
    }
    // Restore only a qualifier immediately after the parsed title, before any
    // episode/release tokens. A country later in an audio/subtitle list is not identity.
    let parsed_key = normalized(parsed);
    if !parsed_key.is_empty() && country_edition(parsed).is_none() {
        for found in country_pattern().find_iter(raw) {
            let prefix = normalized(&raw[..found.start()]);
            if prefix.ends_with(&parsed_key)
                && let Some(code) = country_code(found.as_str())
            {
                let language_code = code.to_ascii_lowercase();
                if languages.iter().any(|language| {
                    language.eq_ignore_ascii_case(&language_code)
                        || crate::ptt::translate_lang(&language_code)
                            .is_some_and(|name| language.eq_ignore_ascii_case(name))
                }) {
                    continue;
                }
                return format!("{parsed} ({code})");
            }
        }
    }
    parsed.to_string()
}

/// A country is only inferred for an explicit query edition, using provider
/// origin_country evidence. English alone cannot distinguish US/UK/AU editions.
pub fn qualify_series_search_title(
    query: &str,
    title: &str,
    original_language: Option<&str>,
    origin_countries: &[&str],
) -> String {
    let title = qualify_series_title(title, original_language);
    if normalized(query) == normalized(&title) {
        return title;
    }
    if let Some((base, country)) = country_edition(query)
        && country_edition(&title).is_none()
        && normalized(base) == normalized(&title)
        && origin_countries.len() == 1
        && country_code(origin_countries[0]) == Some(country)
    {
        return format!("{title} ({country})");
    }
    title
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
    if normalized(query) == normalized(candidate) {
        return true;
    }
    match (country_edition(query), country_edition(candidate)) {
        (Some((base, country)), Some((other_base, other_country))) => {
            return country == other_country && normalized(base) == normalized(other_base);
        }
        (Some(_), None) | (None, Some(_)) => return false,
        _ => {}
    }
    match (edition(query), edition(candidate)) {
        (Some((_, language)), Some((_, other))) => language == other,
        (None, None) => true,
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn country_editions_work_across_franchises_and_country_aliases() {
        for base in [
            "Big Brother",
            "Celebrity Big Brother",
            "The Office",
            "Survivor",
            "MasterChef",
            "Married at First Sight",
        ] {
            assert!(series_editions_match(
                &format!("{base} (US)"),
                &format!("{base} USA")
            ));
            assert!(series_editions_match(
                &format!("{base} UK"),
                &format!("{base} (GB)")
            ));
            assert!(series_editions_match(
                &format!("{base} Australia"),
                &format!("{base} AU")
            ));
            assert!(!series_editions_match(
                &format!("{base} US"),
                &format!("{base} UK")
            ));
            assert!(!series_editions_match(&format!("{base} CA"), base));
            assert_eq!(
                crate::parser::similarity_ratio(&format!("{base} UK"), &format!("{base} (GB)")),
                100
            );
        }
        for (name, code) in [("Israel", "IL"), ("Sri Lanka", "LK"), ("Hungary", "HU")] {
            assert!(series_editions_match(
                &format!("Big Brother {name}"),
                &format!("Big Brother ({code})")
            ));
        }
        assert!(series_editions_match(
            "Big Brother United States of America",
            "Big Brother US"
        ));
        assert!(!series_editions_match(
            "Celebrity Big Brother US",
            "Big Brother US"
        ));
        assert!(series_editions_match("THIS IS US", "This Is Us"));
        assert_eq!(
            comparison_title("United States of Tara"),
            "United States of Tara"
        );
    }

    #[test]
    fn restore_only_edition_markers_next_to_the_title() {
        for raw in [
            "Big.Brother.(US).S01E01.1080p",
            "Big_Brother_US_S01E01",
            "Big Brother [us] S01E01",
        ] {
            assert_eq!(
                restore_series_edition(raw, "Big Brother"),
                "Big Brother (US)"
            );
        }
        for marker in ["SD", "BD", "CD", "TV", "CC"] {
            assert_eq!(
                restore_series_edition(&format!("Show {marker} S01E01"), "Show"),
                "Show"
            );
        }
        assert_eq!(
            restore_series_edition("The.Office.(UK).S01E01.1080p", "The Office"),
            "The Office (GB)"
        );
        assert_eq!(
            restore_series_edition("Loki S01E01 1080p English US Subs", "Loki"),
            "Loki"
        );
        assert_eq!(
            restore_series_edition("This Is Us S01E01", "This Is Us"),
            "This Is Us"
        );
    }

    #[test]
    fn ambiguous_candidates_need_country_evidence_and_keep_other_titles_unchanged() {
        for (origin, expected) in [
            (vec!["US"], "Big Brother (US)"),
            (vec!["GB"], "Big Brother"),
            (vec![], "Big Brother"),
            (vec!["US", "GB"], "Big Brother"),
        ] {
            let title =
                qualify_series_search_title("Big Brother US", "Big Brother", Some("en"), &origin);
            assert_eq!(title, expected);
            assert_eq!(
                series_editions_match("Big Brother US", &title),
                origin == ["US"]
            );
        }
        assert_eq!(
            qualify_series_search_title("Friends", "Friends", Some("en"), &["US"]),
            "Friends"
        );
        assert_eq!(
            qualify_series_search_title("This Is Us", "This Is Us", Some("en"), &["US"]),
            "This Is Us"
        );
    }

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
